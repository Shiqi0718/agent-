"""端到端测试：真实调用 FFmpeg 出片（手动模式 / 方案渲染 / 模拟云端）。

第一次运行会自动生成 12 秒测试素材。
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SAMPLE = ROOT / "tests" / "out" / "sample.mp4"


class EndToEndTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if shutil.which("ffmpeg") is None or shutil.which("ffprobe") is None:
            raise unittest.SkipTest("需要 FFmpeg")
        if not SAMPLE.exists():
            subprocess.run(
                [sys.executable, str(ROOT / "tests" / "make_sample.py"), "--out", str(SAMPLE)],
                check=True,
                cwd=ROOT,
            )
        cls.tmp = Path(tempfile.mkdtemp(prefix="clip_agent_test_"))
        cls.env = os.environ.copy()
        cls.env.update(
            {
                "DATA_ROOT": str(cls.tmp / "数据"),
                "OUT_WIDTH": "480",
                "OUT_HEIGHT": "854",
                "OUT_PRESET": "ultrafast",
                "OUT_CRF": "30",
                "OUT_FPS": "24",
                "FRAME_COUNT": "3",
                "FRAME_MAX_KB": "180",
                "SUBTITLE_SIZE": "32",
                "LLM_VISION": "false",
                "LLM_MAX_TOKENS": "1024",
                "LLM_API_KEY": "",
            }
        )

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def run_cli(self, *args: str) -> subprocess.CompletedProcess:
        return subprocess.run(
            [sys.executable, str(ROOT / "run.py"), *args],
            cwd=ROOT,
            env=self.env,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
        )

    def output_files(self) -> list[Path]:
        out_dir = self.tmp / "数据" / "视频成片"
        return sorted(out_dir.glob("*.mp4"))

    def probe_duration(self, path: Path) -> float:
        proc = subprocess.run(
            [
                "ffprobe",
                "-v",
                "error",
                "-print_format",
                "json",
                "-show_format",
                str(path),
            ],
            capture_output=True,
            text=True,
        )
        return float(json.loads(proc.stdout)["format"]["duration"])

    def test_manual_mode(self):
        before = set(self.output_files())
        proc = self.run_cli(
            "manual",
            "--src",
            str(SAMPLE),
            "--keep",
            "00:00:01-00:00:04",
            "00:00:06-00:00:09",
            "--subtitle",
            "端到端测试字幕",
            "--name",
            "e2e_manual",
        )
        self.assertEqual(proc.returncode, 0, proc.stderr)
        new_files = [p for p in self.output_files() if p not in before]
        self.assertEqual(len(new_files), 1, proc.stdout)
        out = new_files[0]
        self.assertGreater(out.stat().st_size, 10_000)
        self.assertAlmostEqual(self.probe_duration(out), 6.0, delta=1.0)
        self.assertTrue((out.parent / f"{out.stem}.srt").exists())
        self.assertTrue((out.parent / f"{out.stem}_notes.md").exists())

    def test_render_from_plan(self):
        plan_path = self.tmp / "plan.json"
        plan_path.write_text(
            json.dumps(
                {
                    "hooks": "结果前置",
                    "overall_suggest": "开场直接给结果，中段删掉等待",
                    "cut_list": [
                        {"start": "00:00:00", "end": "00:00:03", "action": "keep", "subtitle": "开头"},
                        {"start": "00:00:03", "end": "00:00:06", "action": "delete"},
                        {"start": "00:00:06", "end": "00:00:09", "action": "keep", "subtitle": "结尾"},
                    ],
                    "bgm_plan": {"bgm_name": "测试 BGM", "beat_points": ["00:00:02"]},
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        proc = self.run_cli(
            "render",
            "--src",
            str(SAMPLE),
            "--plan",
            str(plan_path),
            "--name",
            "e2e_plan",
        )
        self.assertEqual(proc.returncode, 0, proc.stderr)
        # 日期随运行日变化，用 glob 兜底
        matches = sorted((self.tmp / "数据" / "视频成片").glob("*_e2e_plan_v1.mp4"))
        self.assertTrue(matches, proc.stdout)
        self.assertAlmostEqual(self.probe_duration(matches[0]), 6.0, delta=1.0)
        self.assertTrue((self.tmp / "数据" / "归档" / "方案").exists())

    def test_auto_mock(self):
        proc = self.run_cli(
            "auto",
            "--src",
            str(SAMPLE),
            "--need",
            "剪成高能卡点",
            "--mock",
            "--name",
            "e2e_auto",
        )
        self.assertEqual(proc.returncode, 0, proc.stderr)
        matches = sorted((self.tmp / "数据" / "视频成片").glob("*_e2e_auto_v1.mp4"))
        self.assertTrue(matches)
        self.assertGreater(matches[0].stat().st_size, 10_000)

    def test_queue_flow(self):
        proc = self.run_cli(
            "queue",
            "add",
            "--mode",
            "manual",
            "--src",
            str(SAMPLE),
            "--keep",
            "00:00:02-00:00:05",
            "--name",
            "e2e_queue",
        )
        self.assertEqual(proc.returncode, 0, proc.stderr)
        proc = self.run_cli("queue", "run", "--limit", "3")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        matches = sorted((self.tmp / "数据" / "视频成片").glob("*_e2e_queue_v1.mp4"))
        self.assertTrue(matches, proc.stdout)


if __name__ == "__main__":
    unittest.main()
