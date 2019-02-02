# -*- coding: utf-8 -*-

import colorsys
import contextlib
import inspect
import logging
import random
import sys
from functools import partial
from collections import OrderedDict

import discord
from discord.ext import commands

logger = logging.getLogger(__name__)

__all__ = ['Context', 'MetaCog', 'Command', 'Group', 'command', 'group']


class MetaCog(type):
    @classmethod
    def __prepare__(mcs, name, bases, **kwargs):
        return OrderedDict()

    def __new__(mcs, *args, **kwargs):
        self = super().__new__(mcs, *args)

        self.color = discord.Colour.from_rgb(*(int(x * 255) for x in colorsys.hsv_to_rgb(random.random(), 1, 1)))
        self.category = kwargs.pop('category', None)
        self.secret = kwargs.pop('secret', False)

        for key, value in kwargs.items():
            setattr(self, key, value)

        self.commands = []
        self.events = []

        return self

    def __call__(cls, *args, **kwargs):
        self = super().__call__(*args, **kwargs)

        if not isinstance(args[0], commands.bot.BotBase):
            raise TypeError('The first parameter of a cog is supposed to be an instance of commands.Bot.')

        bot = args[0]
        bot.cogs[type(self).__name__] = self

        bot.categories[self.category].append(self)

        try:
            check = getattr(self, f'_{self.__class__.__name__}__global_check')
        except AttributeError:
            pass
        else:
            bot.add_check(check)

        try:
            check = getattr(self, f'_{self.__class__.__name__}__global_check_once')
        except AttributeError:
            pass
        else:
            bot.add_check(check, call_once=True)

        for name, member in inspect.getmembers(self):
            if isinstance(member, commands.Command):
                if member.parent:
                    continue

                if self.secret:
                    member.hidden = True

                bot.add_command(member)
                self.commands.append(member)

            elif name.startswith('on_'):
                bot.add_listener(member, name)
                self.events.append(name)

        return self


class _ContextAcquire:
    __slots__ = ('ctx', 'timeout')

    def __init__(self, ctx, timeout):
        self.ctx = ctx
        self.timeout = timeout

    def __await__(self):
        return self.ctx._acquire(timeout=self.timeout).__await__()

    async def __aenter__(self):
        return await self.ctx._acquire(timeout=self.timeout)

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        return await self.ctx._release(exc_type, exc_val, exc_tb)


class Context(commands.Context):
    """Represents a custom command context with extended functionality."""

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.db = None

    @property
    def pool(self):
        """The database connection pool of the bot."""

        return self.bot.pool

    @property
    def clean_prefix(self):
        """The cleaned up invoke prefix."""

        return self.prefix.replace(self.bot.user.mention, f'@{self.bot.user.name}')

    async def _acquire(self, *, timeout=None):
        if not self.db:
            self.db = await self.pool.acquire(timeout=timeout)

        return self.db

    def acquire(self, *, timeout=None):
        """Acquires the database connection from the connection pool."""

        return _ContextAcquire(self, timeout)

    async def _release(self, *args):
        """Internal method used for properly propagating the exceptions in the session's __aexit__.

        This method is called automatically by the bot, NOT Context.release!
        """

        if self.db:
            await self.pool.release(self.db)
            self.db = None

    async def release(self):
        """Closes the current database session.

        Useful if needed for "long" interactive commands where
        we want to release the connection and later re-acquire it.
        """

        return await self._release(*sys.exc_info())

    async def confirm(self, message, *, timeout=60.0, delete_after=True, reacquire=True, author_id=None, destination=None):
        """Prompts the user with either yes or no."""

        destination = destination or self.channel
        with contextlib.suppress(AttributeError):
            if not destination.permissions_for(self.me).add_reactions:
                raise RuntimeError('Bot is missing Add Reactions permisson.')

        confirm_emoji, deny_emoji = emojis = ['\N{WHITE HEAVY CHECK MARK}', '\N{CROSS MARK}']
        is_valid_emoji = frozenset(map(str, emojis)).__contains__
        instructions = f'{confirm_emoji} \N{EM DASH} Yes\n{deny_emoji} \N{EM DASH} No'

        if isinstance(message, discord.Embed):
            message.add_field(name='Choices', value=instructions, inline=False)
            msg = await destination.send(embed=message)
        else:
            message = f'{message}\n\n{instructions}'
            msg = await destination.send(message)

        author_id = author_id or self.author.id
        check = lambda data: data.message_id == msg.id and data.user_id == author_id and is_valid_emoji(str(data.emoji))
        for emoji in emojis:
            await msg.add_reaction(emoji)
        if reacquire:
            await self.release()
        try:
            data = await self.bot.wait_for('raw_reaction_add', check=check, timeout=timeout)
            return str(data.emoji) == str(confirm_emoji)
        finally:
            if reacquire:
                await self.acquire()
            if delete_after:
                await msg.delete()

    def bot_has_permissions(self, **perms):
        permissions = self.channel.permissions_for(self.me)
        return all(getattr(permissions, permission) == value for permission, value in perms.items())


