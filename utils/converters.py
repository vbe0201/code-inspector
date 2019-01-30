# -*- coding: utf-8 -*-

import collections
import re

from discord.ext import commands

__all__ = ['CodeblockConverter']

Codeblock = collections.namedtuple('Codeblock', 'language content')
CODEBLOCK_REGEX = re.compile("^(?:```([A-Za-z0-9\\-.]*)\n)?(.+?)(?:```)?$", re.S)


class CodeblockConverter(commands.Converter):
    """
    A converter that strips codeblock markdown if it exists.

    Returns a namedtuple of (language, cotent).
    The language field may be None.
    """

    async def convert(self, ctx, argument):
        match = CODEBLOCK_REGEX.search(argument)
        if not match:
            return Codeblock(None, argument.strip('` \n'))

        return Codeblock(match.group(1), match.group(2))


class Guild(commands.Converter):
    """
    A converter that matches guilds.
    """

    async def convert(self, ctx, argument):
        guild = self._get_guild(ctx, argument)
        if guild is None:
            raise commands.BadArgument('Could not find any matching guild.')
        else:
            return guild

    @staticmethod
    def _get_guild(ctx, argument):
        if isinstance(argument, int):
            guild = ctx.bot.get_guild(argument)
        elif isinstance(argument, str):
            guilds = list(filter(lambda g: g.name == argument, ctx._state._guilds.values()))
            if len(guilds) > 1:
                raise commands.BadArgument('Found multiple guilds with the same name.')
            elif len(guilds) == 1:
                guild = guilds[0]
            else:
                guild = None
        else:
            raise commands.BadArgument(f'Cannot determine guilds given this argument: {argument}')

        return guild


class MessageConverter(commands.Converter):
    async def convert(self, ctx, argument):
        if isinstance(argument, str):
            match = re.match(r"^(?:https?://)?(:?canary|ptb.)?discordapp.com/channels/([0-9]+)/([0-9]+)/([0-9+]+)$", argument, re.IGNORECASE)
            if match:
                message = await ctx.bot.get_channel(int(match.group(2))).get_message(int(match.group(3)))
            else:
                message = (await CodeblockConverter().convert(ctx, argument))
        elif isinstance(argument, int):
            message = await ctx.channel.get_message(argument)
        else:
            message = None

        if message is None:
            raise commands.BadArgument('Couldn\'t find any matching message.')

        return message.content
