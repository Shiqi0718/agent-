"""云端规划器：只上传「文字需求 + 少量关键帧」，只接收结构化 JSON 方案。"""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from typing import Any

from config import Settings

from .media import Frame
from .plan import (
    Plan,
    PlanError,
    kept_duration,
    parse_plan_json,
    validate_against_duration,
    within_target,
)

SYSTEM_PROMPT = """你是资深短视频/漫剧剪辑导演，同时精通抖音与 B 站的爆款节奏。
你只输出一个 JSON 对象，不要任何解释、Markdown、前后缀文字。

JSON 字段固定如下：
{
  "hooks": "钩子建议（一句话，说明前 3 秒靠什么留住人）",
  "cut_list": [
    {"start": "00:00:00", "end": "00:00:05", "action": "保留", "subtitle": "这一段的字幕"}
  ],
  "bgm_time": "卡点时间戳，如 00:00:02,00:00:06",
  "comment": "整体优化建议"
}

硬性要求：
1. cut_list 必须按源视频时间轴，从 00:00:00 开始，覆盖到视频结尾；
2. action 只能是「保留」或「删除」；
3. 每个「保留」段都要给 subtitle，单行不超过 14 个字，口语化，不要标点堆砌；
4. 至少保留一个「保留」段；若用户给了目标时长，「保留」段合计必须接近目标（±10% 以内）；无论如何不超过源视频时长；
5. 时间格式 HH:MM:SS 或 HH:MM:SS.mmm，且必须落在视频时长内；
6. 不要返回散文式方案，不要解释字段含义；时长不够就改 cut_list，不要只写在 comment 里当建议。"""


class CloudError(RuntimeError):
    """云端接口不可用、鉴权失败或返回内容不合规。"""


