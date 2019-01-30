# -*- coding: utf-8 -*-

import asyncio
import contextlib
import subprocess
import traceback
import typing

import discord
from discord.ext import commands


async def send_traceback(destination: discord.abc.Messageable, verbosity: int, *exc_info):
    traceback_content = ''.join(traceback.format_exception(*exc_info, verbosity)).replace('``', '`\u200b`')

    paginator = commands.Paginator(prefix='```py')
    for line in traceback_content.split('\n'):
        paginator.add_line(line)

    message = None
    for page in paginator.pages:
        message = await destination.send(page)

    return message


async def do_after(delay: float, coro, *args, **kwargs):
    await asyncio.sleep(delay)
    return await coro(*args, **kwargs)


async def attempt_to_react(message: discord.Message, reaction: typing.Union[str, discord.Emoji]):
    with contextlib.suppress(discord.HTTPException):
        return await message.add_reaction(reaction)


class ReactionProcedureTimer:
    """Reacts to a message based on what happens during its lifetime."""

    __slots__ = ('message', 'loop', 'handle', 'raised')

    def __init__(self, message: discord.Message, loop: typing.Optional[asyncio.BaseEventLoop] = None):
        self.message = message
        self.loop = loop or asyncio.get_event_loop()
        self.handle = None
        self.raised = False

    async def __aenter__(self):
        self.handle = self.loop.create_task(do_after(1, attempt_to_react, self.message, '\N{BLACK RIGHT-POINTING TRIANGLE}'))
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        if self.handle:
            self.handle.cancel()

        # no exception, check mark
        if not exc_val:
            await attempt_to_react(self.message, '\N{WHITE HEAVY CHECK MARK}')
            return

        self.raised = True

        if isinstance(exc_val, (asyncio.TimeoutError, subprocess.TimeoutExpired)):
            # timed out, alarm clock
            await attempt_to_react(self.message, '\N{ALARM CLOCK}')
        elif isinstance(exc_val, SyntaxError):
            # syntax error, single exclamation mark
            await attempt_to_react(self.message, '\N{HEAVY EXCLAMATION MARK SYMBOL}')
        else:
            # other error, double exclamation mark
            await attempt_to_react(self.message, '\N{DOUBLE EXCLAMATION MARK}')


class ReplResponseReactor(ReactionProcedureTimer):
    async def __aexit__(self, exc_type, exc_val, exc_tb):
        await super().__aexit__(exc_type, exc_val, exc_tb)

        # nothing went wrong
        if not exc_val:
            return

        if isinstance(exc_val, (SyntaxError, asyncio.TimeoutError, subprocess.TimeoutExpired)):
            # short traceback, send to channel
            await send_traceback(self.message.channel, 0, exc_type, exc_val, exc_tb)
        else:
            # this traceback likely needs more info, so increase verbosity and DM it instead
            await send_traceback(self.message.author, 8, exc_type, exc_val, exc_tb)

        return True  # the exception has been handled
