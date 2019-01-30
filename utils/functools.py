# -*- coding: utf-8 -*-

import asyncio
import functools
import typing


async def run_in_executor(func: typing.Callable):
    """A decorator that wraps a sync function to be executed in an executor, changing it to an async function."""

    @functools.wraps(func)
    async def decorator(*args, **kwargs):
        loop = asyncio.get_event_loop()
        internal_function = functools.partial(func, *args, **kwargs)
        return await loop.run_in_executor(None, internal_function)

    return decorator
