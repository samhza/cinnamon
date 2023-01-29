import os
import discord.ext.commands as commands
import util.vips as vips
import pyvips
import typing
import asyncio
import ffmpeg
import util.ffmpeg as ffutil
import concurrent.futures as futures
import functools
import util.vips as vips
import asyncio
import util.tmpfile as tmpfile


class File:
    def __init__(self, name: str, type: str) -> None:
        self.name = name
        self.type = type

def audio(probe, input):
    stream = next((s for s in probe["streams"] if s["codec_type"] == "audio"), None)
    if not stream:
        return None
    return input.audio

def write_overlay(func: typing.Callable, width: int, height: int, *args, **kwargs) -> str:
    image: pyvips.Image = func(width, height, *args, **kwargs)
    return vips.write_image(image, ".png")

def vips_overlay(input: File, func: typing.Callable, *args, **kwargs) -> str:
    if input.type == "gif":
        input_image: pyvips.Image = pyvips.Image.new_from_file(input.name, n=-1, access="sequential")
    else:
        input_image: pyvips.Image = pyvips.Image.new_from_file(input.name)
    overlay: pyvips.Image = func(input_image.width, input_image.get_page_height(), *args, **kwargs)
    replicated: pyvips.Image = overlay.replicate(1, input_image.get_n_pages())
    output: pyvips.Image = input_image.composite2(replicated, "over")
    suffix = ".gif" if input.type == "gif" else ".png"
    out = vips.write_image(output, suffix)
    return out

def dimensions_from_probe(probe):
    video = next((s for s in probe["streams"] if s["codec_type"] == "video"), None)
    if not video:
        raise commands.BadArgument("No video stream found")
    return video["width"], video["height"]


