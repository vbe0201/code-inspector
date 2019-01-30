# -*- coding: utf-8 -*-

from discord.ext import commands

from .compilation import *
from .inspections import all_inspections
from .scope import *


def get_var_dict_from_ctx(ctx: commands.Context):
    """Returns the dict to be used in REPL for a given context."""

    return {
        'author': ctx.author,
        'bot': ctx.bot,
        'channel': ctx.channel,
        'ctx': ctx,
        'guild': ctx.guild,
        'message': ctx.message,
        'msg': ctx.message
    }
