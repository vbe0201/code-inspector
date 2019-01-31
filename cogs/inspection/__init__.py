# -*- coding: utf-8 -*-

"""
Some utilites for code inspection.
"""

import os


async def get_source_revision():
    """Retrieves the source version for the discord.py rewrite branch."""

    cmd = r'git ls-remote https://github.com/Rapptz/discord.py --tags rewrite HEAD~1..HEAD --format="%s (%cr)"'
    if os.name == 'posix':
        cmd = cmd.format(r'\`%h\`')
    else:
        cmd = cmd.format(r'`%h`')
    revision = os.popen(cmd).read().strip()

    return revision.split()[0]
