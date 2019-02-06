# -*- coding: utf-8 -*-

import discord
import lxml.etree as etree
import typing
from discord.ext import commands

from core import commands as inspector

_PYTHON_ICON = 'https://cdn.icon-icons.com/icons2/112/PNG/512/python_18894.png'


class Linting(metaclass=inspector.MetaCog, category='Inspection'):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @staticmethod
    def _format_pep_embed(embed: discord.Embed, nodes: typing.Generator):
        for node in nodes:
            title = node.getchildren()[0].text
            value = node.getchildren()[1].text

            if title == 'PEP:':
                continue
            elif title == 'Title:':
                embed.title = value
                continue
            elif title == 'Created:':
                embed.set_footer(text=f'{title} {value}', icon_url=_PYTHON_ICON)
                continue

            if value is not None:
                embed.description += f'\n**{title}** `{value}`\n'

        return embed

    @inspector.command()
    async def pep(self, ctx: inspector.Context, index: int):
        """Provides a Python Enhancement Proposal (PEP) by index."""

        def _get_nodes(page):
            root = etree.fromstring(page, etree.HTMLParser())
            return root.iterfind('.//table[@class="rfc2822 docutils field-list"]/tbody/tr[@class="field"]')

        url = 'https://www.python.org/dev/peps/pep-{:04d}/'.format(index)
        async with ctx.bot.session.get(url) as response:
            if response.status != 200:
                return await ctx.send('There\'s no such PEP out there.')

            text = await response.text(encoding='utf-8')

        nodes = await ctx.bot.loop.run_in_executor(None, lambda: _get_nodes(text))

        embed = discord.Embed(
            url=url,
            description=f'**PEP:** {index}',
            colour=discord.Colour.blurple()
        )

        await ctx.send(embed=self._format_pep_embed(embed, nodes))
