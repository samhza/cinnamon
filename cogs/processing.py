from bot import Cinnamon
from discord.ext import commands
import discord
import magic
import concurrent.futures
import processing
import functools
import os
import mimetypes
import tempfile
import processing
import requests
import util.ffmpeg as ffutil
import typing
import contextlib


async def media_from_message(ctx: commands.Context, message: discord.Message) -> str | None:
    for attachment in message.attachments:
        if attachment.height:
            return attachment.url
    for embed in message.embeds:
        if embed.type == "video" and not embed.provider:
            return embed.url
        if embed.type == "image":
            return embed.thumbnail.proxy_url
        if embed.type == "gifv":
            if embed.url.startswith("https://tenor.com"):
                resp = await ctx.bot.session.get(embed.url)
                body = await resp.text()
                return body.split('rel="image_src" href="')[1].split('"')[0]
            raise commands.BadArgument(f"TODO: gifv {embed.provider.name}")
    return None
class DisallowedMediaError(Exception):
    def __init__(self, type: str) -> None:
        self.type = type
        super().__init__(f"Unsupported media type {type}")

def mime_to_media_type(mime: str) -> str:
    match mime:
        case "image/gif":
            return "gif"
        case "video/mp4" | "video/webm" | "video/quicktime":
            return "video"
        case _:
            if mime.startswith("image"):
                return "image"
            else:
                raise DisallowedMediaError(mime)

def input(url: str, allowed_types: list) -> processing.File:
        type = mimetypes.guess_type(url)[0]
        if mime_to_media_type(type) not in allowed_types:
            raise commands.BadArgument("Unsupported media type")
        with requests.get(url, stream=True) as resp:
            resp.raise_for_status();
            first = next(resp.iter_content(chunk_size=512))
            mime = magic.detect_from_content(first)
            if mime_to_media_type(mime.mime_type) not in allowed_types:
                raise commands.BadArgument("Unsupported media type")
            with tempfile.NamedTemporaryFile(delete=False) as f:
                try:
                    f.write(first)
                    written = len(first)
                    for chunk in resp.iter_content(4096):
                        written += len(chunk)
                        f.write(chunk)
                    f.close()
                    return processing.File(f.name, mime_to_media_type(mime.mime_type))
                except Exception as e:
                    f.close()
                    os.remove(f.name)
                    raise e

class URL(commands.Converter):
    async def convert(self, argument: str) -> str:
        if argument.startswith("http"):
            return argument
        raise commands.BadArgument("Bad URL")

async def find_input(ctx: commands.Context) -> typing.Optional[str]:
    if media := await media_from_message(ctx, ctx.message):
        return media
    if ctx.message.reference:
        message = await ctx.channel.fetch_message(ctx.message.reference.message_id)
        if media := await media_from_message(ctx, message):
            return media
    async for message in ctx.channel.history(limit=50, oldest_first=False, before=ctx.message):
        if media := await media_from_message(ctx, message):
            return media

async def ensure_input_url(ctx: commands.Context, input: typing.Optional[str]) -> str:
    if input is None:
        input = await find_input(ctx)
        if input is None:
            raise commands.BadArgument("No media found")
    return input

# context manager to delete multiple temporary files
class TempFiles:
    def __init__(self, ctx: commands.Context):
        self.ctx = ctx
        self.exec = ctx.bot.get_cog("Processing").executor
        self.files = []

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        for file in self.files:
            os.remove(file)

    def append(self, file: str):
        self.files.append(file)

    async def input(self, url: typing.Optional[str], allowed_types: list) -> processing.File:
        url = await ensure_input_url(self.ctx, url)
        f = await self.ctx.bot.loop.run_in_executor(self.exec, functools.partial(input, url, allowed_types))
        self.append(f.name)
        return f

