"""字幕：生成 ASS（烧录进画面）与 SRT（外挂/校对），样式与位置可配置。"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from .plan import Segment
from .timeutil import fmt_ass_time


@dataclass
class SubtitleEvent:
    start: float
    end: float
    text: str


def events_from_segments(segments: list[Segment]) -> list[SubtitleEvent]:
    """片段自带字幕 → 成片时间轴上的字幕事件；相邻同文案自动合并。"""
    events: list[SubtitleEvent] = []
    for seg in segments:
        text = seg.subtitle.strip()
        if not text:
            continue
        if events and events[-1].text == text and abs(events[-1].end - seg.out_start) < 0.05:
            events[-1].end = seg.out_end
        else:
            events.append(SubtitleEvent(seg.out_start, seg.out_end, text))
    return events


def _escape_ass(text: str) -> str:
    # 花括号是 ASS 控制符，换行用 \N
    text = text.replace("{", "｛").replace("}", "｝")
    return text.replace("\r\n", "\n").replace("\n", r"\N")


def build_ass(
    events: list[SubtitleEvent],
    width: int,
    height: int,
    font: str,
    size: int,
    margin_v: int | None = None,
    alignment: int = 2,
) -> str:
    if margin_v is None or margin_v <= 0:
        margin_v = int(height * 0.18) if height >= width else int(height * 0.07)

    header = f"""[Script Info]
; 由 endcloud-clip-agent 生成
ScriptType: v4.00+
PlayResX: {width}
PlayResY: {height}
WrapStyle: 0
ScaledBorderAndShadow: yes
YCbCr Matrix: TV.709

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Default,{font},{int(size)},&H00FFFFFF,&H000000FF,&H00000000,&H80000000,0,0,0,0,100,100,0,0,1,4,1,{alignment},48,48,{int(margin_v)},1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""
    lines = []
    for event in events:
        if event.end <= event.start or not event.text.strip():
            continue
        lines.append(
            "Dialogue: 0,{start},{end},Default,,0,0,0,,{text}".format(
                start=fmt_ass_time(event.start),
                end=fmt_ass_time(event.end),
                text=_escape_ass(event.text.strip()),
            )
        )
    return header + "\n".join(lines) + "\n"


def build_srt(events: list[SubtitleEvent]) -> str:
    def srt_time(seconds: float) -> str:
        ms = int(round(seconds * 1000))
        h, ms = divmod(ms, 3_600_000)
        m, ms = divmod(ms, 60_000)
        s, ms = divmod(ms, 1000)
        return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"

    blocks = []
    for i, event in enumerate(events, 1):
        if event.end <= event.start or not event.text.strip():
            continue
        blocks.append(f"{i}\n{srt_time(event.start)} --> {srt_time(event.end)}\n{event.text.strip()}\n")
    return "\n".join(blocks)


def write_ass(events: list[SubtitleEvent], path: Path, width: int, height: int, font: str, size: int, margin_v: int | None = None) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(build_ass(events, width, height, font, size, margin_v), encoding="utf-8")
    return path


def write_srt(events: list[SubtitleEvent], path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(build_srt(events), encoding="utf-8")
    return path
