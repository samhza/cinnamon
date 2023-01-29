import asyncio
import json
import ffmpeg


class FFmpegError(Exception):
    def __init__(self, status: int, stderr: str) -> None:
        self.status = status
        self.stderr = stderr
        super().__init__(f"FFmpeg exited with status {status}: {stderr}")


class ProbeError(Exception):
    def __init__(self, status: int, stderr: str) -> None:
        self.status = status
        self.stderr = stderr
        super().__init__(f"FFprobe exited with status {status}: {stderr}")


async def run(stream_spec) -> None:
    args = ffmpeg.compile(stream_spec, overwrite_output=True)
    proc = await asyncio.create_subprocess_exec(
        *args, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
    )
    _, stderr = await proc.communicate()
    if proc.returncode != 0:
        raise FFmpegError(proc.returncode, stderr.decode())
    return


async def probe(filename: str) -> dict:
    proc = await asyncio.create_subprocess_exec(
        "ffprobe",
        "-show_format",
        "-show_streams",
        "-of",
        "json",
        filename,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, stderr = await proc.communicate()
    if proc.returncode != 0:
        raise ProbeError(proc.returncode, stderr.decode())
    return json.loads(stdout)
