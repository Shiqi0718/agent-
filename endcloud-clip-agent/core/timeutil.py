"""时间解析与格式化：兼容 00:00:05、00:00:05.200、65、65.2 等写法。"""

from __future__ import annotations

import re


class TimeFormatError(ValueError):
    pass


def parse_time(value) -> float:
    """把各种时间写法统一成秒（float）。"""
    if isinstance(value, bool):
        raise TimeFormatError(f"非法时间: {value!r}")
    if isinstance(value, (int, float)):
        return float(value)

    text = str(value).strip().replace("：", ":").replace("，", ".")
    if not text:
        raise TimeFormatError("时间为空")
    if " " in text:  # 容错："00:00:05 200" 这种手写毫秒
        text = text.replace(" ", ".")

    if ":" in text:
        parts = text.split(":")
        try:
            nums = [float(p) for p in parts]
        except ValueError as exc:
            raise TimeFormatError(f"非法时间: {value!r}") from exc
        if len(nums) == 3:
            h, m, s = nums
        elif len(nums) == 2:
            h, m, s = 0.0, nums[0], nums[1]
        else:
            raise TimeFormatError(f"非法时间: {value!r}")
        return h * 3600 + m * 60 + s

    try:
        return float(text)
    except ValueError as exc:
        raise TimeFormatError(f"非法时间: {value!r}") from exc


def fmt_clock(seconds: float) -> str:
    """输出 HH:MM:SS.mmm，用于日志、文件名和方案存档。"""
    seconds = max(0.0, float(seconds))
    h = int(seconds // 3600)
    m = int((seconds % 3600) // 60)
    s = seconds % 60
    return f"{h:02d}:{m:02d}:{s:06.3f}"


def fmt_ass_time(seconds: float) -> str:
    """ASS 时间格式 H:MM:SS.cc（厘秒）。"""
    seconds = max(0.0, float(seconds))
    h = int(seconds // 3600)
    m = int((seconds % 3600) // 60)
    s = int(seconds % 60)
    cs = int(round((seconds - int(seconds)) * 100))
    if cs >= 100:  # 四舍五入进位
        cs = 99
    return f"{h}:{m:02d}:{s:02d}.{cs:02d}"


def parse_range(text: str) -> tuple[float, float]:
    """解析手动模式的时间段，如 '00:00:01-00:00:04' 或 '1.5-4'。"""
    cleaned = text.strip().replace("—", "-").replace("~", "-")
    match = re.match(r"^\s*([0-9:.]+)\s*-\s*([0-9:.]+)\s*$", cleaned)
    if not match:
        raise TimeFormatError(f"时间段格式应为 开始-结束，收到: {text!r}")
    start = parse_time(match.group(1))
    end = parse_time(match.group(2))
    if end <= start:
        raise TimeFormatError(f"结束时间必须大于开始时间: {text!r}")
    return start, end