class Processing():
    def __init__(self, exec: futures.Executor, loop: asyncio.AbstractEventLoop) -> None:
        self.exec = exec
        self.loop = loop

    async def spawn_blocking(self, func: typing.Callable, *args, **kwargs) -> typing.Any:
        return await self.loop.run_in_executor(self.exec, functools.partial(func, *args, **kwargs))

    async def dimensions(self, filepath: str, media_type: str) -> typing.Tuple[int, int]:
        if media_type in ["gif", "image"]:
            return await self.spawn_blocking(self.exec, vips.dimensions, filepath)
        else:
            probed = await ffutil.probe(filepath)
            return probed.video.width, probed.video.height

    async def ffmpeg_overlay(self, input: File, func: typing.Callable, *args, **kwargs) -> str:
        overlay_file = None
        info = await ffutil.probe(input.name)
        video = next((stream for stream in info["streams"] if stream["codec_type"] == "video"), None)
        width = video["width"]
        height = video["height"]
        overlay_file = await self.spawn_blocking(write_overlay, func, width, height, *args, **kwargs)
        in_file = ffmpeg.input(input.name)
        overlay = ffmpeg.input(overlay_file)
        out = ffmpeg.overlay(in_file, overlay)
        suffix = ".gif" if input.type != "video" else ".mp4"
        tf = tmpfile.reserve(suffix)
        streams = [out]
        kwargs = {}
        if audiostream := audio(info, in_file):
            streams.append(audiostream)
            kwargs["acodec"] = "copy"
        try:
            await ffutil.run(ffmpeg.output(*streams, tf, **kwargs).global_args('-loglevel', 'error'))
            return tf
        except Exception as e:
            os.remove(tf)
            raise e
        finally:
            if overlay_file is not None:
                os.remove(overlay_file)

    async def overlay(self, input: File, func: typing.Callable, *args, **kwargs) -> str:
        if input.type in ["gif", "image"]:
            return await self.spawn_blocking(vips_overlay, input, func, *args, **kwargs)
        else:
            return await self.ffmpeg_overlay(input, func, *args, **kwargs)

    async def cut(self, file1, file2, delay: int) -> str:
        i1 = ffmpeg.input(file1.name)
        i2 = ffmpeg.input(file2.name)
        v1, v2 = i1.video, i2.video
        a1, a2 = i1.audio, i2.audio
        if delay > 0:
            v1 = v1.filter('trim', end=delay)
            a1 = a1.filter('atrim', end=delay)
        width, height = dimensions_from_probe(await ffutil.probe(file1.name))
        v2 = v2.filter('scale', width, height, force_original_aspect_ratio='decrease').filter('pad', width, height, -1, -1)
        joined = ffmpeg.filter_multi_output([v1, a1, v2, a2], 'concat', v=1, a=1)
        out = tmpfile.reserve(".mp4")
        try:
            await ffutil.run(ffmpeg.output(joined[0].filter('fps', 30), joined[1], out))
            return out
        except Exception as e:
            os.remove(out)
            raise e

    async def loopvid(self, inputf: File, length: int) -> str:
        out = tmpfile.reserve(".mp4")
        input = ffmpeg.input(inputf.name, stream_loop=-1)
        if inputf.type in ["gif", "image"]:
            input = input.filter('fps', 30)
        output = input.output(out, t=length)
        try:
            await ffutil.run(output)
            return out
        except Exception as e:
            os.remove(out)
            raise e
    async def first_frame(self, inputf: File) -> str:
        out = tmpfile.reserve(".png")
        input = ffmpeg.input(inputf.name)
        output = input.output(out, vframes=1)
        try:
            await ffutil.run(output)
            return out
        except Exception as e:
            os.remove(out)
            raise e
    async def crop(self, input: File, direction: str, amount: int) -> str:
        probe = await ffutil.probe(input.name)
        width, height = dimensions_from_probe(probe)
        if direction == "top":
            crop = (width, height - amount, 0, amount)
        elif direction == "bottom":
            crop = (width, height - amount, 0, 0)
        elif direction == "left":
            crop = (width - amount, height, amount, 0)
        elif direction == "right":
            crop = (width - amount, height, 0, 0)
        else:
            raise commands.BadArgument("Invalid crop direction")
        input = ffmpeg.input(input.name)
        cropped = input.video.filter('crop', *crop)
        out = tmpfile.reserve(".mp4")
        streams = [cropped]
        if audiostream := audio(probe, input):
            streams.append(audiostream)
        try:
            await ffutil.run(ffmpeg.output(*streams, out))
            return out
        except Exception as e:
            os.remove(out)
            raise e
    async def stack(self, orientation, file1, file2) -> str:
        probe1 = await ffutil.probe(file1.name)
        probe2 = await ffutil.probe(file2.name)
        input1 = ffmpeg.input(file1.name).video.filter('setpts', 'PTS-STARTPTS').filter('format', 'yuv420p')
        input2 = ffmpeg.input(file2.name).video.filter('setpts', 'PTS-STARTPTS').filter('format', 'yuv420p')
        if orientation == "vstack":
            scaled = ffmpeg.filter_multi_output([input1, input2], 'scale2ref', 'iw', 'ow/mdar')
        else:
            scaled = ffmpeg.filter_multi_output([input1, input2], 'scale2ref', 'ih*mdar', 'ih')
        stacked = ffmpeg.filter([scaled[0], scaled[1]], orientation).filter("pad", "ceil(iw/2)*2", "ceil(ih/2)*2").filter('fps', 30)
        out = tmpfile.reserve(".mp4")
        streams = [stacked]
        audios = []
        if audio1 := audio(probe1, ffmpeg.input(file1.name)):
            audios.append(audio1)
        if audio2 := audio(probe2, ffmpeg.input(file2.name)):
            audios.append(audio2)
        if len(audios) == 1:
            streams.append(audios[0])
        elif len(audios) == 2:
            streams.append(ffmpeg.filter(audios, 'amix', dropout_transition=0))
        try:
            await ffutil.run(ffmpeg.output(*streams, out))
            return out
        except Exception as e:
            os.remove(out)
            raise e


    async def meme(self, input: File, top: str, bottom: str) -> str:
        return await self.overlay(input, vips.meme, top, bottom)
