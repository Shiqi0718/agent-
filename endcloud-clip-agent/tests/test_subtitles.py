"""字幕生成单元测试。"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from core.subtitles import SubtitleEvent, build_ass, build_srt  # noqa: E402


class SubtitleTest(unittest.TestCase):
    def setUp(self):
        self.events = [
            SubtitleEvent(0.0, 2.5, "第一行字幕"),
            SubtitleEvent(2.5, 6.0, "第二行\n带换行"),
        ]

    def test_ass_header_and_events(self):
        ass = build_ass(self.events, 1080, 1920, "Microsoft YaHei", 68)
        self.assertIn("PlayResX: 1080", ass)
        self.assertIn("PlayResY: 1920", ass)
        self.assertEqual(ass.count("Dialogue:"), 2)
        self.assertIn("0:00:00.00,0:00:02.50", ass)
        self.assertIn(r"第二行\N带换行", ass)

    def test_ass_escapes_braces(self):
        ass = build_ass([SubtitleEvent(0, 1, "花字{测试}")], 1080, 1920, "Microsoft YaHei", 68)
        self.assertNotIn("{测试}", ass)

    def test_srt(self):
        srt = build_srt(self.events)
        self.assertIn("00:00:00,000 --> 00:00:02,500", srt)
        self.assertIn("第一行字幕", srt)

    def test_empty_events(self):
        self.assertEqual(build_srt([]), "")
        self.assertEqual(build_ass([], 1080, 1920, "Arial", 40).count("Dialogue:"), 0)


if __name__ == "__main__":
    unittest.main()
