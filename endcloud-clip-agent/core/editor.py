"""本地剪辑执行器：切片 → 拼接 → 字幕烧录 → BGM 混音 → 导出，全程 FFmpeg。"""

from __future__ import annotations

import json
import re
import shutil
from datetime import datetime
from pathlib import Path

from config import Settings

from .media import run_ffmpeg, video_info
from .plan import Plan, Segment
from .subtitles import SubtitleEvent, write_ass, write_srt

INVALID_NAME_CHARS = re.compile(r'[\\/:*?"<>|\r\n\t]')


def sanitize_name(name: str, fallback: str = "clip") -> str:
    cleaned = INVALID_NAME_CHARS.sub("_", (name or "").strip())
    cleaned = re.sub(r"\s+", "_", cleaned).strip("._")
    return cleaned[:60] or fallback


def unique_output(out_dir: Path, theme: str, ext: str = ".mp4") -> Path:
    """日期_主题_vN 自增，永不覆盖旧版。"""
    out_dir.mkdir(parents=True, exist_ok=True)
    day = datetime.now().strftime("%Y%m%d")
    safe_theme = sanitize_name(theme)
    version = 1
    while True:
        candidate = out_dir / f"{day}_{safe_theme}_v{version}{ext}"
        if not candidate.exists():
            return candidate
        version += 1


def escape_filter_path(path: Path) -> str:
    """Windows 盘符的冒号在 filter 参数里需要转义。"""
    text = path.resolve().as_posix()
    if re.match(r"^[A-Za-z]:", text):
        text = text[0] + r"\:" + text[2:]
    return text.replace("'", r"\'")


def target_size(info: dict, settings: Settings) -> tuple[int, int]:
    width, height = settings.out_width, settings.out_height
    if width <= 0 or height <= 0:
        width, height = info["width"], info["height"]
    return _even(width), _even(height)


def _even(value: int) -> int:
    value = int(value) or 2
    return value - (value % 2)


def cut_segment(
    src: Path,
    segment: Segment,
    out: Path,
    width: int,
    height: int,
    fps: int,
    crf: int,
    preset: str,
    has_audio: bool,
    logger,
) -> None:
    vf = (
        f"scale={width}:{height}:force_original_aspect_ratio=decrease,"
        f"pad={width}:{height}:(ow-iw)/2:(oh-ih)/2,setsar=1,fps={fps}"
    )
    args = ["-ss", f"{segment.src_start:.3f}", "-t", f"{segment.duration:.3f}", "-i", str(src)]
    if not has_audio:  # 无音轨素材补静音，保证拼接时音轨结构一致
        args += ["-f", "lavfi", "-i", "anullsrc=channel_layout=stereo:sample_rate=48000"]
    args += [
        "-map",
        "0:v:0",
        "-map",
        "0:a:0" if has_audio else "1:a:0",
        "-vf",
        vf,
        "-c:v",
        "libx264",
        "-preset",
        preset,
        "-crf",
        str(crf),
        "-pix_fmt",
        "yuv420p",
        "-c:a",
        "aac",
        "-b:a",
        "192k",
        "-ar",
        "48000",
        "-ac",
        "2",
        "-shortest",
        str(out),
    ]
    run_ffmpeg(args, logger=logger)


def concat_segments(parts: list[Path], list_file: Path, out: Path, logger) -> None:
    lines = []
    for part in parts:
        path = part.as_posix().replace("'", r"'\''")
        lines.append(f"file '{path}'")
    list_file.write_text("\n".join(lines) + "\n", encoding="utf-8")
    run_ffmpeg(
        ["-f", "concat", "-safe", "0", "-i", str(list_file), "-c", "copy", str(out)],
        logger=logger,
    )


def finalize(
    concat_file: Path,
    out: Path,
    subtitles_ass: Path | None,
    bgm: Path | None,
    bgm_volume: float,
    total: float,
    width: int,
    height: int,
    settings: Settings,
    logger,
) -> None:
    args = ["-i", str(concat_file)]
    if bgm:
        args += ["-stream_loop", "-1", "-i", str(bgm)]

    filters: list[str] = []
    if subtitles_ass:
        font_part = ""
        fonts_dir = Path(settings.subtitle_fonts_dir)
        if fonts_dir.exists():
            font_part = f":fontsdir='{escape_filter_path(fonts_dir)}'"
        filters.append(f"[0:v]subtitles=filename='{escape_filter_path(subtitles_ass)}'{font_part}[v]")
    if bgm:
        filters += [
            f"[1:a]volume={max(0.0, bgm_volume):.3f}[bg]",
            "[0:a][bg]amix=inputs=2:duration=first:dropout_transition=2:normalize=0[aout]",
        ]
    if filters:
        args += ["-filter_complex", ";".join(filters)]

    args += ["-map", "[v]" if subtitles_ass else "0:v:0"]
    args += ["-map", "[aout]" if bgm else "0:a:0"]

    if subtitles_ass:  # 烧字幕必须重新编码视频
        args += [
            "-c:v",
            "libx264",
            "-preset",
            settings.out_preset,
            "-crf",
            str(settings.out_crf),
            "-pix_fmt",
            "yuv420p",
        ]
    else:
        args += ["-c:v", "copy"]

    if bgm:
        args += ["-c:a", "aac", "-b:a", "192k", "-ar", "48000"]
    else:
        args += ["-c:a", "copy"]

    args += ["-t", f"{total:.3f}", "-movflags", "+faststart", str(out)]
    run_ffmpeg(args, logger=logger)