class CloudPlanner:
    def __init__(self, settings: Settings, logger):
        self.settings = settings
        self.logger = logger
        # 每次 analyze 的原始回复（首轮 / 纠偏），供上层归档排查
        self.trace: list[dict[str, str]] = []

    def _endpoint(self) -> str:
        base = self.settings.llm_base_url.rstrip("/")
        if base.endswith("/chat/completions"):
            return base
        return f"{base}/chat/completions"

    def _build_messages(
        self, need: str, info: dict, frames: list[Frame], target: float | None = None
    ) -> list[dict[str, Any]]:
        target_line = ""
        if target and target > 0:
            target_line = f"【目标时长】约 {target:.1f} 秒（源素材 {info['duration']:.1f} 秒，不得超过）\n"
        text = (
            f"【用户需求】{need}\n"
            f"【素材信息】时长 {info['duration']} 秒，{info['width']}x{info['height']}，"
            f"{info['fps']} fps，{'有' if info['has_audio'] else '无'}音轨\n"
            f"{target_line}"
            f"【关键帧时间点】{', '.join(f.clock for f in frames)}\n"
            "请给出可直接执行的剪辑方案。"
        )
        content: list[dict[str, Any]] = [{"type": "text", "text": text}]
        if self.settings.llm_vision:
            for frame in frames:
                content.append(
                    {
                        "type": "image_url",
                        "image_url": {"url": frame.to_data_url(), "detail": "low"},
                    }
                )
        return [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": content},
        ]

    def _post(self, payload: dict) -> dict:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        request = urllib.request.Request(
            self._endpoint(),
            data=body,
            method="POST",
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {self.settings.llm_api_key}",
            },
        )
        with urllib.request.urlopen(request, timeout=self.settings.llm_timeout) as response:
            return json.loads(response.read().decode("utf-8"))

    def analyze(
        self, need: str, info: dict, frames: list[Frame], target: float | None = None
    ) -> tuple[Plan, str]:
        """请求云端方案；给了目标时长就先校验，偏离过大再纠偏一次。"""
        if not self.settings.cloud_ready:
            raise CloudError("未配置云端 API：请在 .env 填写 LLM_API_KEY / LLM_BASE_URL / LLM_MODEL")

        self.trace = []
        messages = self._build_messages(need, info, frames, target)
        plan, text = self._request(messages, "首轮")
        validate_against_duration(plan, info["duration"])

        if not target or target <= 0:
            return plan, text

        actual = kept_duration(plan)
        if within_target(actual, target):
            self.logger.info("时长校验通过：目标 %.1fs / 实际 %.1fs", target, actual)
            return plan, text

        self.logger.warning("时长偏离：目标 %.1fs / 实际 %.1fs，发起一次纠偏", target, actual)
        correction = messages + [
            {"role": "assistant", "content": text},
            {"role": "user", "content": self._correction_text(target, actual, info["duration"])},
        ]
        try:
            plan2, text2 = self._request(correction, "纠偏")
            validate_against_duration(plan2, info["duration"])
        except CloudError as exc:
            self.logger.warning("纠偏失败，沿用首轮方案: %s", exc)
            return plan, text

        actual2 = kept_duration(plan2)
        if within_target(actual2, target):
            self.logger.info("纠偏成功：目标 %.1fs / 实际 %.1fs", target, actual2)
            return plan2, text2

        self.logger.warning("纠偏后仍偏离：实际 %.1fs（目标 %.1fs），取更接近目标的一版", actual2, target)
        return (plan2, text2) if abs(actual2 - target) < abs(actual - target) else (plan, text)

    def _correction_text(self, target: float, actual: float, duration: float) -> str:
        diff = target - actual
        low = max(1.0, round(target * 0.9, 1))
        high = min(duration, round(target * 1.1, 1))
        if high < low:
            high = low
        return (
            f"【时长纠偏】上一版「保留」合计 {actual:.2f} 秒，目标 {target:.2f} 秒，"
            f"需要{'增加' if diff > 0 else '减少'} {abs(diff):.2f} 秒。\n"
            "只改 cut_list，其它字段保持原样；硬性要求：\n"
            f"1. 从 00:00:00 连续覆盖到 {duration:.2f} 秒，不留空档；\n"
            f"2. 「保留」段合计必须落在 {low:.2f}–{high:.2f} 秒；\n"
            "3. 必须通过调整保留/删除的边界来达成，不要写进 comment 当建议；\n"
            "4. 每个「保留」段仍给 ≤14 字字幕，保留段至少 2 段。\n"
            "直接输出完整 JSON。"
        )

    def _request(self, messages: list[dict[str, Any]], stage: str) -> tuple[Plan, str]:
        payload: dict[str, Any] = {
            "model": self.settings.llm_model,
            "messages": messages,
            "temperature": 0.2,
        }
        if self.settings.llm_json_mode:
            payload["response_format"] = {"type": "json_object"}

        last_error = ""
        for attempt in range(1, self.settings.llm_retries + 2):
            try:
                self.logger.info("调用云端模型 %s（%s，第 %d 次）", self.settings.llm_model, stage, attempt)
                data = self._post(payload)
                text = _extract_content(data)
                plan = parse_plan_json(text)
                usage = data.get("usage") or {}
                if usage:
                    self.logger.info("云端用量: %s", usage)
                self.trace.append({"stage": stage, "text": text})
                return plan, text
            except urllib.error.HTTPError as exc:
                detail = exc.read().decode("utf-8", errors="replace")[:500]
                last_error = f"HTTP {exc.code}: {detail}"
                if exc.code in (401, 403):
                    raise CloudError(f"鉴权失败，请检查 API Key。{last_error}") from exc
                self.logger.warning("云端返回错误，准备重试: %s", last_error)
            except (urllib.error.URLError, TimeoutError) as exc:
                last_error = f"网络错误: {exc}"
                self.logger.warning("云端连接失败，准备重试: %s", last_error)
            except PlanError as exc:
                last_error = f"方案不合规: {exc}"
                self.logger.warning("云端方案解析失败，准备重试: %s", last_error)

        raise CloudError(f"{stage}调用失败（已重试 {self.settings.llm_retries} 次）：{last_error}")


def _extract_content(data: dict) -> str:
    try:
        message = data["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError) as exc:
        raise CloudError(f"云端返回结构异常: {str(data)[:300]}") from exc
    if isinstance(message, str):
        return message
    if isinstance(message, list):  # 部分供应商返回分段内容
        parts = [p.get("text", "") for p in message if isinstance(p, dict)]
        return "\n".join(parts)
    raise CloudError("云端返回的 content 不是文本")
