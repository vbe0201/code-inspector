# -*- coding: utf-8 -*-

import datetime
import inspect
import itertools
import json
import random
import re
import sys
import traceback

import discord
from discord.ext import commands
from more_itertools import last

from utils.formats import human_join

_handlers = []
ERROR_ICON_URL = 'https://twemoji.maxcdn.com/2/72x72/1f6ab.png'

# Some errors we'll ignore no matter what. These are based on idiots using the bot wrong.
_ignored_exceptions = (
    commands.NoPrivateMessage,
    commands.DisabledCommand,
    commands.CheckFailure,
    commands.CommandNotFound,
    commands.UserInputError,
    discord.Forbidden,
)


def _is_required_parameter(param):
    """Indicates whether the parameter is required or not."""

    return param.default is param.empty and param.kind is not param.VAR_POSITIONAL


def _split_params(command):
    """Splits a command's parameters into required and optional parts."""

    params = command.clean_params.values()
    required = list(itertools.takewhile(_is_required_parameter, params))

    optional = []
    for param in itertools.dropwhile(_is_required_parameter, params):
        if param.kind is param.VAR_POSITIONAL:
            args = required if getattr(command, 'require_var_positional', False) else optional
            args.append(param)
            break

        optional.append(param)
        if param.kind is param.KEYWORD_ONLY:
            break

    return required, optional


async def _send_error_webhook(ctx, error):
    """Sends an error webhook mainly for bug tracking."""

    if not isinstance(error, commands.CommandNotFound):
        ctx.bot.command_counter['failed'] += 1

    webhook = ctx.bot.webhook
    if not webhook:
        return

    error = getattr(error, 'original', error)

    if isinstance(error, _ignored_exceptions) or getattr(error, '__ignore__', False):
        return

    e = (discord.Embed(colour=0xcc3366)
         .set_author(name=f'Error in command {ctx.command}', icon_url=ERROR_ICON_URL)
         .add_field(name='Author', value=f'{ctx.author}\n(ID: {ctx.author.id})', inline=False)
         .add_field(name='Channel', value=f'{ctx.channel}\n(ID: {ctx.channel.id})')
         )

    if ctx.guild:
        e.add_field(name='Guild', value=f'{ctx.guild}\n(ID: {ctx.guild.id})')

    exc = ''.join(traceback.format_exception(type(error), error, error.__traceback__, chain=False))
    e.description = f'```py\n{exc}\n```'
    e.timestamp = datetime.datetime.utcnow()
    await webhook.send(embed=e)


def _handler(*exceptions):
    """A decorator that stores a handler and corresponding errors that should be handled."""

    def decorator(func):
        _handlers.append((func, exceptions))
        return func

    return decorator


# ----------------------------------- From here the actual error handling starts -----------------------------------

# BotMissingPermissions

_DEFAULT_MISSING_PERMS_ACTIONS = {
    'embed_links': 'embeds',
    'attach_files': 'upload stuffs',
}

with open('data/bot_missing_perms.json', encoding='utf-8') as f:
    _missing_perm_actions = json.load(f)


def _format_bot_missing_perms(ctx, missing_perms):
    action = _missing_perm_actions.get(str(ctx.command))
    if not action:
        actions = (
            _DEFAULT_MISSING_PERMS_ACTIONS.get(p, p.replace('_', ' '))
            for p in missing_perms
        )
        action = human_join(actions, final='or')

    nice_perms = (
        perm.replace('_', ' ').replace('guild', 'server').title()
        for perm in missing_perms
    )

    return (
        f"Hey hey, I don't have permissions to {action}. "
        f'Please check if I have {human_join(nice_perms)}.'
    )


@_handler(commands.BotMissingPermissions)
def bot_missing_perms(ctx, error):
    return ctx.send(_format_bot_missing_perms(ctx, error.missing_perms))


# MissingRequiredArgument
def _random_slice(seq):
    return seq[:random.randint(0, len(seq))]


def _format_missing_required_arg(ctx, param):
    required, _ = _split_params(ctx.command)
    missing = list(itertools.dropwhile(lambda p: p != param, required))
    names = human_join(f'`{p.name}`' for p in missing)

    # TODO: Specify the args more descriptively.
    return (
        f"Hey hey, you're missing {names}.\n\n"
        f'Usage: `{ctx.clean_prefix}{ctx.command.signature}`\n'
    )


