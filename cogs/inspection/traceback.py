# -*- coding: utf-8 -*-

import asyncio
import importlib
import re

import discord
from discord.ext import commands

from . import get_source_revision
from .source_resolver import SourceResolver

from utils import db
from utils.converters import CodeblockConverter, MessageConverter

_EXCLAMATION_ICON = 'https://twemoji.maxcdn.com/2/72x72/2757.png'


class SourceNotes(db.Table, table_name='source_notes'):
    line = db.Column(db.Text)
    error = db.Column(db.Text)
    reason = db.Column(db.Text)
    solution = db.Column(db.Text)

    notes_index = db.Index(line, error)
    __create_extra__ = ['PRIMARY KEY(line, error)']


class TracebackInspection:
    _TRACEBACK_REGEX = re.compile(r"(?:Traceback.*)*[\n\s]+(?:File\s\"(.*)\",\sline\s(\d+)(?:,\sin\s.*)?)[\n\s]+(.*)")

    def __init__(self, bot):
        self.bot = bot
        self.source_resolver = SourceResolver()
        self.source_revision = None

        self.source_task = self.bot.loop.create_task(self._update_source())

    def __unload(self):
        self.source_task.cancel()

    async def _update_source(self):
        while not self.bot.is_closed():
            try:
                revision = await get_source_revision()
            except Exception:
                await asyncio.sleep(600)
                continue

            if self.source_revision == revision:
                await asyncio.sleep(3600)
                continue

            self.source_revision = revision
            importlib.reload(discord)

            self.source_resolver.read_source()

    @commands.command()
    async def inspect(self, ctx: commands.Context, *, message: MessageConverter):
        """Inspects a Python traceback to retrieve error information.

        <param message>
            Some kind of source to retrieve the traceback from.
            You can specify either a Discord message link, an ID
            of a message in the current channel or the plain traceback
            content.
        </param>

        <example>cc!inspect https://discordapp.com/channels/1234567890/1234567890/1234567890</example>
        <example>cc!inspect 1234567890</example>
        <example>
        cc!inspect
        ```
        <Traceback here>
        ```
        </example>
        """

        matches = list(self._TRACEBACK_REGEX.finditer(message))
        if not matches:
            return await ctx.send('I couldn\'t find any traceback to inspect. :eyes:')

        path = matches[-1].group(1).strip()
        file = re.search(r"[\\/]([^\\/]*)\.py", path).group(1).strip()
        index = int(matches[-1].group(2).strip())
        line = matches[-1].group(3).strip()

        error = re.search(r"(.*Error):", message, re.MULTILINE)
        error = error.group(1) if error else None

        fmt = ('```yaml\nIn file: "{file}, line {index}"\n  - {line}\n\n'
               '# What causes this error?\n{reason}\n\n# Possible solution:\n{solution}```')

        if error is None:
            reason = 'Unknown.'
            solution = 'No solutions to the issue found.'
        else:
            query = 'SELECT reason, solution FROM source_notes WHERE line = $1 AND error = $2;'
            row = await ctx.db.fetchrow(query, line, error)
            if not row:
                reason = 'Unknown.'
                solution = 'No solutions to the issue found.'
            else:
                reason, solution = row['reason'], row['solution']

        await ctx.send(
            fmt.format(file=file, index=index, line=line, reason=reason, solution=solution).replace('\\n', '\n').replace('``', '`\u200b`')
        )

    async def on_message(self, message: discord.Message):
        """Inspects sent messages to give a warning about tracebacks
        that are caused by the discord.py async branch as it is deprecated."""

        codeblock = await CodeblockConverter().convert(message, message.content)
        matches = list(self._TRACEBACK_REGEX.finditer(codeblock.content))
        if not matches:
            return

        # the source line that caused the issue
        line = matches[-1].group(3).strip()

        is_async = line not in self.source_resolver
        if is_async:
            embed = (discord.Embed(description='Please consider updating your installation to the rewrite branch (v1.0.0).', color=0xe74c3c)
                     .set_author(name='Uh oh. Seems like you\'re using a deprecated version of discord.py!', icon_url=_EXCLAMATION_ICON)
                     .add_field(name='Installation:', value='First, get [git](https://git-scm.com). Then you can use the command\n`'
                                                            'python3.7 -m pip install git+https://github.com/Rapptz/discord.py@rewrite`.')
                     .add_field(name='Documentation:', value='[Click here](http://discordpy.readthedocs.io/en/rewrite)')
                     .add_field(name='Examples:', value='[Official examples](https://github.com/Rapptz/discord.py/tree/rewrite/examples)\n'
                                                        '[Community examples](https://gist.github.com/EvieePy/d78c061a4798ae81be9825468fe146be)'))

            await message.channel.send(embed=embed)


def setup(bot: commands.Bot):
    """Adds the traceback cog to the bot."""

    bot.add_cog(TracebackInspection(bot))
