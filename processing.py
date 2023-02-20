import os
import datetime
import discord.ext.commands as commands
import util.vips as vips
import pyvips
from typing import Optional, Callable, Any, Tuple
import asyncio
import ffmpeg
import util.ffmpeg as ffutil
import concurrent.futures as futures
import functools
import util.vips as vips
import asyncio
from dataclasses import dataclass
import util.tmpfile as tmpfile
import yt_dlp


@dataclass
class Edits:
    music: str = ""
    musicskip: float = 0
    musicdelay: float = 0
    musicvolume: float = 1
    volume: Optional[float] = None
    speed: Optional[float] = None
    vreverse: bool = False
    areverse: bool = False
    mute: bool = False
    start: float = 0
    end: float = 0


def split_command(command: str) -> Tuple[str, str]:
    return command.split(" ", 1)


class ParseError(Exception):
    pass


def parse_timestamp(timestamp: str) -> float:
    split = timestamp.split(":")
    try:
        if len(split) == 2:
            return float(split[0]) * 60 + float(split[1])
        return float(split[0])
    except ValueError:
        raise ParseError(f"Invalid timestamp {timestamp}")


def parse_float(fl: str) -> float:
    try:
        return float(fl)
    except ValueError:
        raise ParseError(f"Invalid number {fl}")


def split_command(command: str) -> Tuple[str, str]:
    split = command.strip().split(" ", 1)
    if len(split) == 1:
        return split[0], ""
    return split


def check_empty(arg: str):
    if arg != "":
        raise ParseError(f"Command takes no arguments")


def parse_edit(edits: Edits, cmd: str, arg: str) -> Edits:
    if cmd == "music":
        edits.music = arg
    elif cmd == "musicskip":
        edits.musicskip = parse_timestamp(arg)
    elif cmd == "musicdelay":
        edits.musicdelay = parse_timestamp(arg)
    elif cmd == "musicvolume":
        edits.musicvolume = parse_float(arg)
    elif cmd == "volume":
        edits.volume = parse_float(arg)
    elif cmd == "speed":
        edits.speed = parse_float(arg)
    elif cmd == "vreverse":
        check_empty(arg)
        edits.vreverse = True
    elif cmd == "areverse":
        edits.areverse = True
    elif cmd == "reverse":
        edits.vreverse = True
        edits.areverse = True
    elif cmd == "mute":
        edits.mute = True
    elif cmd == "start":
        edits.start = parse_timestamp(arg)
    elif cmd == "end":
        edits.start = parse_timestamp(arg)
    else:
        raise ParseError(f"Unknown command")
    return edits


def parse_edits(args: str) -> Edits:
    edits = Edits()
    commands = [split_command(cmd) for cmd in args.split(",")]
    print(commands)
    seen = set()
    for cmd, _ in commands:
        if cmd in seen:
            raise ParseError(f"Duplicate command '{cmd}'")
        seen.add(cmd)
    for cmd, arg in commands:
        try:
            edits = parse_edit(edits, cmd, arg)
        except ParseError as e:
            raise ParseError(f"Error parsing command {cmd}: {e}")
    return edits


class File:
    def __init__(self, name: str, type: str) -> None:
        self.name = name
        self.type = type


def maybe_audio(probe, input):
    audio = (s for s in probe["streams"] if s["codec_type"] == "audio")
    stream = next(audio, None)
    if not stream:
        return None
    return input.audio


def write_overlay(func: Callable, width: int, height: int, *args, **kwargs) -> str:
    image: pyvips.Image = func(width, height, *args, **kwargs)
    return vips.write_image(image, ".png")


def vips_overlay(input: File, func: Callable, *args, **kwargs) -> str:
    if input.type == "gif":
        input_image: pyvips.Image = pyvips.Image.new_from_file(
            input.name, n=-1, access="sequential"
        )
    else:
        input_image: pyvips.Image = pyvips.Image.new_from_file(input.name)
    overlay: pyvips.Image = func(
        input_image.width, input_image.get_page_height(), *args, **kwargs
    )
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

def caption(input: File, text: str):
    if input.type == "gif":
        input_image: pyvips.Image = pyvips.Image.new_from_file(
            input.name, n=-1, access="sequential"
        )
    else:
        input_image: pyvips.Image = pyvips.Image.new_from_file(input.name)
    caption = vips.caption(input_image.width, text)
    out = vips.vstack(caption, input_image)
    suffix = ".gif" if input.type == "gif" else ".png"
    return vips.write_image(out, suffix)