def write_notes(
    out_path: Path,
    plan: Plan | None,
    segments: list[Segment],
    events: list[SubtitleEvent],
    info: dict,
    settings: Settings,
    bgm: Path | None,
    need: str,
) -> Path:
    """交付说明：本地接手的人不用再问云端。"""
    notes = out_path.parent / f"{out_path.stem}_notes.md"
    lines = [
        f"# {out_path.stem}",
        "",
        f"- 生成时间：{datetime.now():%Y-%m-%d %H:%M:%S}",
        f"- 源素材：`{info['path']}`",
        f"- 源时长：{info['duration']} s（{info['width']}x{info['height']} @ {info['fps']}fps）",
        f"- 成片：`{out_path}`",
        f"- 成片时长：{sum(s.duration for s in segments):.2f} s",
        f"- 保留片段：{len(segments)} 段",
        f"- 字幕条数：{len(events)}",
        f"- BGM：{bgm.name if bgm else '无'}",
        f"- 导出：{settings.out_width}x{settings.out_height} / {settings.out_fps}fps / CRF {settings.out_crf}",
        "",
    ]
    if need:
        lines += ["## 用户需求", "", need, ""]
    if plan:
        lines += [
            "## 云端方案",
            "",
            f"- 钩子：{plan.hooks or '—'}",
            f"- 卡点：{plan.bgm_time or '—'}",
            f"- 建议：{plan.comment or '—'}",
            "",
            "| 源起点 | 源终点 | 成片起点 | 字幕 |",
            "|---|---|---|---|",
        ]
        for seg in segments:
            lines.append(
                f"| {seg.src_start:.2f}s | {seg.src_end:.2f}s | {seg.out_start:.2f}s | {seg.subtitle or '—'} |"
            )
        lines.append("")
    lines += ["## 可调项", "", "- 想更慢/更狠：调 `OUT_FPS`、`BGM_VOLUME` 或手动模式重剪。", "- 换字幕样式：改 `SUBTITLE_FONT` / `SUBTITLE_SIZE` / `SUBTITLE_MARGIN_V`。", ""]
    notes.write_text("\n".join(lines), encoding="utf-8")
    return notes


def render_timeline(
    src: Path,
    segments: list[Segment],
    total: float,
    events: list[SubtitleEvent],
    out_path: Path,
    settings: Settings,
    logger,
    bgm: Path | None = None,
    plan: Plan | None = None,
    need: str = "",
    keep_temp: bool = False,
) -> dict:
    info = video_info(src)
    width, height = target_size(info, settings)
    if not info["has_audio"]:
        logger.info("素材无音轨，自动补静音轨")

    temp_dir = settings.temp_dir / f"{out_path.stem}_{datetime.now():%H%M%S}"
    temp_dir.mkdir(parents=True, exist_ok=True)
    parts: list[Path] = []
    try:
        for index, segment in enumerate(segments, 1):
            part = temp_dir / f"seg_{index:03d}.mp4"
            logger.info(
                "切片 %d/%d：%s → %s（%.2fs）",
                index,
                len(segments),
                f"{segment.src_start:.2f}",
                f"{segment.src_end:.2f}",
                segment.duration,
            )
            cut_segment(
                src,
                segment,
                part,
                width,
                height,
                settings.out_fps,
                settings.out_crf,
                settings.out_preset,
                info["has_audio"],
                logger,
            )
            parts.append(part)

        concat_file = temp_dir / "concat.mp4"
        concat_segments(parts, temp_dir / "concat.txt", concat_file, logger)

        ass_file = None
        if events:
            ass_file = temp_dir / "subtitles.ass"
            write_ass(
                events,
                ass_file,
                width,
                height,
                settings.subtitle_font,
                settings.subtitle_size,
                settings.subtitle_margin_v,
            )
            write_srt(events, out_path.parent / f"{out_path.stem}.srt")

        logger.info("混音与导出：%s", out_path)
        finalize(
            concat_file,
            out_path,
            ass_file,
            bgm,
            settings.bgm_volume,
            total,
            width,
            height,
            settings,
            logger,
        )
        notes = write_notes(out_path, plan, segments, events, info, settings, bgm, need)
        if plan is not None:
            (out_path.parent / f"{out_path.stem}_plan.json").write_text(
                json.dumps(plan.raw or {}, ensure_ascii=False, indent=2), encoding="utf-8"
            )
        return {
            "output": str(out_path),
            "notes": str(notes),
            "duration": round(total, 2),
            "segments": len(segments),
            "subtitles": len(events),
            "size_bytes": out_path.stat().st_size if out_path.exists() else 0,
        }
    finally:
        if keep_temp:
            logger.info("临时文件保留在 %s", temp_dir)
        else:
            shutil.rmtree(temp_dir, ignore_errors=True)
