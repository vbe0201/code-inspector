# -*- coding: utf-8 -*-

import collections
import re

import discord
from discord.ext import commands

__all__ = ['Codeblock', 'CodeblockConverter', 'Guild', 'MessageConverter']

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


class Guild(commands.IDConverter):
    """
    A converter that matches guilds.
    """

    async def convert(self, ctx, argument):
        id_ = self._get_id_match(argument)
        if id_:
            result = ctx.bot.get_guild(int(id_.group(1)))
        else:
            result = discord.utils.find(lambda g: g.name == argument, ctx.bot.guilds)

        if not result:
            raise commands.BadArgument('Couldn\'t find a guild that matches {}'.format(argument))

        return result


class MessageConverter(commands.IDConverter):
    async def convert(self, ctx, argument):
        id_ = self._get_id_match(argument)
        if id_:
            result = await ctx.channel.get_message(int(id_.group(1)))
        else:
            match = re.search(r"(?:https?://)?(?:canary|ptb.)?discordapp.com/channels/([0-9]+)/([0-9]+)/([0-9+]+)", argument, re.IGNORECASE)
            if match:
                result = await ctx.bot.get_channel(int(match.group(2))).get_message(int(match.group(3)))
            else:
                result = None

        if not result:
            raise commands.BadArgument('Couldn\'t find a message that matches {}'.format(argument))

        return (await CodeblockConverter().convert(ctx, result.content)).content
