"""剪辑方案：解析、校验、时间轴换算。

云端只允许返回 JSON，本地必须把它变成「保留片段 + 字幕事件」才能交给 FFmpeg。
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .timeutil import TimeFormatError, fmt_clock, parse_time

KEEP_ACTIONS = {"保留", "keep", "keep_it", "1", "true", "yes", "是"}
DROP_ACTIONS = {"删除", "删掉", "drop", "cut", "remove", "0", "false", "no", "否"}

# ---- 目标时长识别：只认明确说出口的时长，不猜 ----
# 中文常用写法单独列，避免「一分钟」被数字正则漏掉
_CN_DURATIONS = {
    "半分钟": 30.0,
    "一分钟": 60.0,
    "一分半": 90.0,
    "两分钟": 120.0,
    "三分钟": 180.0,
    "五分钟": 300.0,
}

# 强提示词：出现这些词，才算用户在下「时长指令」
_STRONG_CUES = (
    "剪成", "剪到", "剪出", "剪短到", "做成", "压到", "压成", "缩到", "缩短到",
    "控制在", "控制到", "凑到", "做到", "总长", "总时长", "成片时长", "卡在",
)
# 弱提示词：单独出现时不够确定，只在没有强提示时兜底
_WEAK_CUES = ("时长", "大约")

_TARGET_RANGE_RE = re.compile(
    r"(?P<a>\d+(?:\.\d+)?)\s*[-~—到至]\s*(?P<b>\d+(?:\.\d+)?)\s*(?P<u>秒钟|秒|sec|分钟|min|s)",
    re.I,
)
_TARGET_SINGLE_RE = re.compile(
    r"(?P<a>\d+(?:\.\d+)?)\s*(?P<u>秒钟|秒|sec|分钟|min|s)",
    re.I,
)


class PlanError(ValueError):
    """方案无法被本地解析或执行。"""


@dataclass
class CutItem:
    start: float
    end: float
    action: str  # 统一为 "保留" / "删除"
    subtitle: str = ""

    @property
    def is_keep(self) -> bool:
        return self.action == "保留"


@dataclass
class Plan:
    hooks: str
    cuts: list[CutItem]
    bgm_time: str
    comment: str
    raw: dict[str, Any] = field(default_factory=dict)

    def keep_items(self) -> list[CutItem]:
        return [c for c in self.cuts if c.is_keep]


@dataclass
class Segment:
    """一个要保留的源片段，以及它在成片时间轴上的位置。"""

    src_start: float
    src_end: float
    out_start: float
    out_end: float
    subtitle: str = ""

    @property
    def duration(self) -> float:
        return self.src_end - self.src_start


def _as_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, (list, tuple)):
        return "；".join(str(v).strip() for v in value if str(v).strip())
    if isinstance(value, dict):
        return "；".join(f"{k}: {v}" for k, v in value.items())
    return str(value).strip()


def _normalize_action(value: Any, index: int) -> str:
    text = str(value).strip().lower()
    if text in KEEP_ACTIONS:
        return "保留"
    if text in DROP_ACTIONS:
        return "删除"
    raise PlanError(f"cut_list[{index}] 的 action 只能是「保留」或「删除」，收到: {value!r}")


def _strip_code_fence(text: str) -> str:
    text = text.strip()
    if text.startswith("```"):
        lines = text.splitlines()
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip().startswith("```"):
            lines = lines[:-1]
        text = "\n".join(lines).strip()
    return text


def extract_json_object(text: str) -> str:
    """从模型回复中截出第一个完整 JSON 对象，容忍前后多余文字。"""
    text = _strip_code_fence(text)
    start = text.find("{")
    if start < 0:
        raise PlanError("云端回复里找不到 JSON 对象")
    depth = 0
    in_string = False
    escaped = False
    for i in range(start, len(text)):
        ch = text[i]
        if in_string:
            if escaped:
                escaped = False
            elif ch == "\\":
                escaped = True
            elif ch == '"':
                in_string = False
            continue
        if ch == '"':
            in_string = True
        elif ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return text[start : i + 1]
    raise PlanError("JSON 对象不完整（括号未闭合）")


def parse_plan_json(text: str) -> Plan:
    """把云端返回的文本解析为 Plan，任何不合规都抛 PlanError。"""
    candidate = extract_json_object(text)
    try:
        data = json.loads(candidate)
    except json.JSONDecodeError as exc:
        raise PlanError(f"JSON 解析失败: {exc.msg}（位置 {exc.pos}）") from exc
    if not isinstance(data, dict):
        raise PlanError("JSON 顶层必须是对象")

    required = {"hooks", "cut_list", "bgm_time", "comment"}
    missing = required - data.keys()
    if missing:
        raise PlanError(f"缺少字段: {', '.join(sorted(missing))}")

    cuts_raw = data.get("cut_list")
    if not isinstance(cuts_raw, list) or not cuts_raw:
        raise PlanError("cut_list 必须是非空数组")

    cuts: list[CutItem] = []
    for index, item in enumerate(cuts_raw):
        if not isinstance(item, dict):
            raise PlanError(f"cut_list[{index}] 必须是对象")
        for key in ("start", "end", "action"):
            if key not in item:
                raise PlanError(f"cut_list[{index}] 缺少字段 {key}")
        try:
            start = parse_time(item["start"])
            end = parse_time(item["end"])
        except TimeFormatError as exc:
            raise PlanError(f"cut_list[{index}] 时间格式错误: {exc}") from exc
        if end <= start:
            raise PlanError(f"cut_list[{index}] end 必须大于 start（{start} -> {end}）")
        cuts.append(
            CutItem(
                start=start,
                end=end,
                action=_normalize_action(item["action"], index),
                subtitle=_as_text(item.get("subtitle", "")),
            )
        )

    if not any(c.is_keep for c in cuts):
        raise PlanError("cut_list 里没有任何「保留」片段，无法出片")

    plan = Plan(
        hooks=_as_text(data.get("hooks")),
        cuts=cuts,
        bgm_time=_as_text(data.get("bgm_time")),
        comment=_as_text(data.get("comment")),
        raw=data,
    )
    validate_against_duration(plan, None)
    return plan


def validate_against_duration(plan: Plan, duration: float | None) -> None:
    """片段必须落在源视频时长内；允许轻微越界，自动截断。"""
    if duration is None or duration <= 0:
        return
    tolerance = 0.5
    for index, cut in enumerate(plan.cuts):
        if cut.start > duration + tolerance:
            raise PlanError(
                f"cut_list[{index}] 起点 {fmt_clock(cut.start)} 超出视频时长 {fmt_clock(duration)}"
            )
        if cut.end > duration + tolerance:
            cut.end = duration
        if cut.end <= cut.start:
            raise PlanError(f"cut_list[{index}] 截断后长度为零")


def build_timeline(plan: Plan) -> tuple[list[Segment], float]:
    """把保留片段按顺序换算到成片时间轴，重叠部分自动裁剪。"""
    keep = sorted(plan.keep_items(), key=lambda c: c.start)
    segments: list[Segment] = []
    offset = 0.0
    cursor = 0.0
    for cut in keep:
        start = max(cut.start, cursor)
        end = cut.end
        if end - start <= 0.05:
            continue
        segments.append(
            Segment(
                src_start=start,
                src_end=end,
                out_start=offset,
                out_end=offset + (end - start),
                subtitle=cut.subtitle,
            )
        )
        offset += end - start
        cursor = end
    if not segments:
        raise PlanError("保留片段互相覆盖或长度为零，无法生成时间轴")
    return segments, offset


def _has_cue(text: str, start: int, words: tuple[str, ...], window: int = 8) -> bool:
    """数字前面一小段里有没有提示词，避免把「前 3 秒」当成目标时长。"""
    return any(word in text[max(0, start - window) : start] for word in words)


def _unit_factor(unit: str) -> float:
    return 60.0 if unit.lower() in {"分钟", "min"} else 1.0


def _clamp_target(seconds: float) -> float | None:
    """过滤明显不合理的结果（比如「3 秒」当钩子、「1080 秒」当分辨率）。"""
    if seconds < 2 or seconds > 3600:
        return None
    return round(seconds, 1)


def guess_target_duration(need: str) -> float | None:
    """从用户需求里识别目标成片时长（秒）。识别不到就返回 None，绝不瞎猜。

    只认明确说了时长的写法：剪成 10 秒 / 控制在 15 秒内 / 8-12 秒 / 两分钟。
    「前 3 秒做钩子」「每 8-12 秒一次变化」这类节奏描述会被排除。
    """
    text = (need or "").strip()
    if not text:
        return None

    for word, seconds in _CN_DURATIONS.items():
        if word in text:
            return seconds

    # 1) 明确区间：「剪成 8-12 秒」取中值
    for match in _TARGET_RANGE_RE.finditer(text):
        if not _has_cue(text, match.start(), _STRONG_CUES):
            continue
        low, high = float(match.group("a")), float(match.group("b"))
        if high > low > 0:
            value = _clamp_target((low + high) / 2 * _unit_factor(match.group("u")))
            if value is not None:
                return value

    matches = list(_TARGET_SINGLE_RE.finditer(text))

    # 2) 强提示词 + 单个数字
    for match in matches:
        if _has_cue(text, match.start(), _STRONG_CUES):
            value = _clamp_target(float(match.group("a")) * _unit_factor(match.group("u")))
            if value is not None:
                return value

    # 3) 「10 秒左右 / 15 秒以内」
    for match in matches:
        suffix = text[match.end() : match.end() + 4]
        if suffix.startswith(("左右", "上下", "以内", "之内", "出头")):
            value = _clamp_target(float(match.group("a")) * _unit_factor(match.group("u")))
            if value is not None:
                return value

    # 4) 弱提示词兜底：「时长大约 45 秒」
    for match in matches:
        if _has_cue(text, match.start(), _WEAK_CUES):
            value = _clamp_target(float(match.group("a")) * _unit_factor(match.group("u")))
            if value is not None:
                return value

    return None


def kept_duration(plan: Plan) -> float:
    """保留片段去重叠后的总时长，与 build_timeline 同口径。"""
    total = 0.0
    cursor = 0.0
    for cut in sorted(plan.keep_items(), key=lambda c: c.start):
        start = max(cut.start, cursor)
        if cut.end - start > 0.05:
            total += cut.end - start
            cursor = cut.end
    return round(total, 3)


def within_target(actual: float, target: float, ratio: float = 0.2, min_delta: float = 1.0) -> bool:
    """成片时长是否达标：允许 ±20% 或 ±1 秒，取更宽的那个。"""
    if target <= 0:
        return True
    return abs(actual - target) <= max(min_delta, target * ratio)


def save_plan(plan: Plan, path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "hooks": plan.hooks,
        "cut_list": [
            {
                "start": fmt_clock(c.start),
                "end": fmt_clock(c.end),
                "action": c.action,
                "subtitle": c.subtitle,
            }
            for c in plan.cuts
        ],
        "bgm_time": plan.bgm_time,
        "comment": plan.comment,
    }
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def load_plan(path: Path) -> Plan:
    return parse_plan_json(path.read_text(encoding="utf-8-sig"))


def mock_plan(duration: float, need: str) -> Plan:
    """无 API 时的占位方案：去掉中间 30%，保留头尾，用于跑通链路与测试。"""
    if duration <= 0:
        raise PlanError("视频时长为零，无法生成模拟方案")
    first_end = max(0.5, min(duration * 0.5, 6.0))
    second_start = min(duration - 0.5, duration * 0.7)
    cuts = [
        CutItem(0.0, first_end, "保留", "模拟字幕：开头钩子"),
        CutItem(first_end, second_start, "删除"),
        CutItem(second_start, duration, "保留", "模拟字幕：结尾收束"),
    ]
    cuts = [c for c in cuts if c.end > c.start]
    return Plan(
        hooks=f"模拟钩子：{need[:24] or '无需求文本'}",
        cuts=cuts,
        bgm_time="每 2 秒一个卡点（模拟）",
        comment="未配置云端 API，本地生成占位方案，仅用于验证流程",
        raw={"mock": True},
    )
