"""时长纠偏测试：用假的 _post 模拟两轮云端回复，不消耗真实 API 额度。"""

from __future__ import annotations

import json
import logging
import os
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import config  # noqa: E402
from core.llm import CloudPlanner  # noqa: E402
from core.plan import kept_duration  # noqa: E402

INFO = {"duration": 12.0, "width": 1280, "height": 720, "fps": 30.0, "has_audio": True}

# 首轮：只保留 6 秒（用户要 10 秒，必须纠偏）
BAD = {
    "hooks": "结果前置",
    "cut_list": [
        {"start": "00:00:00", "end": "00:00:02", "action": "保留", "subtitle": "一"},
        {"start": "00:00:02", "end": "00:00:04", "action": "删除"},
        {"start": "00:00:04", "end": "00:00:06", "action": "保留", "subtitle": "二"},
        {"start": "00:00:06", "end": "00:00:08", "action": "删除"},
        {"start": "00:00:08", "end": "00:00:10", "action": "保留", "subtitle": "三"},
        {"start": "00:00:10", "end": "00:00:12", "action": "删除"},
    ],
    "bgm_time": "00:00:02",
    "comment": "建议再留 4 秒（这句话本地不会执行）",
}

# 纠偏后：保留 9 秒，落在 10 秒目标的容差内
GOOD = {
    "hooks": "结果前置",
    "cut_list": [
        {"start": "00:00:00", "end": "00:00:05", "action": "保留", "subtitle": "一"},
        {"start": "00:00:05", "end": "00:00:07", "action": "删除"},
        {"start": "00:00:07", "end": "00:00:11", "action": "保留", "subtitle": "二"},
        {"start": "00:00:11", "end": "00:00:12", "action": "删除"},
    ],
    "bgm_time": "00:00:02",
    "comment": "已按目标时长收紧",
}


def _reply(plan: dict) -> dict:
    return {
        "choices": [{"message": {"content": json.dumps(plan, ensure_ascii=False)}}],
        "usage": {},
    }


class ScriptedPlanner(CloudPlanner):
    """按顺序返回预设回复，同时记录每次请求体。"""

    def __init__(self, settings, replies: list[dict]):
        super().__init__(settings, logging.getLogger("test_correction"))
        self.replies = list(replies)
        self.calls: list[dict] = []

    def _post(self, payload: dict) -> dict:
        self.calls.append(payload)
        if not self.replies:
            raise AssertionError("云端被调用次数超出预期")
        return self.replies.pop(0)


class CorrectionTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.env_backup = os.environ.copy()
        os.environ.update(
            {
                "DATA_ROOT": self.tmp.name,
                "LLM_API_KEY": "test-key",
                "LLM_BASE_URL": "https://example.com/v1",
                "LLM_MODEL": "test-model",
                "LLM_VISION": "false",
                "LLM_JSON_MODE": "true",
                "LLM_MAX_RETRY": "0",
            }
        )
        self.settings = config.get_settings(refresh=True)

    def tearDown(self):
        os.environ.clear()
        os.environ.update(self.env_backup)
        config._settings = None
        self.tmp.cleanup()

    def test_corrects_when_too_short(self):
        planner = ScriptedPlanner(self.settings, [_reply(BAD), _reply(GOOD)])
        plan, _ = planner.analyze("剪成10秒的高能卡点", INFO, [], target=10.0)

        self.assertEqual(len(planner.calls), 2)
        self.assertAlmostEqual(kept_duration(plan), 9.0)

        correction = planner.calls[1]["messages"]
        self.assertEqual(correction[-2]["role"], "assistant")
        self.assertEqual(correction[-1]["role"], "user")
        self.assertIn("纠偏", correction[-1]["content"])
        self.assertIn("10.00", correction[-1]["content"])

    def test_no_second_call_when_on_target(self):
        planner = ScriptedPlanner(self.settings, [_reply(GOOD)])
        plan, _ = planner.analyze("剪成9秒", INFO, [], target=9.0)

        self.assertEqual(len(planner.calls), 1)
        self.assertAlmostEqual(kept_duration(plan), 9.0)

    def test_keeps_closer_version_when_still_off(self):
        planner = ScriptedPlanner(self.settings, [_reply(BAD), _reply(BAD)])
        plan, _ = planner.analyze("剪成10秒", INFO, [], target=10.0)

        self.assertEqual(len(planner.calls), 2)
        self.assertAlmostEqual(kept_duration(plan), 6.0)

    def test_no_target_no_correction(self):
        planner = ScriptedPlanner(self.settings, [_reply(BAD)])
        plan, _ = planner.analyze("随便剪个高能版", INFO, [])

        self.assertEqual(len(planner.calls), 1)
        self.assertAlmostEqual(kept_duration(plan), 6.0)


if __name__ == "__main__":
    unittest.main()