class Command(commands.Command):
    def __init__(self, name, callback, **kwargs):
        super().__init__(name, callback, **kwargs)

    async def can_run(self, ctx: commands.Context):
        original = ctx.command
        ctx.command = self

        try:
            if not (await ctx.bot.can_run(ctx)):
                raise commands.CheckFailure(f'The global check functions for {self.qualified_name} failed.')

            cog = self.instance
            if cog is not None:
                try:
                    local_check = getattr(cog, f'_{cog.__class__.__name__}__local_check')
                except AttributeError:
                    pass
                else:
                    ret = await discord.utils.maybe_coroutine(local_check, ctx)
                    if not ret:
                        return False

            if ctx.author.id in ctx.bot.owners:
                return True

            predicates = self.checks
            if not predicates:
                return True

            return await discord.utils.async_all(predicate(ctx) for predicate in predicates)
        finally:
            ctx.command = original


class Group(commands.GroupMixin, Command):
    def __init__(self, **attrs):
        self.invoke_without_command = attrs.pop('invoke_without_command', True)
        super().__init__(**attrs)

    def command(self, *args, **kwargs):
        def decorator(func):
            result = commands.command(cls=Command, *args, **kwargs)(func)
            try:
                self.add_command(result)
            except Exception as e:
                logger.error(e)

            return result
        return decorator

    def group(self, *args, **kwargs):
        def decorator(func):
            result = commands.group(*args, **kwargs)(func)
            self.add_command(result)

            return result
        return decorator

    async def invoke(self, ctx: commands.Context):
        early_invoke = not self.invoke_without_command
        if early_invoke:
            await self.prepare(ctx)

        view = ctx.view
        previous = view.index
        view.skip_ws()
        trigger = view.get_word()

        if trigger:
            ctx.subcommand_passed = trigger
            ctx.invoked_subcommand = self.all_commands.get(trigger)

        if early_invoke:
            injected = commands.core.hooked_wrapped_callback(self, ctx, self.callback)
            await injected(*ctx.args, **ctx.kwargs)

        if trigger and ctx.invoked_subcommand:
            ctx.invoked_with = trigger
            await ctx.invoked_subcommand.invoke(ctx)
        elif not early_invoke:
            view.index = previous
            view.previous = previous
            await super().invoke(ctx)

    async def reinvoke(self, ctx: commands.Context, *, call_hooks=False):
        early_invoke = not self.invoke_without_command
        if early_invoke:
            ctx.command = self
            await self._parse_arguments(ctx)

            if call_hooks:
                await self.call_before_hooks(ctx)

        view = ctx.view
        previous = view.index
        view.skip_ws()
        trigger = view.get_word()

        if trigger:
            ctx.subcommand_passed = trigger
            ctx.invoked_subcommand = self.all_commands.get(trigger)

        if early_invoke:
            try:
                await self.callback(*ctx.args, **ctx.kwargs)
            except Exception:
                ctx.command_failed = True
                raise
            finally:
                if call_hooks:
                    await self.call_after_hooks(ctx)

        if trigger and ctx.invoked_subcommand:
            ctx.invoked_with = trigger
            await ctx.invoked_subcommand.reinvoke(ctx, call_hooks=call_hooks)
        elif not early_invoke:
            view.index = previous
            view.previous = previous
            await super().reinvoke(ctx, call_hooks=call_hooks)


command = partial(commands.command, cls=Command)
group = partial(commands.command, cls=Group)
