#!/usr/bin/env python
# -*- coding: utf-8 -*-

import asyncio
import contextlib
import importlib
import logging
import os
import sys
import traceback
from datetime import datetime
from pathlib import Path

import click
import yaml

from core import CodeInspector
from utils import db as database

# for a faster event loop
try:
    import uvloop
except ImportError:
    pass
else:
    asyncio.set_event_loop_policy(uvloop.EventLoopPolicy())
finally:
    loop = asyncio.get_event_loop()

with open('config.yaml', 'rb') as f:
    config = yaml.safe_load(f)


@contextlib.contextmanager
def log(stream=False):
    """A contextmanager that sets up logging for our bot."""

    logging.getLogger('discord').setLevel(logging.INFO)

    log_dir = Path(os.path.dirname(__file__)).parent / 'logs'
    log_dir.mkdir(exist_ok=True)

    logger = logging.getLogger()
    logger.setLevel(logging.INFO)

    file_handler = logging.FileHandler(
        filename=f'logs/code-inspector.{datetime.now()}.log',
        encoding='utf-8',
        mode='w'
    )

    formatter = logging.Formatter('[{levelname}] ({asctime}) - {name}:{lineno} - {message}', '%Y-%m-%d %H:%M:%S', style='{')
    file_handler.setFormatter(formatter)
    logger.addHandler(file_handler)

    if stream:
        stream_handler = logging.StreamHandler()
        stream_handler.setFormatter(formatter)
        logger.addHandler(stream_handler)

    try:
        yield
    finally:
        for handler in logger.handlers[:]:
            handler.close()
            logger.removeHandler(handler)


def run_bot(stream_log: bool = False, *, ci: bool = False, **bot_config):
    bot = CodeInspector(bot_config)

    with log(stream_log):
        try:
            if ci:
                bot.loop.create_task(bot.start(bot.token))
                bot.loop.run_until_complete(bot.ci_stats())
            else:
                bot.run()
        except KeyboardInterrupt:
            pass
        finally:
            bot.loop.run_until_complete(bot.logout())
            bot.loop.close()
            os._exit(0)


async def init_db(quiet: bool = True):
    from utils.scheduler import Schedule  # we need to do this to make all_tables recognise the table

    pool = await database.create_pool(config['postgresql'])
    con = await pool.acquire()

    async with con.transaction():
        for table in database.all_tables():
            query = table.create_sql(exist_ok=True)
            if not quiet:
                logging.info('Creating table %s\nusing query %r', table.__tablename__, query)
            await pool.execute(query)

    await pool.release(con)
    del Schedule


@click.group(invoke_without_command=True, options_metavar='[options]')
@click.option('--stream-log', is_flag=True, help='Adds a stderr stream handler to the bot\'s logging component.')
@click.pass_context
def main(ctx: click.Context, stream_log: bool = False):
    """The main group of our bot. If invoked without subcommands, this runs our bot."""
    if ctx.invoked_subcommand is None:
        ci = os.getenv('CI', False)
        if ci:
            bot_config = {
                'token': os.getenv('BOT_TOKEN'),
                'prefix': os.getenv('BOT_PREFIX'),
                'description': os.getenv('BOT_DESCRIPTION'),
                'webhook_url': os.getenv('BOT_ERROR_WEBHOOK_URL'),
                'pg_credentials': {
                    'host': '127.0.0.1',
                    'port': 5432,
                    'user': 'ci',
                    'database': 'code_inspector',
                    'password': '',
                    'timeout': 60,
                },
                'owners': os.getenv('BOT_OWNERS'),
            }
        else:
            bot_config = config

        run_bot(stream_log, ci=ci, **bot_config)


@main.group(short_help='database stuff', options_metavar='[options]')
def db():
    """A command group for all database-related commands."""

    pass


@db.command(short_help='initialises the database tables for the bot', options_metavar='[options]')
@click.argument('cogs', nargs=-1, metavar='[cogs]')
@click.option('-q', '--quiet', help='less verbose output', is_flag=True, default=True)
def init(cogs: str, quiet: bool):
    """Initialises our database by creating all necessary tables.

    You can either pass a cog name or "all" as argument to initialize the
    corresponding or all tables.
    """

    if not cogs:
        click.echo('No cogs specified.')
        return
    elif cogs == 'all':
        cogs = []
        for name in os.listdir('cogs'):
            if name.startswith('__'):
                continue

            modules = CodeInspector.search_extensions(f'cogs.{name}')
            if not modules:
                continue

            for cog in modules:
                cogs.append(cog)
    else:
        cogs = [f'cogs.{e}' if not e.startswith('cogs.') else e for e in cogs]

    for ext in cogs:
        try:
            importlib.import_module(ext)
        except Exception:
            click.echo(f'Could not load {ext}.\n{traceback.format_exc()}', err=True)
            return

    loop.run_until_complete(init_db(quiet))


if __name__ == '__main__':
    sys.exit(main())
