"""媒体处理底座：ffprobe 读取信息、FFmpeg 执行、关键帧抽取。"""

from __future__ import annotations

import base64
import json
import shlex
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

from .timeutil import fmt_clock


class FFmpegError(RuntimeError):
    """FFmpeg / ffprobe 执行失败。"""


@dataclass
class Frame:
    path: Path
    time_sec: float

    @property
    def clock(self) -> str:
        return fmt_clock(self.time_sec)

    def to_data_url(self) -> str:
        data = base64.b64encode(self.path.read_bytes()).decode("ascii")
        return f"data:image/jpeg;base64,{data}"


def ffmpeg_path() -> str:
    return shutil.which("ffmpeg") or "ffmpeg"


def ffprobe_path() -> str:
    return shutil.which("ffprobe") or "ffprobe"


def environment_ready() -> tuple[bool, str]:
    missing = [tool for tool in ("ffmpeg", "ffprobe") if shutil.which(tool) is None]
    if missing:
        return False, f"未找到 {', '.join(missing)}，请安装 FFmpeg 并加入 PATH"
    return True, "FFmpeg 可用"


def run_ffmpeg(args: list[str], logger=None, timeout: int | None = None) -> subprocess.CompletedProcess:
    """统一入口：记录命令、捕获 stderr、失败时抛出可读错误。"""
    cmd = [ffmpeg_path(), "-hide_banner", "-y", *args]
    if logger:
        logger.info("FFmpeg: %s", " ".join(shlex.quote(str(c)) for c in cmd))
    proc = subprocess.run(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout,
    )
    if proc.returncode != 0:
        tail = "\n".join(proc.stderr.strip().splitlines()[-15:])
        raise FFmpegError(f"FFmpeg 退出码 {proc.returncode}:\n{tail}")
    return proc


def _parse_rate(value: str | None) -> float:
    if not value or value == "0/0":
        return 0.0
    try:
        num, den = value.split("/")
        return float(num) / float(den) if float(den) else 0.0
    except (ValueError, ZeroDivisionError):
        return 0.0


def probe(path: Path) -> dict:
    cmd = [
        ffprobe_path(),
        "-v",
        "error",
        "-print_format",
        "json",
        "-show_format",
        "-show_streams",
        str(path),
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace")
    if proc.returncode != 0:
        raise FFmpegError(f"ffprobe 读取失败: {proc.stderr.strip()[-400:]}")
    return json.loads(proc.stdout or "{}")


def video_info(path: Path) -> dict:
    """返回剪辑需要的核心信息；无视频流直接报错。"""
    if not path.exists():
        raise FileNotFoundError(f"素材不存在: {path}")
    data = probe(path)
    video = next((s for s in data.get("streams", []) if s.get("codec_type") == "video"), None)
    if video is None:
        raise FFmpegError(f"素材没有视频流: {path}")
    audio = next((s for s in data.get("streams", []) if s.get("codec_type") == "audio"), None)

    duration = 0.0
    for candidate in (video.get("duration"), data.get("format", {}).get("duration")):
        try:
            if candidate:
                duration = float(candidate)
                break
        except ValueError:
            continue

    fps = _parse_rate(video.get("avg_frame_rate")) or _parse_rate(video.get("r_frame_rate"))
    return {
        "path": str(path.resolve()),
        "duration": round(duration, 3),
        "width": int(video.get("width") or 0),
        "height": int(video.get("height") or 0),
        "fps": round(fps, 3),
        "video_codec": video.get("codec_name", ""),
        "audio_codec": (audio or {}).get("codec_name", ""),
        "has_audio": audio is not None,
        "size_bytes": int(data.get("format", {}).get("size") or path.stat().st_size),
    }


def extract_frames(
    src: Path,
    out_dir: Path,
    count: int = 5,
    width: int = 720,
    logger=None,
    info: dict | None = None,
) -> list[Frame]:
    """按时间均匀抽帧，数量限制在 3–8 张，只上传少量图片给云端。"""
    info = info or video_info(src)
    duration = info["duration"]
    if duration <= 0:
        raise FFmpegError("视频时长为零，无法抽帧")
    count = min(8, max(3, int(count)))
    out_dir.mkdir(parents=True, exist_ok=True)

    frames: list[Frame] = []
    for i in range(count):
        t = round(duration * (i + 0.5) / count, 3)
        stamp = fmt_clock(t).replace(":", "-")
        out = out_dir / f"frame_{i + 1:02d}_{stamp}.jpg"
        try:
            run_ffmpeg(
                [
                    "-ss",
                    f"{t:.3f}",
                    "-i",
                    str(src),
                    "-frames:v",
                    "1",
                    "-vf",
                    f"scale={max(160, int(width))}:-2",
                    "-q:v",
                    "3",
                    str(out),
                ],
                logger=logger,
                timeout=60,
            )
        except FFmpegError:
            if logger:
                logger.warning("第 %d 帧抽取失败（时间 %.3fs），跳过", i + 1, t)
            continue
        if out.exists() and out.stat().st_size > 0:
            frames.append(Frame(path=out, time_sec=t))

    if not frames:
        raise FFmpegError("所有关键帧都抽取失败，请检查素材编码")
    return frames
