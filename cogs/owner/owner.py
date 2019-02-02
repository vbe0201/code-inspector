# -*- coding: utf-8 -*-

import asyncio
import collections
import contextlib
import io
import os
import os.path
import re
import textwrap
import time
import traceback
import typing

import discord
from discord.ext import commands

from core import commands as inspector
from utils.converters import Codeblock, CodeblockConverter, Guild
from utils.db import PostgreSQLExecutor, TableFormat
from utils.exception_handling import ReplResponseReactor
from utils.formats import pluralize
from utils.models import copy_context_with
from utils.paginator import FilePaginator, PaginatorInterface, WrappedPaginator
from utils.repl import AsyncCodeExecutor, Scope, all_inspections, get_var_dict_from_ctx
from utils.shell import ShellReader
from utils.voice import BasicYTDLSource, connected_check, playing_check, vc_check, youtube_dl

CommandTask = collections.namedtuple('CommandTask', 'index ctx task')


class Owner(metaclass=inspector.MetaCog, category='Owner'):
    __cat_line_regex = re.compile(r"(?:\./+)?(.+?)(?:#L?(\d+)(?:-L?(\d+))?)?$")

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self._scope = Scope()
        self._retain = False
        self.last_result = None
        self._tasks = collections.deque()
        self.task_count = 0

    @property
    def scope(self):
        if self._retain:
            return self._scope
        return Scope()

    @contextlib.contextmanager
    def submit(self, ctx: commands.Context):
        self.task_count += 1
        cmd_task = CommandTask(self.task_count, ctx, asyncio.Task.current_task())
        self._tasks.append(cmd_task)

        try:
            yield cmd_task
        finally:
            if cmd_task in self._tasks:
                self._tasks.remove(cmd_task)

    @staticmethod
    def get_syntax_error(e):
        if not e.text:
            return '```py\n{0.__class__.__name__}: {0}\n```'.format(e)
        return '```py\n{0.text}{1:>{0.offset}}\n{2}: {0}```'.format(e, '^', type(e).__name__)

    @staticmethod
    def format_tb(error):
        return ''.join(re.sub(r'File ".*[\\/]([^\\/]+.py)"', r'File "\1"', line)
                       for line in traceback.format_exception(type(error), error, error.__traceback__))

    async def __local_check(self, ctx: commands.Context):
        if not await ctx.bot.is_owner(ctx.author):
            raise commands.NotOwner('You must own this bot to use this command.')
        return True

    @inspector.command()
    async def cat(self, ctx: commands.Context, argument: str):
        """Read out a file, using syntax highlighting if detected.

        Lines and linespans are supported by adding `#L12` or `#L12-14` etc to the end of the filename.
        """

        match = self.__cat_line_regex.search(argument)
        if not match:  # should never happen
            return await ctx.send('Couldn\'t parse this input.')

        path = match.group(1)
        line_span = None

        if match.group(2):
            start = int(match.group(2))
            line_span = (start, int(match.group(3) or start))

        if not os.path.exists(path) or os.path.isdir(path):
            return await ctx.send(f'``{path}``: No file by that name.')

        size = os.path.getsize(path)

        if size <= 0:
            return await ctx.send(f'``{path}``: Please just fuck off. This file may be endless, empty or inaccessible.'
                                  f'I don\'t want to read that.')

        if size > 50 * (1024 ** 2):
            return await ctx.send(f'``{path}``: I won\'t read a file >50MB for you.')

        try:
            with open(path, 'rb') as f:
                paginator = FilePaginator(f, line_span=line_span, max_size=1985)
        except UnicodeDecodeError:
            return await ctx.send(f'``{path}``: Couldn\'t determine the encoding of this file.')
        except ValueError as e:
            return await ctx.send(f'``{path}``: Couldn\'t read this file, {e}')

        await PaginatorInterface(ctx.bot, paginator, owner=ctx.author).send_to(ctx)

    @inspector.command()
    async def tasks(self, ctx: commands.Context):
        """Shows the currently running tasks."""

        if not self._tasks:
            return await ctx.send('No currently running tasks.')

        paginator = commands.Paginator(max_size=1985)
        for task in self._tasks:
            paginator.add_line(f'{task.index}: `{task.ctx.command.qualified_name}`, invoked at '
                               f'{task.ctx.message.created_at.strftime("%Y-%m-%d %H:%M:%S")} UTC.')

        await PaginatorInterface(ctx.bot, paginator, owner=ctx.author).send_to(ctx)

    @inspector.command()
    async def cancel(self, ctx: commands.Context, *, index: int):
        """Cancels a task with the given index.

        If the index passed is -1, will cancel the last task instead.
        """

        if not self._tasks:
            return await ctx.send('No tasks to cancel.')

        if index == -1:
            task = self._tasks.pop()
        else:
            task = discord.utils.get(self._tasks, index=index)
            if task:
                self._tasks.remove(task)
            else:
                return await ctx.send('Unknown task.')

        task.task.cancel()
        await ctx.send(f'Cancelled task {task.index}: `{task.ctx.command.qualified_name}`, '
                       f'invoked at {task.ctx.message.created_at.strftime("%Y-%m-%d %H:%M:%S")} UTC.')

    @inspector.command()
    async def retain(self, ctx: commands.Context, *, toggle: bool):
        """Turns variable retention for REPL on or off."""

        if toggle:
            if self._retain:
                return await ctx.send('Variable retention is already set to on.')

            self._retain = True
            self._scope = Scope()
            return await ctx.send('Variable retention is on. Future REPL sessions will retain their scope.')

        if not self._retain:
            return await ctx.send('Variable retention is already set to off.')

        self._retain = False
        await ctx.send('Variable retention is off. Future REPL sessions will dispose their scope when done.')

    @inspector.command()
    async def eval(self, ctx: commands.Context, *, code: CodeblockConverter):
        """Legacy eval."""

        env = {**get_var_dict_from_ctx(ctx), **globals(), '_': self.last_result}
        to_compile = f'async def _eval():\n{textwrap.indent(code.content, "    ")}'

        async def safe_send(content: str):
            # replace token
            content = content.replace(ctx.bot.http.token, '<Token omitted>')

            if len(content) >= 1990:
                file = discord.File(io.BytesIO(content.encode('utf-8')), 'eval_result.txt')
                await ctx.send('Content too long for Discord...', file=file)
            else:
                await ctx.send(content)

        await ctx.trigger_typing()
        try:
            exec(to_compile, env)
        except SyntaxError as e:
            return await ctx.send(self.get_syntax_error(e))

        func = env['_eval']

        async with ReplResponseReactor(ctx.message):
            with self.submit(ctx):
                with io.StringIO() as stdout:
                    try:
                        with contextlib.redirect_stdout(stdout):
                            result = await func()
                    except Exception as e:
                        value = stdout.getvalue()

                        await safe_send(f'{value}{self.format_tb(e)}')
                    else:
                        value = stdout.getvalue()

                        if not result:
                            if value:
                                await safe_send(value)
                        else:
                            self.last_result = result
                            await safe_send(f'{value}{result}')

    @inspector.command(aliases=['py'])
    async def python(self, ctx: commands.Context, *, code: CodeblockConverter):
        """Direct evaluation of Python code."""

        arg_dict = get_var_dict_from_ctx(ctx)
        scope = self.scope

        scope.clean()
        arg_dict['_'] = self.last_result

        # Huge fuckery, but contextmanagers are our best shot.
        async with ReplResponseReactor(ctx.message):
            with self.submit(ctx):
                async for result in AsyncCodeExecutor(code.content, scope, arg_dict=arg_dict):
                    if result is None:
                        continue

                    self.last_result = result

                    if isinstance(result, discord.File):
                        await ctx.send(file=result)
                    elif isinstance(result, discord.Embed):
                        await ctx.send(embed=result)
                    elif isinstance(result, PaginatorInterface):
                        await result.send_to(ctx)
                    else:
                        if not isinstance(result, str):
                            # repr all non-strings
                            result = repr(result)

                        if len(result) > 2000:
                            # inconsistency here, results get wrapped in codeblocks when they are too large
                            #  but don't if they're not. probably not that bad, but noting for later review
                            paginator = WrappedPaginator(prefix='```py', suffix='```', max_size=1985)
                            paginator.add_line(result)

                            await PaginatorInterface(ctx.bot, paginator, owner=ctx.author).send_to(ctx)
                        else:
                            if result.strip() == '':
                                result = '\u200b'

                            await ctx.send(result.replace(self.bot.http.token, '<Token omitted>'))

    @inspector.command(aliases=['py_inspect', 'pyi', 'pythoninspect'])
    async def python_inspect(self, ctx: commands.Context, *, code: CodeblockConverter):
        """Evaluation of Python code with inspect information."""

        arg_dict = get_var_dict_from_ctx(ctx)
        scope = self.scope

        scope.clean()
        arg_dict['_'] = self.last_result

        # Ugly fuckery
        async with ReplResponseReactor(ctx.message):
            with self.submit(ctx):
                async for result in AsyncCodeExecutor(code.content, scope, arg_dict=arg_dict):
                    self.last_result = result

                    header = repr(result)
                    if len(header) > 485:
                        header = header[0:482] + '...'

                    paginator = WrappedPaginator(prefix=f'```prolog\n=== {header} ===\n', max_size=1985)

                    for name, res in all_inspections(result):
                        paginator.add_line(f'{name:16.16} :: {res}')

                    await PaginatorInterface(ctx.bot, paginator, owner=ctx.author).send_to(ctx)

    @inspector.command(aliases=['sh'])
    async def shell(self, ctx: commands.Context, *, script: CodeblockConverter):
        """Executes statements in the system shell.

        This uses the bash shell. Execution can be cancelled by closing the paginator.
        """

        # Fuck this
        async with ReplResponseReactor(ctx.message):
            with self.submit(ctx):
                paginator = WrappedPaginator(prefix='```sh', max_size=1985)
                paginator.add_line(f'$ {script.content}\n')

                interface = PaginatorInterface(ctx.bot, paginator, owner=ctx.author)
                self.bot.loop.create_task(interface.send_to(ctx))

                with ShellReader(script.content) as reader:
                    async for line in reader:
                        if interface.closed:
                            return

                        await interface.add_line(line)

                await interface.add_line(f'\n[Status] Return code {reader.close_code}')

    @inspector.command()
    async def sql(self, ctx: commands.Context, *, query: CodeblockConverter):
        """Executes SQL queries and displays their results in a rST table."""

        async with ReplResponseReactor(ctx.message):
            with self.submit(ctx):
                paginator = WrappedPaginator(prefix='```', max_size=1985)

                async for total, result in PostgreSQLExecutor(ctx, query.content):
                    paginator.add_line(f'# {query.content}\n')
                    if not result or len(result) <= 0:
                        paginator.add_line(f'{total:.2f}ms: {result}\n')
                    else:
                        num_rows = len(result)
                        headers = list(result[0].keys())

                        # Build a nice rST table
                        table = TableFormat()
                        table.set(headers)
                        table.add(list(res.values()) for res in result)
                        rendered = f'{table.render()}\nReturned {pluralize(row=num_rows)} in {total:.2f}ms'

                        paginator.add_line(rendered, empty=True)

                    await PaginatorInterface(ctx.bot, paginator, owner=ctx.author).send_to(ctx)

    @inspector.command()
    async def git(self, ctx: commands.Context, *, command: CodeblockConverter):
        """Shortcut for `ci!sh git`. Invokes the system shell."""

        return await ctx.invoke(self.shell, argument=Codeblock(command.language, 'git ' + command.content))

    @inspector.command(aliases=['reload'])
    async def load(self, ctx: commands.Context, *extensions):
        """Loads or reloads the given extension names.

        Reports any extension that failed to load.
        """

        paginator = commands.Paginator(prefix='', suffix='')

        for extension in extensions:
            load_icon = ('\N{CLOCKWISE RIGHTWARDS AND LEFTWARDS OPEN CIRCLE ARROWS}'
                         if extension in self.bot.extensions else '\N{INBOX TRAY}')

            try:
                self.bot.unload_extension(extension)
                self.bot.load_extension(extension)
            except Exception as e:
                traceback_data = ''.join(traceback.format_exception(type(e), e, e.__traceback__, 1))

                paginator.add_line(f'\N{WARNING SIGN} `{extension}`\n```py\n{traceback_data}```', empty=True)
            else:
                paginator.add_line(f'{load_icon} `{extension}`', empty=True)

        for page in paginator.pages:
            await ctx.send(page)

    @inspector.command()
    async def unload(self, ctx: commands.Context, *extensions):
        """Unloads the given extension names.

        Reports any extension that failed to unload.
        """

        paginator = commands.Paginator(prefix='', suffix='')

        for extension in extensions:
            try:
                self.bot.unload_extension(extension)
            except Exception as e:
                traceback_data = ''.join(traceback.format_exception(type(e), e, e.__traceback__, 1))

                paginator.add_line(f'\N{WARNING SIGN} `{extension}`\n```py\n{traceback_data}```', empty=True)
            else:
                paginator.add_line(f'\N{OUTBOX TRAY} `{extension}`', empty=True)

        for page in paginator.pages:
            await ctx.send(page)

    @inspector.group(aliases=['vc'])
    @commands.check(vc_check)
    async def voice(self, ctx: commands.Context):
        """Voice-related commands.

        If invoked without subcommand, relays current voice state.
        """

        # if using a subcommand, short out
        if ctx.invoked_subcommand is not None and ctx.invoked_subcommand is not self.voice:
            return

        # give info about the current voice client if there is one
        voice = ctx.guild.voice_client
        if not voice or not voice.is_connected():
            return await ctx.send('Not connected.')

        await ctx.send(f'Connected to {voice.channel.name}, '
                       f'{"paused" if voice.is_paused() else "playing" if voice.is_playing() else "idle"}.')

    @voice.command(name='join', aliases=['connect'])
    async def voice_join(self, ctx: commands.Context, *, destination: typing.Union[discord.VoiceChannel, discord.Member] = None):
        """Joins a voice channel, or moves to it if already connected.

        Passing a voice channel uses that voice channel.
        Passing a member will use that member's current voice channel.
        Passing nothing will use the author's voice channel.
        """

        destination = destination or ctx.author

        if isinstance(destination, discord.Member):
            if destination.voice and destination.voice.channel:
                destination = destination.voice.channel
            else:
                return await ctx.send('Member has no voice channel.')

        voice = ctx.guild.voice_client
        if voice:
            await voice.move_to(destination)
        else:
            await destination.connect(reconnect=True)

        await ctx.send(f'Connected to {destination.name}.')

    @voice.command(name='disconnect', aliases=['dc'])
    async def voice_disconnect(self, ctx: commands.Context):
        """Disconnects from the voice channel in this guild, if there is one."""

        voice = ctx.guild.voice_client
        await voice.disconnect()
        await ctx.send(f'Disconnected from {voice.channel.name}.')

    @voice.command(name='stop')
    @commands.check(playing_check)
    async def voice_stop(self, ctx: commands.Context):
        """Stops running an audio source, if there is one."""

        voice = ctx.guild.voice_client
        voice.stop()
        await ctx.send(f'Stopped playing audio in {voice.channel.name}.')

    @voice.command(name='pause')
    @commands.check(playing_check)
    async def voice_pause(self, ctx: commands.Context):
        """Pauses a running audio source, if there is one."""

        voice = ctx.guild.voice_client
        if voice.is_paused():
            return await ctx.send('Audio is already paused.')

        voice.pause()
        await ctx.send(f'Paused audio in {voice.channel.name}.')

    @voice.command(name='resume')
    @commands.check(playing_check)
    async def voice_resume(self, ctx: commands.Context):
        """Pauses a running audio source, if there is one."""

        voice = ctx.guild.voice_client
        if not voice.is_paused():
            return await ctx.send('Audio is already paused.')

        voice.resume()
        await ctx.send(f'Resumed audio in {voice.channel.name}.')

    @voice.command(name='volume')
    @commands.check(playing_check)
    async def voice_volume(self, ctx: commands.Context, *, percentage: float):
        """Adjusts the volume of an audio source if it is supported."""

        volume = max(0.0, min(1.0, percentage / 100))
        source = ctx.guild.voice_client.source

        if not isinstance(source, discord.PCMVolumeTransformer):
            return await ctx.send('This source doesn\'t support adjusting volume '
                                  'or the interface to do so is not exposed.')

        source.volume = volume
        await ctx.send(f'Volume set to {volume * 100:.2f}%')

    @voice.command(name='play')
    @commands.check(connected_check)
    async def voice_play(self, ctx: commands.Context, *, uri: str):
        """Plays audio direct from a URI.

        Can be either a local file or an audio resource from the internet.
        """

        voice = ctx.guild.voice_client
        if voice.is_playing():
            voice.stop()

        # remove embed maskers if present
        uri = uri.lstrip('<').rstrip('>')

        voice.play(discord.PCMVolumeTransformer(discord.FFmpegPCMAudio(uri)))
        await ctx.send(f'Playing in {voice.channel.name}.')

    @voice.command(name='youtube_dl', aliases=['youtubedl', 'ytdl', 'yt'])
    @commands.check(connected_check)
    async def voice_youtube_dl(self, ctx: commands.Context, *, url: str):
        """Plays audio from youtube_dl-compatible sources."""

        if not youtube_dl:
            return await ctx.send('youtube_dl is not installed.')

        voice = ctx.guild.voice_client
        if voice.is_playing():
            voice.stop()

        # remove embed maskers if present
        url = url.lstrip('<').rstrip('>')

        voice.play(discord.PCMVolumeTransformer(BasicYTDLSource(url)))
        await ctx.send(f'Playing in {voice.channel.name}.')

    @inspector.command()
    async def su(self, ctx: commands.Context, member: typing.Union[discord.Member, discord.User], *, command: str):
        """Run a command as someone else.

        This will try to resolve to a Member, but will use a User if it can't find one.
        """

        alt_ctx = await copy_context_with(ctx, author=member, content=ctx.prefix + command)
        if alt_ctx.command is None:
            return await ctx.send(f'Command "{alt_ctx.invoked_with}" is not found.')

        return await alt_ctx.command.invoke(alt_ctx)

    @inspector.command()
    async def sudo(self, ctx: commands.Context, *, command: str):
        """Runs a command bypassing all checks and cooldowns.

        This also bypasses permission checks so this has a high possibility of making a command raise.
        """

        alt_ctx = await copy_context_with(ctx, content=ctx.prefix + command)
        if alt_ctx.command is None:
            return await ctx.send(f'Command "{alt_ctx.invoked_with}" is not found.')

        return await alt_ctx.command.reinvoke(alt_ctx)

    @inspector.command()
    async def debug(self, ctx: commands.Context, *, command: str):
        """Run a command timing execution and catching exceptions."""

        alt_ctx = await copy_context_with(ctx, content=ctx.prefix + command)
        if alt_ctx.command is None:
            return await ctx.send(f'Command "{alt_ctx.invoked_with}" is not found.')

        start = time.perf_counter()
        async with ReplResponseReactor(ctx.message):
            with self.submit(ctx):
                await alt_ctx.command.invoke(alt_ctx)

        end = time.perf_counter()
        await ctx.send(f'Command `{alt_ctx.command.qualified_name}` finished in {end - start:.3f}s.')

    @inspector.command(aliases=['logout'])
    async def shutdown(self, ctx: commands.Context):
        """Logs this bot out."""

        await ctx.send('Logging out now...')
        await ctx.bot.logout()

    @inspector.command()
    async def leave(self, ctx: commands.Context, *, server: Guild):
        """Leaves a server.

        Defaults to the server the command is invoked in.
        """

        await ctx.send('My owner doesn\'t want me to stay here any longer. Bye bye.')
        await server.leave()
