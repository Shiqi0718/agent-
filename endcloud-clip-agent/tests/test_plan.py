"""方案解析与时间轴换算的单元测试（不依赖云端、不依赖 FFmpeg）。"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from core.plan import (  # noqa: E402
    PlanError,
    build_timeline,
    guess_target_duration,
    kept_duration,
    mock_plan,
    parse_plan_json,
    save_plan,
    within_target,
)
from core.timeutil import TimeFormatError, parse_time, parse_range  # noqa: E402

VALID = """
模型说明文字可以忽略。
```json
{
  "hooks": "前 3 秒用结果反差",
  "cut_list": [
    {"start": "00:00:00", "end": "00:00:05", "action": "保留", "subtitle": "先看结果"},
    {"start": "00:00:05", "end": "00:00:08", "action": "删除", "subtitle": ""},
    {"start": "00:00:08", "end": "00:00:12", "action": "保留", "subtitle": "结尾留钩子"}
  ],
  "bgm_time": "00:00:02,00:00:09",
  "comment": "中段可以再快一点"
}
```
"""


class TimeUtilTest(unittest.TestCase):
    def test_parse_variants(self):
        self.assertAlmostEqual(parse_time("00:00:05"), 5.0)
        self.assertAlmostEqual(parse_time("00:01:05.500"), 65.5)
        self.assertAlmostEqual(parse_time("1:05"), 65.0)
        self.assertAlmostEqual(parse_time(3.25), 3.25)

    def test_parse_range(self):
        self.assertEqual(parse_range("00:00:01-00:00:04"), (1.0, 4.0))
        self.assertEqual(parse_range(" 1.5 - 4 "), (1.5, 4.0))
        with self.assertRaises(TimeFormatError):
            parse_range("5-2")

    def test_bad_time(self):
        with self.assertRaises(TimeFormatError):
            parse_time("abc")


class PlanParseTest(unittest.TestCase):
    def test_valid_with_fence(self):
        plan = parse_plan_json(VALID)
        self.assertEqual(plan.hooks, "前 3 秒用结果反差")
        self.assertEqual(len(plan.cuts), 3)
        self.assertEqual(plan.cuts[1].action, "删除")

    def test_timeline(self):
        plan = parse_plan_json(VALID)
        segments, total = build_timeline(plan)
        self.assertEqual(len(segments), 2)
        self.assertAlmostEqual(segments[0].out_start, 0.0)
        self.assertAlmostEqual(segments[0].out_end, 5.0)
        self.assertAlmostEqual(segments[1].out_start, 5.0)
        self.assertAlmostEqual(total, 9.0)

    def test_overlap_clamped(self):
        plan = parse_plan_json(
            '{"hooks":"","cut_list":['
            '{"start":"0","end":"5","action":"保留"},'
            '{"start":"3","end":"8","action":"保留"}],'
            '"bgm_time":"","comment":""}'
        )
        segments, total = build_timeline(plan)
        self.assertEqual(len(segments), 2)
        self.assertAlmostEqual(segments[1].src_start, 5.0)
        self.assertAlmostEqual(total, 8.0)

    def test_prose_rejected(self):
        with self.assertRaises(PlanError):
            parse_plan_json("我建议你前 3 秒放最刺激的画面，然后卡点。")

    def test_missing_field(self):
        with self.assertRaises(PlanError):
            parse_plan_json('{"hooks":"","cut_list":[]}')

    def test_bad_action(self):
        with self.assertRaises(PlanError):
            parse_plan_json(
                '{"hooks":"","cut_list":[{"start":"0","end":"3","action":"也许","subtitle":""}],'
                '"bgm_time":"","comment":""}'
            )

    def test_no_keep(self):
        with self.assertRaises(PlanError):
            parse_plan_json(
                '{"hooks":"","cut_list":[{"start":"0","end":"3","action":"删除","subtitle":""}],'
                '"bgm_time":"","comment":""}'
            )

    def test_save_and_reload(self):
        import tempfile

        plan = parse_plan_json(VALID)
        with tempfile.TemporaryDirectory() as tmp:
            path = save_plan(plan, Path(tmp) / "plan.json")
            self.assertTrue(path.exists())
            again = parse_plan_json(path.read_text(encoding="utf-8"))
            self.assertAlmostEqual(again.cuts[0].end, 5.0)

    def test_mock_plan(self):
        plan = mock_plan(10.0, "测试")
        segments, total = build_timeline(plan)
        self.assertGreaterEqual(len(segments), 1)
        self.assertGreater(total, 0)


class TargetDurationTest(unittest.TestCase):
    def test_strong_cue(self):
        self.assertAlmostEqual(guess_target_duration("剪成10秒左右的高能卡点"), 10.0)
        self.assertAlmostEqual(guess_target_duration("总时长控制在 20 秒"), 20.0)

    def test_ignore_hook_and_rhythm(self):
        # 钩子秒数、节奏秒数不是目标时长，不能被误识别
        self.assertIsNone(guess_target_duration("前3秒用最强画面做钩子"))
        self.assertIsNone(guess_target_duration("每8-12秒必须有一次变化"))

    def test_range_midpoint(self):
        self.assertAlmostEqual(guess_target_duration("剪成 8-12 秒的卡点片"), 10.0)

    def test_chinese_minutes(self):
        self.assertAlmostEqual(guess_target_duration("做成两分钟的口播"), 120.0)

    def test_weak_cue(self):
        self.assertAlmostEqual(guess_target_duration("这条片时长大约 45 秒"), 45.0)

    def test_kept_duration_overlap(self):
        plan = parse_plan_json(
            '{"hooks":"","cut_list":['
            '{"start":"0","end":"5","action":"保留"},'
            '{"start":"3","end":"8","action":"保留"}],'
            '"bgm_time":"","comment":""}'
        )
        self.assertAlmostEqual(kept_duration(plan), 8.0)

    def test_within_target(self):
        self.assertTrue(within_target(9.0, 10.0))
        self.assertTrue(within_target(11.0, 10.0))
        self.assertFalse(within_target(6.0, 10.0))
        # 绝对容差兜底：短片上 1 秒内都算过
        self.assertTrue(within_target(2.5, 3.0))


if __name__ == "__main__":
    unittest.main()
