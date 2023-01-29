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

def media_from_message(message: discord.Message) -> str | None:
    for attachment in message.attachments:
        if attachment.height:
            return attachment.url
    for embed in message.embeds:
        if embed.type == "video" and not embed.provider:
            return embed.url
        if embed.type == "image":
            return embed.thumbnail.proxy_url
        if embed.type == "gifv":
            raise commands.BadArgument("TODO: gifv")
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

class MediaInput(commands.Converter):
    async def convert(self, ctx: commands.Context, argument: str) -> str:
        if ctx.message.attachments:
            ctx.view.undo()
            return ctx.message.attachments[0].url
        if argument.startswith("http"):
            return argument
        ctx.view.undo()
        if ctx.message.reference:
            message = await ctx.channel.fetch_message(ctx.message.reference.message_id)
            if media := media_from_message(message):
                return media
        async for message in ctx.channel.history(limit=50, oldest_first=False, before=ctx.message):
            if media := media_from_message(message):
                return media
        raise commands.BadArgument("No media found")

class Basic(commands.Cog):
    def __init__(self, bot: Cinnamon):
        self.bot = bot
        self.executor = concurrent.futures.ProcessPoolExecutor(max_workers=4)
        self.processing = processing.Processing(self.executor, self.bot.loop)
    
    async def input(self, url: str, allowed_types: list) -> processing.File:
        return await self.bot.loop.run_in_executor(self.executor, functools.partial(input, url, allowed_types))


    async def cog_command_error(self, ctx: commands.Context, error: commands.CommandError):
        if isinstance(error, (commands.BadArgument)):
                await ctx.reply(str(error))


    

    async def _stack(self, ctx: commands.Context, method: str, url1: str, url2:str):
        async with ctx.typing():
            input1 = None
            input2 = None
            fname = None
            try:
                input1 = await self.input(url1, ["image", "video", "gif", "gifv"])
                input2 = await self.input(url2, ["image", "video", "gif", "gifv"])
                fname = await self.processing.stack(method, input1, input2)
                await ctx.reply(file=discord.File(fname))
            finally:
                if input1 is not None:
                    os.remove(input1.name)
                if input2 is not None:
                    os.remove(input2.name)
                if fname is not None:
                    os.remove(fname)
    
    @commands.command(name="probe")
    async def probe(self, ctx: commands.Context, url: MediaInput):
        async with ctx.typing():
            input = None
            try:
                probed = await ffutil.probe(url)
                reply = ""
                for stream in probed["streams"]:
                    if stream["codec_type"] == "video":
                        reply += f"Video: {stream['codec_name']} {stream['width']}x{stream['height']} {stream['avg_frame_rate']}fps {stream['duration']}s\n"
                    elif stream["codec_type"] == "audio":
                        reply += f"Audio: {stream['codec_name']} {stream['sample_rate']}Hz {stream['duration']}s\n"
                await ctx.reply(reply)
            finally:
                if input is not None:
                    os.remove(input.name)

    @commands.command(name="crop")
    async def crop(self, ctx: commands.Context, url: MediaInput, direction: str, amount: int):
        async with ctx.typing():
            input = None
            fname = None
            try:
                input = await self.input(url, ["video"])
                fname = await self.processing.crop(input, direction, amount)
                await ctx.reply(file=discord.File(fname))
            finally:
                if input is not None:
                    os.remove(input.name)
                if fname is not None:
                    os.remove(fname)
              
    @commands.command(name="firstframe")
    async def firstframe(self, ctx: commands.Context, url: MediaInput):
        async with ctx.typing():
            input = None
            fname = None
            try:
                input = await self.input(url, ["video", "gif"])
                fname = await self.processing.first_frame(input)
                await ctx.reply(file=discord.File(fname))
            finally:
                if input is not None:
                    os.remove(input.name)
                if fname is not None:
                    os.remove(fname)

    @commands.command(name="loop")
    async def loop(self, ctx: commands.Context, url: MediaInput, duration: int):
        async with ctx.typing():
            input = None
            fname = None
            try:
                input = await self.input(url, ["video", "image", "gif"])
                fname = await self.processing.loopvid(input, duration)
                await ctx.reply(file=discord.File(fname))
            finally:
                if input is not None:
                    os.remove(input.name)
                if fname is not None:
                    os.remove(fname)

    
    @commands.command(name="cut")
    async def cut(self, ctx: commands.Context, url1:str, url2:str, delay: typing.Optional[int] = 0):
        async with ctx.typing():
            input1 = None
            input2 = None
            fname = None
            try:
                input1 = await self.input(url1, ["video", "gif", "gifv"])
                input2 = await self.input(url2, ["video", "gif", "gifv"])
                fname = await self.processing.cut(input1, input2, delay)
                await ctx.reply(file=discord.File(fname))
            finally:
                if input1 is not None:
                    os.remove(input1.name)
                if input2 is not None:
                    os.remove(input2.name)
                if fname is not None:
                    os.remove(fname)
                    
    @commands.command(name="stitch")
    async def stitch(self, ctx: commands.Context, url1: str, url2: str):
        await self._stack(ctx, "hstack", url1, url2)

        
    @commands.command(name="stack")
    async def stack(self, ctx: commands.Context, url1: str, url2: str):
        await self._stack(ctx, "vstack", url1, url2)

    @commands.command(name="meme")
    async def meme(self, ctx: commands.Context, media: MediaInput, top: str, bottom: str):
        async with ctx.typing():
            input = None
            fname = None
            try:
                input = await self.input(media, ["image", "gif", "gifv", "video"])
                fname = await self.processing.meme(input, top, bottom)
                await ctx.reply(file=discord.File(fname))
            finally:
                if fname:
                    os.remove(fname)
                if input:
                    os.remove(input.name)
        

async def setup(bot: Cinnamon) -> None:
    await bot.add_cog(Basic(bot))
