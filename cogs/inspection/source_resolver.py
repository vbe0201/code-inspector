# -*- coding: utf-8 -*-

import inspect
import os.path
import pathlib
import typing
from collections import defaultdict

import discord
from discord.ext import commands

Path = typing.NewType('Path', typing.Union[pathlib.Path, pathlib.PosixPath])


def resolve_line(line, d):
    for key in d.keys():
        if any(line in l for l in d[key]):
            return True

    return False


class SourceResolver:
    """A helper class that is used to read and store the discord.py source lines."""

    PATH = pathlib.Path(os.path.dirname(inspect.getfile(discord)))

    def __init__(self):
        self.source = defaultdict(list)

    def __contains__(self, item):
        return resolve_line(item, self.source)

    def __getitem__(self, item):
        return self.source[item]

    @staticmethod
    def resolve_source_object(file: Path):
        """Given a PosixPath object, this returns an object
        that is compatible to :meth:`~inspect.getsourcelines`.

        This also covers the `__init__.py` files of Python modules.
        # TODO: Find a way to resolve the contents of `__main__.py`
        """

        parts = file.parts
        name = file.name.rpartition('.')[0]

        if file.parts[-2] == 'commands':
            # we're in discord.ext.commands
            obj = (getattr(commands, name)
                   if parts[-1] != '__init__.py'
                   else commands)
        else:
            # we're in discord
            obj = (getattr(discord, name)
                   if parts[-1] != '__init__.py'
                   else commands)

        return name, obj

    @staticmethod
    def cleanup_code(code: list):
        """Cleans up a list containing source lines and returns
        a cleaned-up version with stripped lines and removed
        new line characters."""

        # We must pay attention to the following aspects:
        #
        # First of all, there are lines that may only contain
        # a new line character or some whitespaces. these must
        # be removed.
        # Further, we need to strip all unnecessary indents from
        # the source lines as well as remove new line characters.
        # And last but not least, every file contains license information
        # and an encoding declaration. These need to be removed.
        try:
            start = code.index('"""\n') + 1
            end = code[start:].index('"""\n') + 4
        except ValueError:
            start, end = 0, 0

        code = [
            line.replace('\n', '').strip() for line in code[end:]
            if line != '\n' or line.strip()
        ]
        return code

    def read_source(self):
        for file in list(self.PATH.glob('*.py')) + list(self.PATH.joinpath('ext/commands').glob('*.py')):
            try:
                name, obj = self.resolve_source_object(file)
            except (AttributeError, TypeError):
                continue

            lines = inspect.getsourcelines(obj)[0]
            obj = self.cleanup_code(lines)
            self.source[name] = obj