class Processing(commands.Cog):
    def __init__(self, bot: Cinnamon):
        self.bot = bot
        self.executor = concurrent.futures.ProcessPoolExecutor(max_workers=4)
        self.processing = processing.Processing(self.executor, self.bot.loop)
    
    async def input(self, url: str, allowed_types: list) -> processing.File:
        return await self.bot.loop.run_in_executor(self.executor, functools.partial(input, url, allowed_types))

    async def cog_command_error(self, ctx: commands.Context, error: commands.CommandError):
        if isinstance(error, (commands.BadArgument)):
                await ctx.reply(str(error))

    async def _stack(self, ctx: commands.Context, method: str, url1: str, url2: str):
        with TempFiles(ctx) as files:
            input1 = await files.input(url1, ["image", "video", "gif", "gifv"])
            input2 = await files.input(url2, ["image", "video", "gif", "gifv"])
            fname = await self.processing.stack(method, input1, input2)
            files.append(fname)
            await ctx.reply(file=discord.File(fname))
    
    @commands.command(name="probe")
    async def probe(self, ctx: commands.Context, url: typing.Optional[URL]):
        url = await ensure_input_url(ctx, url)
        probed = await ffutil.probe(url)
        reply = ""
        for stream in probed["streams"]:
            if stream["codec_type"] == "video":
                reply += f"Video: {stream['codec_name']} {stream['width']}x{stream['height']} {stream['avg_frame_rate']}fps {stream['duration']}s\n"
            elif stream["codec_type"] == "audio":
                reply += f"Audio: {stream['codec_name']} {stream['sample_rate']}Hz {stream['duration']}s\n"
        await ctx.reply(reply)

    @commands.command(name="crop")
    async def crop(self, ctx: commands.Context, url: typing.Optional[URL], direction: str, amount: int):
        with TempFiles(ctx) as files:
            input = await files.input(url, ["video"])
            fname = await self.processing.crop(input, direction, amount)
            files.append(fname)
            await ctx.reply(file=discord.File(fname))
              
    @commands.command(name="firstframe")
    async def firstframe(self, ctx: commands.Context, url: typing.Optional[URL]):
        with TempFiles(ctx) as files:
                input = await files.input(url, ["video", "gif"])
                fname = await self.processing.first_frame(input)
                await ctx.reply(file=discord.File(fname))

    @commands.command(name="gif")
    async def gif(self, ctx: commands.Context, url: typing.Optional[URL]):
        with TempFiles(ctx) as files:
            input = await files.input(url, ["video"])
            fname = await self.processing.gif(input)
            files.append(fname)
            await ctx.reply(file=discord.File(fname))

    @commands.command(name="loop")
    async def loop(self, ctx: commands.Context, url: typing.Optional[URL], duration: int):
        with TempFiles(ctx) as files:
            input = await files.input(url, ["video", "image", "gif"])
            fname = await self.processing.loopvid(input, duration)
            files.append(fname)
            await ctx.reply(file=discord.File(fname))

    
    @commands.command(name="cut")
    async def cut(self, ctx: commands.Context, url1:str, url2:str, delay: typing.Optional[int] = 0):
        with TempFiles(ctx) as files:
            input1 = await files.input(url1, ["video", "gif", "gifv"])
            input2 = await files.input(url2, ["video", "gif", "gifv"])
            fname = await self.processing.cut(input1, input2, delay)
            files.append(fname)
            await ctx.reply(file=discord.File(fname))
    @commands.command(name="stitch")
    async def stitch(self, ctx: commands.Context, url1: URL, url2: URL):
        await self._stack(ctx, "hstack", url1, url2)

    @commands.command(name="stack")
    async def stack(self, ctx: commands.Context, url1: URL, url2: URL):
        await self._stack(ctx, "vstack", url1, url2)

    @commands.command(name="meme")
    async def meme(self, ctx: commands.Context, media: typing.Optional[URL], top: str, bottom: str):
        with TempFiles(ctx) as files:
            input = await files.input(media, ["image", "gif", "gifv", "video"])
            fname = await self.processing.meme(input, top, bottom)
            files.append(fname)
            await ctx.reply(file=discord.File(fname))
    

async def setup(bot: Cinnamon) -> None:
    await bot.add_cog(Processing(bot))
