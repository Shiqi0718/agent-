"""云端规划器：只上传「文字需求 + 少量关键帧」，只接收结构化 JSON 方案。"""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from typing import Any

from config import Settings

from .media import Frame
from .plan import Plan, PlanError, parse_plan_json

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
4. 至少保留一个「保留」段，总保留时长不超过源视频时长；
5. 时间格式 HH:MM:SS 或 HH:MM:SS.mmm，且必须落在视频时长内；
6. 不要返回散文式方案，不要解释字段含义。"""


class CloudError(RuntimeError):
    """云端接口不可用、鉴权失败或返回内容不合规。"""


class CloudPlanner:
    def __init__(self, settings: Settings, logger):
        self.settings = settings
        self.logger = logger

    def _endpoint(self) -> str:
        base = self.settings.llm_base_url.rstrip("/")
        if base.endswith("/chat/completions"):
            return base
        return f"{base}/chat/completions"

    def _build_messages(self, need: str, info: dict, frames: list[Frame]) -> list[dict[str, Any]]:
        text = (
            f"【用户需求】{need}\n"
            f"【素材信息】时长 {info['duration']} 秒，{info['width']}x{info['height']}，"
            f"{info['fps']} fps，{'有' if info['has_audio'] else '无'}音轨\n"
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

    def analyze(self, need: str, info: dict, frames: list[Frame]) -> tuple[Plan, str]:
        if not self.settings.cloud_ready:
            raise CloudError("未配置云端 API：请在 .env 填写 LLM_API_KEY / LLM_BASE_URL / LLM_MODEL")

        payload: dict[str, Any] = {
            "model": self.settings.llm_model,
            "messages": self._build_messages(need, info, frames),
            "temperature": 0.2,
        }
        if self.settings.llm_json_mode:
            payload["response_format"] = {"type": "json_object"}

        last_error = ""
        for attempt in range(1, self.settings.llm_retries + 2):
            try:
                self.logger.info("调用云端模型 %s（第 %d 次）", self.settings.llm_model, attempt)
                data = self._post(payload)
                text = _extract_content(data)
                plan = parse_plan_json(text)
                usage = data.get("usage") or {}
                if usage:
                    self.logger.info("云端用量: %s", usage)
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

        raise CloudError(f"云端调用失败（已重试 {self.settings.llm_retries} 次）：{last_error}")


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
