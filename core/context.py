# -*- coding: utf-8 -*-

import contextlib
import sys

import discord
from discord.ext import commands


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