class Processing:
    def __init__(self, exec: futures.Executor, loop: asyncio.AbstractEventLoop) -> None:
        self.exec = exec
        self.loop = loop

    async def spawn_blocking(self, func: Callable, *args, **kwargs) -> Any:
        return await self.loop.run_in_executor(
            self.exec, functools.partial(func, *args, **kwargs)
        )

    async def dimensions(self, filepath: str, media_type: str) -> Tuple[int, int]:
        if media_type in ["gif", "image"]:
            return await self.spawn_blocking(self.exec, vips.dimensions, filepath)
        else:
            probed = await ffutil.probe(filepath)
            return probed.video.width, probed.video.height

    async def ffmpeg_overlay(self, input: File, func: Callable, *args, **kwargs) -> str:
        overlay_file = None
        info = await ffutil.probe(input.name)
        video = next(
            (stream for stream in info["streams"] if stream["codec_type"] == "video"),
            None,
        )
        width = video["width"]
        height = video["height"]
        overlay_file = await self.spawn_blocking(
            write_overlay, func, width, height, *args, **kwargs
        )
        in_file = ffmpeg.input(input.name)
        overlay = ffmpeg.input(overlay_file)
        out = ffmpeg.overlay(in_file, overlay)
        suffix = ".gif" if input.type != "video" else ".mp4"
        tf = tmpfile.reserve(suffix)
        streams = [out]
        kwargs = {}
        if stream := maybe_audio(info, in_file):
            streams.append(stream)
            kwargs["acodec"] = "copy"
        try:
            await ffutil.run(
                ffmpeg.output(*streams, tf, **kwargs).global_args("-loglevel", "error")
            )
            return tf
        except Exception as e:
            os.remove(tf)
            raise e
        finally:
            if overlay_file is not None:
                os.remove(overlay_file)

    async def overlay(self, input: File, func: Callable, *args, **kwargs) -> str:
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
            v1 = v1.filter("trim", end=delay)
            a1 = a1.filter("atrim", end=delay)
        width, height = dimensions_from_probe(await ffutil.probe(file1.name))
        v2 = v2.filter(
            "scale", width, height, force_original_aspect_ratio="decrease"
        ).filter("pad", width, height, -1, -1)
        joined = ffmpeg.filter_multi_output([v1, a1, v2, a2], "concat", v=1, a=1)
        out = tmpfile.reserve(".mp4")
        try:
            await ffutil.run(ffmpeg.output(joined[0].filter("fps", 30), joined[1], out))
            return out
        except Exception as e:
            os.remove(out)
            raise e

    async def gif(self, inputf: File) -> str:
        out = tmpfile.reserve(".gif")
        input = ffmpeg.input(inputf.name)
        input = input.filter("fps", 30)
        split = input.filter_multi_output("split")
        palette = split[0].filter("palettegen")
        output = ffmpeg.filter([split[1], palette], "paletteuse")
        try:
            await ffutil.run(output.output(out))
            return out
        except Exception as e:
            os.remove(out)
            raise e

    async def loopvid(self, inputf: File, length: int) -> str:
        out = tmpfile.reserve(".mp4")
        input = ffmpeg.input(inputf.name, stream_loop=-1)
        if inputf.type in ["gif", "image"]:
            input = input.filter("fps", 30)
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
        cropped = input.video.filter("crop", *crop)
        out = tmpfile.reserve(".mp4")
        streams = [cropped]
        if stream := maybe_audio(probe, input):
            streams.append(stream)
        try:
            await ffutil.run(ffmpeg.output(*streams, out))
            return out
        except Exception as e:
            os.remove(out)
            raise e

    async def stack(self, orientation, file1, file2) -> str:
        if orientation not in ["vstack", "hstack"]:
            raise commands.BadArgument("Invalid stack orientation")
        probe1 = await ffutil.probe(file1.name)
        probe2 = await ffutil.probe(file2.name)

        def input(f):
            return (
                ffmpeg.input(f.name)
                .video.filter("setpts", "PTS-STARTPTS")
                .filter("format", "yuv420p")
            )

        input1 = input(file1)
        input2 = input(file2)
        if orientation == "vstack":
            scaled = ffmpeg.filter_multi_output(
                [input1, input2], "scale2ref", "iw", "ow/mdar"
            )
        else:
            scaled = ffmpeg.filter_multi_output(
                [input1, input2], "scale2ref", "ih*mdar", "ih"
            )
        stacked = (
            ffmpeg.filter([scaled[0], scaled[1]], orientation)
            .filter("pad", "ceil(iw/2)*2", "ceil(ih/2)*2")
            .filter("fps", 30)
        )
        out = tmpfile.reserve(".mp4")
        streams = [stacked]
        audios = []
        if audio1 := maybe_audio(probe1, ffmpeg.input(file1.name)):
            audios.append(audio1)
        if audio2 := maybe_audio(probe2, ffmpeg.input(file2.name)):
            audios.append(audio2)
        if len(audios) == 1:
            streams.append(audios[0])
        elif len(audios) == 2:
            streams.append(ffmpeg.filter(audios, "amix", dropout_transition=0))
        try:
            await ffutil.run(ffmpeg.output(*streams, out))
            return out
        except Exception as e:
            os.remove(out)
            raise e

    async def edit(self, input: File, edits: Edits) -> str:
        probe = await ffutil.probe(input.name)
        videoprobe = next(
            (s for s in probe["streams"] if s["codec_type"] == "video"), None
        )
        if videoprobe is None:
            raise Exception("no video stream")
        duration = float(videoprobe["duration"])
        input = ffmpeg.input(input.name)
        video = input.video
        ogvid = video
        audio = maybe_audio(probe, input)
        now = datetime.datetime.now()
        if edits.start or edits.end:
            if edits.start:
                start = edits.start
            else:
                start = 0
            if edits.end:
                end = edits.end
            else:
                end = duration
            if start > end:
                raise commands.BadArgument("Start time must be before end time")
            video = video.filter("trim", start=start, end=end)
            if audio:
                audio = audio.filter("atrim", start=start, end=end)
            duration = end - start
        if edits.mute:
            audio = None
        if audio and edits.volume:
            audio = audio.filter("volume", edits.volume)
        if edits.music:
            with yt_dlp.YoutubeDL() as ydl:
                info = ydl.extract_info("ytsearch:" + edits.music, download=False)
                if len(info["entries"]) == 0:
                    raise Exception("no results found")
                entry = info["entries"][0]
                formats = [
                    fmt
                    for fmt in entry["formats"]
                    if fmt["format_id"] in ["249", "250", "251"]
                ]
                if len(formats) == 0:
                    raise Exception("no audio formats found")
                format = formats[0]
                music = ffmpeg.input(format["url"], ss=edits.musicskip).filter(
                    "volume", edits.musicvolume
                )
                if not audio:
                    audio = ffmpeg.input("anullsrc", f="lavfi", t=duration)
                if edits.musicdelay:
                    split = audio.filter_multi_output("asplit", 2)
                    part1 = (
                        split[0]
                        .filter("atrim", end=edits.musicdelay)
                        .filter("asetpts", "PTS-STARTPTS")
                    )
                    part2 = (
                        split[1]
                        .filter("atrim", start=edits.musicdelay)
                        .filter("asetpts", "PTS-STARTPTS")
                    )
                    part2merged = ffmpeg.filter(
                        [part2, music], "amix", duration="first", dropout_transition=0
                    )
                    audio = ffmpeg.filter([part1, part2merged], "concat", n=2, v=0, a=1)
                else:
                    audio = ffmpeg.filter(
                        [audio, music], "amix", duration="first", dropout_transition=0
                    )
                print(f"time to get music: {datetime.datetime.now() - now}")
        if edits.speed:
            video = video.filter("setpts", f"{1 / edits.speed}*PTS")
            if audio:
                audio = audio.filter("atempo", edits.speed)
        if edits.vreverse:
            video = video.filter("reverse")
        if audio and edits.areverse:
            audio = audio.filter("areverse")
        streams = [video]
        if audio:
            streams.append(audio)
        out = tmpfile.reserve(".mp4")
        try:
            kwargs = {}
            if video is ogvid:
                kwargs["vcodec"] = "copy"
            await ffutil.run(ffmpeg.output(*streams, out, shortest=None, **kwargs))
            print(f"time to ffmpeg: {datetime.datetime.now() - now}")
            return out
        except Exception as e:
            os.remove(out)
            raise e

    async def meme(self, input: File, top: str, bottom: str) -> str:
        return await self.overlay(input, vips.meme, top, bottom)

    async def caption(self, input: File, caption_text: str) -> str:
        return await self.spawn_blocking(caption, input, caption_text)
