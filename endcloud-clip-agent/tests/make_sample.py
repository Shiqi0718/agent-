"""生成测试素材：12 秒彩条 + 正弦音，纯 FFmpeg，无需外部文件。"""

from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def main() -> int:
    parser = argparse.ArgumentParser(description="生成测试视频")
    parser.add_argument("--out", default=str(Path(__file__).parent / "out" / "sample.mp4"))
    parser.add_argument("--duration", type=int, default=12)
    args = parser.parse_args()

    if shutil.which("ffmpeg") is None:
        print("需要 FFmpeg，请先安装并加入 PATH", file=sys.stderr)
        return 1

    out = Path(args.out).expanduser()
    out.parent.mkdir(parents=True, exist_ok=True)
    cmd = [
        "ffmpeg",
        "-hide_banner",
        "-y",
        "-f",
        "lavfi",
        "-i",
        f"testsrc2=size=1280x720:rate=30:duration={args.duration}",
        "-f",
        "lavfi",
        "-i",
        f"sine=frequency=440:sample_rate=48000:duration={args.duration}",
        "-c:v",
        "libx264",
        "-preset",
        "ultrafast",
        "-pix_fmt",
        "yuv420p",
        "-c:a",
        "aac",
        "-b:a",
        "128k",
        "-shortest",
        str(out),
    ]
    subprocess.run(cmd, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    print(f"[OK] 测试素材: {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
