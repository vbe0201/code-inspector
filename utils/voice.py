# -*- coding: utf-8 -*-

import discord.opus
import discord.voice_client
from discord.ext import commands

try:
    import youtube_dl
except ImportError:
    youtube_dl = None


async def vc_check(ctx: commands.Context):
    """Check for whether VC is available in this bot."""

    if not discord.voice_client.has_nacl:
        raise commands.CheckFailure('You cannot use voice because PyNaCl is not loaded.')

    if not discord.opus.is_loaded():
        raise commands.CheckFailure('You cannot use voice because libopus is not loaded.')

    return True


async def connected_check(ctx: commands.Context):
    """Checks whether we are connected to VC in this guild."""

    voice = ctx.guild.voice_client
    if not voice or not voice.is_connected():
        raise commands.CheckFailure('Not connected to VC in this guild.')

    return True


async def playing_check(ctx: commands.Context):
    """Checks whether we are playing audio in VC in this guild.

    This doubles up as a connection check.
    """

    if await connected_check(ctx) and not ctx.guild.voice_client.is_playing():
        raise commands.CheckFailure('The voice client in this guild is not playing anything.')

    return True


BASIC_OPTS = {
    'format': 'webm[abr>0]/bestaudio/best',
    'prefer_ffmpeg': True,
    'quiet': True,
}


class BasicYTDLSource(discord.FFmpegPCMAudio):
    """Basic audio source for ytdl-compatible URLs."""

    def __init__(self, url, download: bool = False):
        ytdl = youtube_dl.YoutubeDL(BASIC_OPTS)
        info = ytdl.extract_info(url, download=download)
        super().__init__(info['url'])
