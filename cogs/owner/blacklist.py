import itertools
import typing
from datetime import datetime

import asyncpg
import discord
from discord.ext import commands

from core import commands as inspector
from utils import db, converters
from utils.paginator import PaginatorEmbedInterface, WrappedPaginator


class Blacklist(db.Table):
    snowflake = db.Column(db.BigInt, primary_key=True)
    blacklisted_when = db.Column(db.Timestamp)
    reason = db.Column(db.Text, nullable=True)


_blocked_icon = 'https://twemoji.maxcdn.com/2/72x72/26d4.png'
_unblocked_icon = 'https://twemoji.maxcdn.com/2/72x72/2705.png'


class Blacklisted(commands.CheckFailure):
    def __init__(self, message, reason, *args):
        self.message = message
        self.reason = reason
        super().__init__(message, *args)

    def to_embed(self):
        embed = (discord.Embed(description=self.reason, colour=0xFF0000)
                 .set_author(name=self.message, icon_url=_blocked_icon))
        return embed


_GuildOrUser = typing.Union[converters.Guild, discord.User]


class Blacklists(metaclass=inspector.MetaCog, category='Owner'):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    async def __local_check(self, ctx: inspector.Context):
        if not await ctx.bot.is_owner(ctx.author):
            raise commands.NotOwner('You must own this bot to use this command.')
        return True

    async def __global_check_once(self, ctx: inspector.Context):
        row = await self.get_blacklist(ctx.author.id, con=ctx.db)
        if row:
            raise Blacklisted('You have been blacklisted by my owner.', row['reason'])

        if not ctx.guild:
            return True

        row = await self.get_blacklist(ctx.guild.id, con=ctx.db)
        if row:
            # the creator of the bot should be able to use it even on blocked guilds
            if ctx.author != ctx.bot.creator:
                raise Blacklisted('This server has been blacklisted by my owner.', row['reason'])

        return True

    async def on_command_error(self, ctx: inspector.Context, error: typing.Union[Blacklisted, typing.Any]):
        if isinstance(error, Blacklisted):
            await ctx.send(embed=error.to_embed())

    async def get_blacklist(self, snowflake, *, con):
        query = 'SELECT reason FROM blacklist WHERE snowflake = $1;'
        return await con.fetchrow(query, snowflake)

    async def _blacklist_embed(self, ctx, action, colour, icon, thing, reason, time):
        type_name = 'Server' if isinstance(thing, discord.Guild) else 'User'
        if reason:
            if len(reason) > 1024:
                reason = reason[0:1021] + '...'
        else:
            reason = 'None'

        embed = (discord.Embed(timestamp=time, color=colour)
                 .set_author(name=f'{type_name} {action}', icon_url=icon)
                 .add_field(name='Name:', value=thing)
                 .add_field(name='ID:', value=thing.id)
                 .add_field(name='Reason:', value=reason, inline=False))
        await ctx.send(embed=embed)

    @inspector.command(aliases=['bl', 'block'])
    async def blacklist(self, ctx: inspector.Context, server_or_user: _GuildOrUser, *, reason: str = ''):
        """Blacklists either a server or a user from using the bot."""

        if await self.bot.is_owner(server_or_user):
            return await ctx.send('You can\'t blacklist my owner, you shitter.')

        time = datetime.utcnow()
        query = 'INSERT INTO blacklist VALUES ($1, $2, $3);'

        try:
            await ctx.db.execute(query, server_or_user.id, time, reason)
        except asyncpg.UniqueViolationError:
            return await ctx.send(f'{server_or_user} has already been blacklisted.')
        else:
            await self._blacklist_embed(ctx, 'blacklisted', 0xff0000, _blocked_icon, server_or_user, reason, time)

    @inspector.command(aliases=['ubl', 'unblock'])
    async def unblacklist(self, ctx: inspector.Context, server_or_user: _GuildOrUser, *, reason: str = ''):
        """Unblacklists either a server or a user."""

        if await self.bot.is_owner(server_or_user):
            return await ctx.send(f'You can\'t even block my owner, so you can\'t unblock him.')

        query = 'DELETE FROM blacklist WHERE snowflake = $1;'
        result = await ctx.db.execute(query, server_or_user.id)

        if result[-1] == '0':
            return await ctx.send(f'{server_or_user} isn\'t blacklisted.')

        await self._blacklist_embed(ctx, 'unblacklisted', 0x00FF00, _unblocked_icon, server_or_user, reason, datetime.utcnow())

    @inspector.command()
    async def blacklisted(self, ctx: inspector.Context):
        """Lists all blacklisted users and guilds."""

        def _get_user_or_guild(index, snowflake):
            guild = ctx.bot.get_guild(snowflake)
            if guild is not None:
                return f'`{index}.` {guild} (ID: {guild.id})'

            user = ctx.bot.get_user(snowflake)
            if user is not None:
                return f'`{index}.` {user} (ID: {user.id})'

            return f'`{index}.` Unknown guild/user'

        snowflakes = [row['snowflake'] for row in (await ctx.db.fetch('SELECT snowflake FROM blacklist;'))]
        entries = (
            itertools.starmap(_get_user_or_guild, enumerate(snowflakes)) if snowflakes else
            ('Currently no blacklisted users or guilds.',)
        )

        paginator = WrappedPaginator(prefix='', suffix='')
        for entry in entries:
            paginator.add_line(entry)

        return await PaginatorEmbedInterface(ctx.bot, paginator, owner=ctx.author).send_to(ctx)