@_handler(commands.MissingRequiredArgument)
def missing_required_arg(ctx, error):
    return ctx.send(_format_missing_required_arg(ctx, error.param))

# BadArgument


_reverse_quotes = {cq: oq for oq, cq in commands.view._quotes.items()}
_clean_content = commands.clean_content(fix_channel_mentions=True, escape_markdown=True)


def _get_bad_argument(ctx, param):
    content = ctx.message.content
    view = ctx.view

    if param.kind == param.KEYWORD_ONLY and not ctx.command.rest_is_raw:
        return content[view.previous:], view.previous

    # Anything past view.index can't (or shouldn't) be checked for validity, so we can safely discard it.
    content = ctx.message.content[:ctx.view.index]
    bad_quote = content[-1]
    bad_open_quote = _reverse_quotes.get(bad_quote)

    if not bad_open_quote or content[-2:-1] in ['\\', '']:
        bad_content = content.rsplit(None, 1)[-1]

        return bad_content, view.index - len(bad_content)

    # We need to look for the last "quoted" word.
    quote_pattern = rf'{bad_open_quote}((?:[^{bad_quote}\\]|\\.)*){bad_quote}'
    last_match = last(re.finditer(quote_pattern, content))
    # I swear if last_match is None...
    assert last_match, f'last_match is None with content {content}'
    return last_match[1], last_match.start()


async def _format_bad_argument(ctx, param, error):
    return (
        f'{error}: Wrong usage of param **{param}**\n\n'
        f'Usage: `{ctx.clean_prefix}{ctx.command.signature}`\n'
    )


@_handler(commands.BadArgument)
async def _bad_argument(ctx, error):
    params = list(ctx.command.params.values())
    index = min(len(ctx.args) + len(ctx.kwargs), len(params) - 1)
    param = params[index]

    error = error or error.__cause__
    if isinstance(error.__cause__, ValueError):
        cause = str(error.__cause__)
        if cause.startswith((
            'invalid literal for int()',         # int
            'could not convert string to float'  # float
        )):

            match = re.search(r": '(.*)'", cause)
            error = f'"{match[1]}" is not a number.'

    message = await _format_bad_argument(ctx, param, error)
    await ctx.send(message)


# BadUnionArgument
def _format_converter(converter):
    if converter == int:
        return 'number'
    return converter.__name__.lower()


def _format_converters(converters):
    if all(inspect.isclass(c) and issubclass(c, discord.abc.GuildChannel) for c in converters):
        return 'channel'

    return human_join(map(_format_converter, converters), final='or')


@_handler(commands.BadUnionArgument)
async def _bad_union_argument(ctx, error):
    bad_arg, _ = _get_bad_argument(ctx, error.param)
    error_message = f'"{bad_arg}" is not a {_format_converters(error.converters)}.'

    message = await _format_bad_argument(ctx, error.param, error_message)
    await ctx.send(message)


# NoPrivateMessage
@_handler(commands.NoPrivateMessage)
def bad_argument(ctx, _):
    return ctx.send('This command cannot be used in private messages.')


# NotOwner
@_handler(commands.NotOwner)
def not_owner(ctx, _):
    return ctx.send(f'Only `{ctx.bot.creator}` is permitted to use this command!')


# CommandInvokeError
@_handler(commands.CommandInvokeError)
async def command_invoke_error(ctx, error):
    print(f'In {ctx.command.qualified_name}:', file=sys.stderr)
    traceback.print_tb(error.original.__traceback__)
    print(f'{error.__class__.__name__}: {error}', file=sys.stderr)


# CommandOnCooldown


@_handler(commands.CommandOnCooldown)
def command_on_cooldown(ctx, error):
    seconds = round(error.retry_after, 2)
    hours, remainder = divmod(int(seconds), 3600)
    minutes, seconds = divmod(remainder, 60)

    return ctx.send(f'Slow down, please! This command is on cooldown.\n**{hours} hours, {minutes} minutes and {seconds} seconds remaining.**')


async def on_command_error(ctx, error):
    """The actual error handler."""

    await _send_error_webhook(ctx, error)

    # this command has a local error handler
    if hasattr(ctx.command, 'on_error'):
        return

    for handler, exceptions in _handlers:
        if isinstance(error, exceptions):
            await handler(ctx, error)
            break


def setup(bot: commands.Bot):
    """Adds our error handler listener to the bot."""

    bot.add_listener(on_command_error)
