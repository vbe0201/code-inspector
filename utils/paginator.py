# -*- coding: utf-8 -*-

import asyncio
import collections
import contextlib
import re

import discord
from discord.ext import commands

from .highlightjs import get_language


EmojiSettings = collections.namedtuple('EmojiSettings', 'start back forward end close')

EMOJI_DEFAULT = EmojiSettings(
    start='\N{BLACK LEFT-POINTING DOUBLE TRIANGLE WITH VERTICAL BAR}',
    back='\N{BLACK LEFT-POINTING TRIANGLE}',
    forward='\N{BLACK RIGHT-POINTING TRIANGLE}',
    end='\N{BLACK RIGHT-POINTING DOUBLE TRIANGLE WITH VERTICAL BAR}',
    close='\N{BLACK SQUARE FOR STOP}'
)


class PaginatorInterface:
    """A reaction-based paginator interface for messages."""

    def __init__(self, bot: commands.Bot, paginator: commands.Paginator, owner: discord.Member = None, emojis: EmojiSettings = None):
        if not isinstance(paginator, commands.Paginator):
            raise TypeError('paginator must be a commands.Paginator instance.')

        self._display_page = 0

        self.bot = bot

        self.message = None
        self.owner = owner
        self.paginator = paginator
        self.emojis = emojis or EMOJI_DEFAULT

        self.sent_page_reactions = False

        self.task: asyncio.Task = None
        self.update_lock: asyncio.Lock = asyncio.Semaphore(value=2)

        if self.page_size > self.max_page_size:
            raise ValueError('paginator passed has too large of a page size for this interface.')

    @property
    def pages(self):
        paginator_pages = list(self.paginator._pages)
        if len(self.paginator._current_page) > 1:
            paginator_pages.append('\n'.join(self.paginator._current_page) + '\n' + self.paginator.suffix)

        return paginator_pages

    @property
    def page_count(self):
        return len(self.pages)

    @property
    def display_page(self):
        self._display_page = max(0, min(self.page_count - 1, self._display_page))
        return self._display_page

    @display_page.setter
    def display_page(self, value):
        self._display_page = max(0, min(self.page_count - 1, value))

    max_page_size = 2000

    @property
    def page_size(self):
        page_count = self.page_count
        return self.paginator.max_size + len(f'\nPage {page_count}/{page_count}')

    @property
    def send_kwargs(self):
        display_page = self.display_page
        page_num = f'\nPage {display_page + 1}/{self.page_count}'
        content = self.pages[display_page] + page_num
        return {'content': content}

    async def add_line(self, *args, **kwargs):
        display_page = self.display_page
        page_count = self.page_count

        self.paginator.add_line(*args, **kwargs)

        new_page_count = self.page_count

        if display_page + 1 == page_count:
            self._display_page = new_page_count
            self.bot.loop.create_task(self.update())

    async def send_to(self, destination: discord.abc.Messageable):
        self.message = await destination.send(**self.send_kwargs)

        # add the close reaction
        await self.message.add_reaction(self.emojis.close)

        # if there is more than one page and the reactions haven't been sent yet, send the navigation emotes
        if not self.sent_page_reactions and self.page_count > 1:
            await self.send_all_reactions()

        if self.task:
            self.task.cancel()

        self.task = self.bot.loop.create_task(self.wait_loop())

    async def send_all_reactions(self):
        for emoji in self.emojis:
            if emoji:
                await self.message.add_reaction(emoji)

        self.sent_page_reactions = True

    @property
    def closed(self):
        if not self.task:
            return False

        return self.task.done()

    async def wait_loop(self):
        start, back, forward, end, close = self.emojis

        def check(reaction, user):
            owner_check = user.id == self.owner.id if self.owner else not user.bot

            return (reaction.message.id == self.message.id
                    and reaction.emoji in self.emojis
                    and user.id != self.bot.user.id
                    and owner_check)

        try:
            while not self.bot.is_closed():
                reaction, user = await self.bot.wait_for('reaction_add', check=check, timeout=3600)

                if reaction.emoji == close:
                    await self.message.delete()
                    return

                if reaction.emoji == start:
                    self._display_page = 0
                elif reaction.emoji == end:
                    self._display_page = self.page_count - 1
                elif reaction.emoji == back:
                    self._display_page -= 1
                elif reaction.emoji == forward:
                    self._display_page += 1

                self.bot.loop.create_task(self.update())

                with contextlib.suppress(discord.Forbidden):
                    await self.message.remove_reaction(reaction.emoji, user)

        except asyncio.TimeoutError:
            await self.message.delete()

    async def update(self):
        if self.update_lock.locked():
            return

        async with self.update_lock:
            if self.update_lock.locked():
                # if this has exhausted the semaphore, we need to calm down
                await asyncio.sleep(1)

            if not self.message:
                # too fast, stagger so this update gets through
                await asyncio.sleep(0.5)

            if not self.sent_page_reactions and self.page_count > 1:
                self.bot.loop.create_task(self.send_all_reactions())
                self.sent_page_reactions = True

            await self.message.edit(**self.send_kwargs)


