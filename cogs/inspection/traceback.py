# -*- coding: utf-8 -*-

import asyncio
import importlib
import re

import discord

from . import get_source_revision
from .source_resolver import SourceResolver

from core import commands as inspector
from utils.converters import CodeblockConverter

_EXCLAMATION_ICON = 'https://twemoji.maxcdn.com/2/72x72/2757.png'


class TracebackInspection(metaclass=inspector.MetaCog, category='Inspection'):
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

    async def on_message(self, message: discord.Message):
        """Inspects sent messages to give a warning about tracebacks
        that are caused by the discord.py async branch as it is deprecated."""

        codeblock = await CodeblockConverter().convert(message, message.content)
        matches = list(self._TRACEBACK_REGEX.finditer(codeblock.content))
        if not matches:
            return

        path = matches[-1].group(1).strip()
        line = matches[-1].group(3).strip()

        match = re.search(r"[\\/]([^\\/]*)[\\/]([^\\/]*)\.py", path)
        if not match:
            return

        is_discord_py_issue = match.group(1).strip() in ('discord', 'commands')
        if not is_discord_py_issue:
            return

        # when the line isn't part of the rewrite branch code lines, it must (obviously) be async
        is_async = line not in self.source_resolver[match.group(2).strip()]
        if is_async:
            embed = (discord.Embed(description='Please consider updating your installation to the rewrite branch (v1.0.0).', color=0xe74c3c)
                     .set_author(name='Uh oh. Seems like you\'re using a deprecated version of discord.py!', icon_url=_EXCLAMATION_ICON)
                     .add_field(name='Installation:', value='First, get [git](https://git-scm.com). Then you can use the command\n`'
                                                            'python3.7 -m pip install git+https://github.com/Rapptz/discord.py@rewrite`.')
                     .add_field(name='Documentation:', value='[Click here](http://discordpy.readthedocs.io/en/rewrite)')
                     .add_field(name='Examples:', value='[Official examples](https://github.com/Rapptz/discord.py/tree/rewrite/examples)\n'
                                                        '[Community examples](https://gist.github.com/EvieePy/d78c061a4798ae81be9825468fe146be)'))

            await message.channel.send(embed=embed)
