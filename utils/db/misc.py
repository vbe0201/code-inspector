# -*- coding: utf-8 -*-

import asyncio
import json
import time

import asyncpg
from discord.ext import commands

__all__ = ['PostgreSQLExecutor', 'create_pool']


async def _set_codec(con):
    await con.set_type_codec(
        'jsonb',
        schema='pg_catalog',
        encoder=json.dumps,
        decoder=json.loads,
        format='text'
    )


async def _create_pool(*, init=None, **kwargs):
    if not init:
        async def new_init(con):
            await _set_codec(con)
    else:
        async def new_init(con):
            await _set_codec(con)
            await init(con)

    return await asyncpg.create_pool(init=new_init, **kwargs)


async def create_pool(config):
    credentials = dict(
        user=config['user'],
        password=config['password'],
        host=config['host'],
        port=config['port'],
        database=config['database'],
    )

    return await _create_pool(**credentials, command_timeout=config['timeout'])


class PostgreSQLExecutor:
    """Executes SQL queries for PostgreSQL inside of an async function or generator."""

    def __init__(self, ctx: commands.Context, query: str, *, loop: asyncio.BaseEventLoop = None):
        self.queries = []

        if query.count(';') > 1:
            # in this case we have multiple queries.
            self.queries = [f'{q.strip()};' for q in query.split(';') if q.strip() != '']
        else:
            self.queries.append(query.strip())

        self.ctx = ctx
        self.loop = loop or asyncio.get_event_loop()

    def __aiter__(self):
        return self

    def __anext__(self):
        if not self.queries or len(self.queries) == 0:
            raise StopAsyncIteration

        query = self.queries.pop(0)
        # determine the way of how we execute the query
        if query.lower().startswith('select'):
            func = self.ctx.db.fetch
        else:
            func = self.ctx.db.execute

        return self.execute(func, query)

    async def execute(self, func, query):
        """Executes SQL queries and returns their result."""

        start = time.perf_counter()
        result = await func(query)
        total = (time.perf_counter() - start) * 1000.0

        return total, result