class PaginatorEmbedInterface(PaginatorInterface):
    """A paginator interface that encloses content in an embed."""

    def __init__(self, *args, **kwargs):
        self._embed = kwargs.pop('embed', None) or discord.Embed()
        super().__init__(*args, **kwargs)

    @property
    def send_kwargs(self):
        display_page = self.display_page
        self._embed.description = self.pages[display_page]
        self._embed.set_footer(text=f'Page {display_page + 1}/{self.page_count}')
        return {'embed': self._embed}

    max_page_size = 2048

    @property
    def page_size(self):
        return self.paginator.max_size


class WrappedPaginator(commands.Paginator):
    """A paginator that allows automatic correcting of lines that do not fit.

    Useful for paginating unpredictable output.
    """

    def __init__(self, *args, wrap_on=('\n', ' '), include_wrapped=True, **kwargs):
        super().__init__(*args, **kwargs)

        self.wrap_on = wrap_on
        self.include_wrapped = include_wrapped

    def add_line(self, line='', *, empty=False):
        true_max_size = self.max_size - len(self.prefix) - 2

        while len(line) > true_max_size:
            search_string = line[0:true_max_size - 1]
            wrapped = False

            for delimiter in self.wrap_on:
                position = search_string.rfind(delimiter)

                if position > 0:
                    super().add_line(line[0:position], empty=empty)
                    wrapped = True

                    if self.include_wrapped:
                        line = line[position:]
                    else:
                        line = line[position + len(delimiter):]

                    break

            if not wrapped:
                break  # this will probably always cause an exception

        super().add_line(line, empty=empty)


class FilePaginator(commands.Paginator):
    """A paginator for syntax-highlighted codeblocks, read from file-like."""

    __encoding_regex = re.compile(br'coding[=:]\s*([-\w.]+)')

    def __init__(self, fp, line_span=None, **kwargs):
        language = ''

        try:
            language = get_language(fp.name)
        except AttributeError:
            pass

        raw_content = fp.read()
        self.lines = self._get_lines(raw_content)
        del raw_content

        first_line = self.lines[0]
        # If the first line is a shebang,
        if first_line.startswith('#!'):
            # prioritize its declaration over the extension.
            language = get_language(first_line) or language

        super().__init__(prefix=f'```{language}', suffix='```', **kwargs)

        line_number = len(self.lines)
        if line_span:
            self.lines = self._get_line_span(line_span, line_number)

        for line in self.lines:
            self.add_line(line)

    def _get_lines(self, raw_content):
        try:
            return raw_content.decode('utf-8').split('\n')
        except UnicodeDecodeError as error:
            # The file isn't utf-8.
            # Ideally speaking, garbage.

            encoding_match = self.__encoding_regex.search(raw_content[:128])

            if encoding_match:
                encoding = encoding_match.group(1)
            else:
                raise error

            try:
                return raw_content.decode(encoding.decode('utf-8')).split('\n')
            except UnicodeDecodeError as error2:
                raise error2 from error

    def _get_line_span(self, line_span, line_number):
        line_span = sorted(line_span)
        if min(line_span) < 1 or max(line_span) > line_number:
            raise ValueError('Linespan goes out of bounds.')

        return self.lines[line_span[0] - 1:line_span[1]]
