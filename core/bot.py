# -*- coding: utf-8 -*-

import collections
import contextlib
import importlib
import inspect
import logging
import os
import pkgutil
import sys
from datetime import datetime

import aiohttp
import discord
import psutil
import yaml
from discord.ext import commands

from . import context

from utils import db
from utils.scheduler import DatabaseScheduler
from utils.transformdict import CaseInsensitiveDict
from utils.time import duration_units

logger = logging.getLogger(__name__)
command_logger = logging.getLogger('commands')

# loading our config
with open('config.yaml', 'rb') as f:
    config = {**yaml.safe_load(f)}


# permission calculations for bot invitations
def _define_permissions(*perms):
    permissions = discord.Permissions.none()
    permissions.update(**dict.fromkeys(perms, True))
    return permissions


_PERMISSIONS = [
    'send_messages',
    'embed_links',
    'add_reactions',
    'attach_files',
    'use_external_emojis',
    'create_instant_invite',
    'manage_messages',
    'read_message_history',
]

# Let's make actual permissions out of that stuff.
_PERMISSIONS = _define_permissions(*_PERMISSIONS)
del _define_permissions

_sentinel = object()


def _is_cog_hidden(cog):
    hidden = getattr(cog, '__hidden__', _sentinel)
    if hidden is not _sentinel:
        return hidden

    try:
        module_name = cog.__module__
    except AttributeError:
        return False

    while module_name:
        module = sys.modules[module_name]
        hidden = getattr(module, '__hidden__', _sentinel)
        if hidden is not _sentinel:
            return hidden

        module_name = module_name.rpartition('.')[0]

    return False


VersionInfo = collections.namedtuple('VersionInfo', 'major minor micro releaselevel serial')


class CodeInspector(commands.Bot):
    __version__ = '1.0.0'
    version_info = VersionInfo(1, 0, 0, 'final', 0)

    def __init__(self):
        super().__init__(
            command_prefix=commands.when_mentioned_or('cc!'),
            description=config['description'],
            case_insensitive=True,
            fetch_offline_members=False,
        )

        self.app_info = None
        self.creator = None

        self.cogs = CaseInsensitiveDict()
        self.owners = config['owners']

        self.command_counter = collections.Counter()
        self.session = aiohttp.ClientSession(loop=self.loop)
        self.start_time = datetime.utcnow()
        self.pool = self.loop.run_until_complete(db.create_pool(config['pg_credentials']))
        self.process = psutil.Process(os.getpid())

        self.db_scheduler = DatabaseScheduler(self.pool, timefunc=datetime.utcnow)
        self.db_scheduler.add_callback(self._dispatch_from_scheduler)

        # add our error handler
        self.load_extension('core.error_handler')

        # load any extensions from the cogs directory
        for name in os.listdir('cogs'):
            if name.startswith('__'):
                continue

            self.load_extension(f'cogs.{name}')

    def _dispatch_from_scheduler(self, entry):
        self.dispatch(entry.event, entry)

    async def is_owner(self, user):
        if self.owners:
            return user.id in self.owners

        return await super().is_owner(user)

    def run(self):
        super().run(config['token'], reconnect=True)

    async def logout(self):
        await self.session.close()
        self.db_scheduler.close()
        await super().logout()

    def add_cog(self, cog):
        super().add_cog(cog)

        if _is_cog_hidden(cog):
            for _, command in inspect.getmembers(cog, lambda m: isinstance(m, commands.Command)):
                command.hidden = True

    @staticmethod
    def search_extensions(name):
        spec = importlib.util.find_spec(name)
        if not spec:
            raise ModuleNotFoundError(f'No module called {name!r}')

        path = spec.submodule_search_locations
        if not path:
            return None

        return (name for info, name, is_pkg in pkgutil.iter_modules(path, spec.name + '.')
                if not is_pkg)

    def load_extension(self, name):
        modules = self.search_extensions(name)
        if not modules:
            return super().load_extension(name)

        for name in modules:
            try:
                super().load_extension(name)
            except discord.ClientException as e:
                if 'extension does not have a setup function' not in str(e):
                    raise

        self.extensions[name] = importlib.import_module(name)

    def unload_extension(self, name):
        super().unload_extension(name)

        for module_name in list(self.extensions):
            if name == module_name or module_name.startswith(name + '.'):
                del self.extensions[module_name]

    @contextlib.contextmanager
    def temp_listener(self, func, name=None):
        """Context manager for temporary listeners."""

        self.add_listener(func, name)
        try:
            yield
        finally:
            self.remove_listener(func)

    async def process_commands(self, message):
        ctx = await self.get_context(message, cls=context.Context)

        if ctx.command is None:
            return

        async with ctx.acquire():
            await self.invoke(ctx)

    async def on_ready(self):
        logger.info(f'\n================\nLogged in as:\n{self.user.name}\n{self.user.id}\n================\n')
        self.db_scheduler.run()

        if not hasattr(self, 'app_info'):
            self.app_info = await self.application_info()

        if not hasattr(self, 'creator'):
            self.creator = await self.get_user_info(self.app_info.owner)

        if not hasattr(self, 'start_time'):
            self.start_time = datetime.utcnow()

        await self.change_presence(
            activity=discord.Activity(name='your shitcode', type=discord.ActivityType.watching),
            status=discord.Status.dnd
        )

    async def on_message(self, message):
        if message.author.bot:
            return

        await self.process_commands(message)

    async def on_command(self, ctx):
        self.command_counter['total'] += 1
        if isinstance(ctx.channel, discord.abc.PrivateChannel):
            self.command_counter['in DMs'] += 1
        elif isinstance(ctx.channel, discord.abc.GuildChannel):
            self.command_counter[str(ctx.guild.id)] += 1

        # DM channels don't have a guild attribute
        if not ctx.guild:
            ctx.guild = 'DM channel'
            ctx.guild.id = 'no ID provided'

        fmt = ('Command executed in {0.channel} ({0.channel.id}) from {0.guild} ({0.guild.id})'
               ' by {0.author} ({0.author.id}) with content: {0.message.content}')
        command_logger.info(fmt.format(ctx))

    async def on_command_error(self, ctx, error):
        if not (
                isinstance(error, commands.CheckFailure)
                and not isinstance(error, commands.BotMissingPermissions)
                and await self.is_owner(ctx.author)
        ):
            return

        try:
            await ctx.release()
            async with ctx.acquire():
                await ctx.reinvoke()

        except Exception as e:
            await ctx.command.dispatch_error(ctx, e)

    @property
    def webhook(self):
        webhook_url = config['webhook_url']
        if webhook_url:
            return discord.Webhook.from_url(webhook_url, adapter=discord.AsyncWebhookAdapter(self.session))

        return None

    @property
    def source(self):
        return 'https://github.com/itsVale/code-inspector'

    @property
    def uptime(self):
        return duration_units((datetime.utcnow() - self.start_time).total_seconds())

    @discord.utils.cached_property
    def invite_url(self):
        return discord.utils.oauth_url(self.user.id, _PERMISSIONS)
