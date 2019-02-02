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
import humanize
import psutil
from discord.ext import commands

from . import commands as inspector

from utils import db
from utils.scheduler import DatabaseScheduler
from utils.transformdict import CaseInsensitiveDict
from utils.time import duration_units

logger = logging.getLogger(__name__)
command_logger = logging.getLogger('commands')


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


VersionInfo = collections.namedtuple('VersionInfo', 'major minor micro releaselevel serial')


class CodeInspector(commands.AutoShardedBot):
    __version__ = '1.0.0'
    version_info = VersionInfo(1, 0, 0, 'final', 0)

    def __init__(self, config: dict):
        super().__init__(
            command_prefix=commands.when_mentioned_or(*config['prefix']),
            description=config['description'],
            case_insensitive=True,
            fetch_offline_members=False,
        )

        self.app_info = None
        self.creator = None

        self.cogs = CaseInsensitiveDict()
        self.categories = collections.defaultdict(list)
        self.owners = config['owners']

        self.command_counter = collections.Counter()
        self.session = aiohttp.ClientSession(loop=self.loop)
        self.start_time = datetime.utcnow()
        self.pool = self.loop.run_until_complete(db.create_pool(config['pg_credentials']))
        self.process = psutil.Process(os.getpid())
        self.token = config['token']
        self.webhook_url = config['webhook_url']

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
        super().run(self.token, reconnect=True)

    async def ci_stats(self):
        await self.wait_until_ready()

        async with self.pool.acquire() as db:
            with self.process.oneshot():
                memory = self.process.memory_full_info()

                print(
                    '================ CI Stats ================\n'
                    '----------- System information -----------\n'
                    f'Running on {sys.platform} with Python {sys.version}.\n'
                    f'Using {humanize.naturalsize(memory.rss)} physical memory'
                    f' and {humanize.naturalsize(memory.vms)} virtual memory,'
                    f' of which {humanize.naturalsize(memory.uss)} unique to this process.\n'
                    f'PostgreSQL version: {".".join(map(str, db.get_server_version()[:3]))}\n'
                    '------------- Bot statistics -------------\n'
                    f'Version: {self.__version__}\n'
                    f'Online for: {self.uptime}\n'
                    f'Guilds: {len(self.guilds)}\n'
                    f'Users: {len(self.users)}\n'
                    f'Voice channels: {len(self.voice_clients)}\n'
                    f'Shards: {len(self.shards)}\n'
                    f'discord.py version: {discord.__version__}\n'
                    '=========================================='
                )

    async def logout(self):
        await self.session.close()
        self.db_scheduler.close()
        await super().logout()

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

    def _add_extension(self, name):
        ext = importlib.import_module(name)
        if hasattr(ext, 'setup'):
            ext.setup(self)
            self.extensions[name] = ext
        else:
            for _, m in inspect.getmembers(ext):
                if inspect.isclass(m) and type(m) == inspector.MetaCog:
                    try:
                        m(self)
                    except Exception as e:
                        logger.error(e)

                    if name not in self.extensions:
                        self.extensions[name] = ext
                else:
                    continue

        if name not in self.extensions:
            del ext
            del sys.modules[name]
            raise discord.ClientException('extension does not have a setup function')

    def load_extension(self, name):
        modules = self.search_extensions(name)
        if not modules:
            return self._add_extension(name)

        for module in modules:
            try:
                self._add_extension(module)
            except discord.ClientException as e:
                if 'extension does not have a setup function' not in str(e):
                    raise

    def unload_extension(self, name):
        super().unload_extension(name)

        for module_name in self.extensions.keys():
            if name == module_name or module_name.startswith(name + '.'):
                self.remove_cog(module_name)
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
        ctx = await self.get_context(message, cls=inspector.Context)

        if ctx.command is None:
            return

        async with ctx.acquire():
            await self.invoke(ctx)

    async def on_ready(self):
        logger.info(f'\n================\nLogged in as:\n{self.user.name}\n{self.user.id}\n================\n')
        self.db_scheduler.run()

        if self.app_info is None:
            self.app_info = await self.application_info()

        if self.creator is None:
            self.creator = await self.get_user_info(self.app_info.owner.id)

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
        webhook_url = self.webhook_url
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
