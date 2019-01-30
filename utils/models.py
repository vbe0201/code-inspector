# -*- coding: utf-8 -*-

import copy

from discord.ext import commands


async def copy_context_with(ctx: commands.Context, *, author=None, **kwargs):
    """Returns a new Context object with modified message properties."""

    # copy context and update attributes
    alt_message = copy.copy(ctx.message)
    alt_message._update(alt_message.channel, kwargs)

    if author is not None:
        alt_message.author = author

    # obtain and return a new context of the same type
    return await ctx.bot.get_context(alt_message, cls=type(ctx))
