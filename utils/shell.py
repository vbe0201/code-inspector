# -*- coding: utf-8 -*-

import asyncio
import os
import re
import shlex
import subprocess
import sys
import time

SHELL = os.getenv('SHELL') or '/bin/bash'
WINDOWS = sys.platform == 'win32'


def background_reader(stream, loop: asyncio.AbstractEventLoop, callback):
    """Reads a stream and forwards each line to an async callback."""

    for line in iter(stream.readline, b''):
        loop.call_soon_threadsafe(loop.create_task, callback(line))


class ShellReader:
    """A class that passively reads from a shell and buffers results for read."""

    def __init__(self, code: str, timeout: int = 90, loop: asyncio.AbstractEventLoop = None):
        if WINDOWS:
            sequence = shlex.split(code)
        else:
            sequence = [SHELL, '-c', code]

        self.process = subprocess.Popen(sequence, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        self.close_code = None

        self.loop = loop or asyncio.get_event_loop()
        self.timeout = timeout

        self.stdout_task = self.make_reader_task(self.process.stdout, self.stdout_handler)
        self.stderr_task = self.make_reader_task(self.process.stderr, self.stderr_handler)

        self.queue = asyncio.Queue(250)

    @property
    def closed(self):
        return self.stdout_task.done() and self.stderr_task.done()

    async def executor_wrapper(self, *args, **kwargs):
        return await self.loop.run_in_executor(None, *args, **kwargs)

    def make_reader_task(self, stream, callback):
        return self.loop.create_task(self.executor_wrapper(background_reader, stream, self.loop, callback))

    @staticmethod
    def clean_bytes(line):
        text = line.decode('utf-8').replace('\r', '').strip('\n')
        return re.sub(r'\x1b[^m]*m]', '', text).replace('``', '`\u200b`').strip('\n')

    async def stdout_handler(self, line):
        await self.queue.put(self.clean_bytes(line))

    async def stderr_handler(self, line):
        await self.queue.put(self.clean_bytes(b'[stderr] ' + line))

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.process.kill()
        self.process.terminate()
        self.close_code = self.process.wait(timeout=0.5)

    def __aiter__(self):
        return self

    async def __anext__(self):
        start_time = time.perf_counter()

        while not self.closed or not self.queue.empty():
            try:
                return await asyncio.wait_for(self.queue.get(), timeout=1)
            except asyncio.TimeoutError as error:
                if time.perf_counter() - start_time >= self.timeout:
                    raise error

        raise StopAsyncIteration()
