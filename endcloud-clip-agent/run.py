#!/usr/bin/env python3
"""端云协同 AI 剪辑 Agent —— 命令行入口。

本地负责：读素材、抽帧、FFmpeg 剪辑、队列、归档、导出。
云端负责：DeepSeek Flash 看懂关键帧 + 需求，返回结构化 JSON 剪辑方案。

常用示例：
  python run.py doctor
  python run.py scan   --dir "D:\\Trae work\\Trae work处理文件夹\\视频素材"
  python run.py auto   --src "D:\\...\\视频素材\\a.mp4" --need "剪成 15 秒高能卡点"
  python run.py manual --src "D:\\...\\视频素材\\a.mp4" --keep 00:00:01-00:00:05 00:00:08-00:00:12
  python run.py render --src "D:\\...\\a.mp4" --plan "D:\\...\\方案.json"
"""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
from datetime import datetime
from pathlib import Path

from config import get_settings
from core import media, plan as planmod, subtitles
from core.editor import render_timeline, unique_output
from core.llm import CloudError, CloudPlanner
from core.logger import setup_logger
from core.media import FFmpegError
from core.queue import TaskQueue
from core.timeutil import TimeFormatError, fmt_clock, parse_range


def _ctx():
    settings = get_settings()
    logger = setup_logger(settings.log_dir)
    return settings, logger


def _src(path_text: str) -> Path:
    path = Path(path_text).expanduser()
    if not path.exists():
        raise FileNotFoundError(f"素材不存在: {path}")
    if not path.is_file():
        raise FileNotFoundError(f"素材不是文件: {path}")
    return path


def _report(result: dict) -> None:
    size_mb = result.get("size_bytes", 0) / 1024 / 1024
    print("\n[OK] 成片完成")
    print(f"  成片: {result['output']}")
    print(f"  说明: {result.get('notes', '—')}")
    if result.get("plan"):
        print(f"  方案: {result['plan']}")
    if result.get("bgm"):
        print(f"  BGM: {result['bgm']}")
    print(
        f"  时长: {result['duration']}s | 片段: {result['segments']} | "
        f"字幕: {result['subtitles']} | {size_mb:.1f}MB"
    )
    print("\n--- JSON ---")
    print(json.dumps(result, ensure_ascii=False, indent=2))


# --------------------------------------------------------------------------- 命令


def cmd_doctor(args: argparse.Namespace) -> int:
    settings, logger = _ctx()
    ok, message = media.environment_ready()
    print(f"FFmpeg: {'OK' if ok else 'FAIL'} | {message}")
    if ok:
        version = subprocess.run(
            ["ffmpeg", "-version"], capture_output=True, text=True, errors="replace"
        ).stdout.splitlines()[0]
        print(f"  版本: {version}")

    for label, path in (
        ("素材目录", settings.src_dir),
        ("成片目录", settings.out_dir),
        ("日志目录", settings.log_dir),
        ("抽帧缓存", settings.frame_cache_dir),
        ("切片临时", settings.temp_dir),
        ("归档目录", settings.archive_dir),
        ("BGM 目录", settings.bgm_dir),
        ("任务目录", settings.task_dir),
    ):
        try:
            probe = path / ".write_test"
            probe.write_text("ok", encoding="utf-8")
            probe.unlink()
            print(f"{label}: OK | {path}")
        except OSError as exc:
            print(f"{label}: FAIL | {path} | {exc}")

    cloud = "OK" if settings.cloud_ready else "未配置（可用 --mock 或手动模式）"
    print(f"云端: {cloud} | {settings.llm_base_url} / {settings.llm_model}")
    print(f"视觉识图: {'开启' if settings.llm_vision else '关闭'} | max_tokens={settings.llm_max_tokens}")
    print(
        f"关键帧: {settings.frame_count} 张 / 宽 {settings.frame_width}px / "
        f"单张上限 {settings.frame_max_kb}KB"
    )
    print(f"字幕字体: {settings.subtitle_font} | 字体目录: {settings.subtitle_fonts_dir}")
    print(f"导出: {settings.out_width}x{settings.out_height} @ {settings.out_fps}fps CRF{settings.out_crf}")
    logger.info("doctor 检查完成")
    return 0 if ok else 1


def cmd_info(args: argparse.Namespace) -> int:
    _, _ = _ctx()
    info = media.video_info(_src(args.src))
    print(json.dumps(info, ensure_ascii=False, indent=2))
    return 0


def cmd_frames(args: argparse.Namespace) -> int:
    settings, logger = _ctx()
    src = _src(args.src)
    info = media.video_info(src)
    out_dir = (
        Path(args.out).expanduser()
        if args.out
        else settings.frame_cache_dir / f"{src.stem}_{datetime.now():%Y%m%d_%H%M%S}"
    )
    frames = media.extract_frames(
        src,
        out_dir,
        count=args.count or settings.frame_count,
        width=settings.frame_width,
        quality=settings.frame_quality,
        max_kb=settings.frame_max_kb,
        logger=logger,
        info=info,
    )
    print(f"[OK] 抽帧 {len(frames)} 张 → {out_dir}")
    for frame in frames:
        kb = frame.path.stat().st_size / 1024
        print(f"  {frame.clock}  {frame.path.name}  {kb:.0f}KB")
    return 0


def cmd_scan(args: argparse.Namespace) -> int:
    """盘点素材目录：只看文件名，不读内容、不上传。"""
    settings, logger = _ctx()
    root = Path(args.dir).expanduser() if args.dir else settings.src_dir
    result = media.scan_media(root)
    total = sum(len(items) for items in result.values())
    print(f"[OK] 素材盘点: {root} | 共 {total} 个文件")
    for label, key in (("视频", "video"), ("音频", "audio"), ("图片", "image")):
        items = result[key]
        print(f"\n{label}（{len(items)}）:")
        for item in items[: args.limit] if args.limit else items:
            print(f"  {item}")
        if args.limit and len(items) > args.limit:
            print(f"  …… 其余 {len(items) - args.limit} 个已省略")
    logger.info("素材盘点完成: %s", root)
    return 0


def _make_plan(settings, logger, src: Path, need: str, mock: bool, clean_frames: bool = False):
    """抽关键帧 → 云端出方案。原始视频全程留在本地，只上传压缩后的少量截图。"""
    info = media.video_info(src)
    frame_dir = settings.frame_cache_dir / f"{src.stem}_{datetime.now():%Y%m%d_%H%M%S}"
    frames = media.extract_frames(
        src,
        frame_dir,
        count=settings.frame_count,
        width=settings.frame_width,
        quality=settings.frame_quality,
        max_kb=settings.frame_max_kb,
        logger=logger,
        info=info,
    )
    uploaded_kb = sum(f.path.stat().st_size for f in frames) / 1024
    logger.info("已抽取 %d 张关键帧（合计 %.0fKB）: %s", len(frames), uploaded_kb, frame_dir)

    try:
        if mock:
            plan_obj = planmod.mock_plan(info["duration"], need)
            logger.info("使用本地模拟方案（未调用云端）")
            return plan_obj, info

        if not settings.cloud_ready:
            raise CloudError("未配置云端 API：请复制 .env.example 为 .env 并填写 LLM_API_KEY，或加 --mock")

        target = planmod.guess_target_duration(need)
        if target is not None:
            if target > info["duration"] - 0.5:
                logger.warning(
                    "目标 %.1fs 超过素材 %.1fs，按素材上限处理（不做慢放）；要更长得补素材或拆条",
                    target,
                    info["duration"],
                )
                target = info["duration"]
            logger.info("识别到目标时长 %.1fs", target)

        planner = CloudPlanner(settings, logger)
        plan_obj, _ = planner.analyze(need, info, frames, target=target)

        raw_dir = settings.archive_dir / "方案"
        raw_dir.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        single = len(planner.trace) == 1
        for index, item in enumerate(planner.trace, 1):
            suffix = "raw" if single else f"raw{index}"
            raw_path = raw_dir / f"{src.stem}_{stamp}_{suffix}.txt"
            raw_path.write_text(item["text"], encoding="utf-8")
            logger.info("云端原始回复（%s）已保存: %s", item["stage"], raw_path)
        return plan_obj, info
    finally:
        # 可选清理：默认保留抽帧方便复核，加 --clean-frames 才删
        if clean_frames:
            shutil.rmtree(frame_dir, ignore_errors=True)
            logger.info("已清理抽帧缓存: %s", frame_dir)


def _render_plan(settings, logger, src: Path, plan_obj, need: str, name: str, bgm: str | None, keep_temp: bool) -> dict:
    info = media.video_info(src)
    planmod.validate_against_duration(plan_obj, info["duration"])
    segments, total = planmod.build_timeline(plan_obj)
    events = subtitles.events_from_segments(segments)

    bgm_path = _src(bgm) if bgm else None
    out_path = unique_output(settings.out_dir, name or src.stem)
    result = render_timeline(
        src,
        segments,
        total,
        events,
        out_path,
        settings,
        logger,
        bgm=bgm_path,
        plan=plan_obj,
        need=need,
        keep_temp=keep_temp,
    )

    plan_path = settings.archive_dir / "方案" / f"{out_path.stem}_plan.json"
    planmod.save_plan(plan_obj, plan_path)
    result["plan"] = str(plan_path)
    return result


def _render_manual(settings, logger, src: Path, ranges, subtitle: str, name: str, bgm: str | None, keep_temp: bool) -> dict:
    info = media.video_info(src)
    segments: list[planmod.Segment] = []
    offset = 0.0
    for start, end in ranges:
        if start >= info["duration"]:
            raise planmod.PlanError(f"起点 {fmt_clock(start)} 超出视频时长 {fmt_clock(info['duration'])}")
        end = min(end, info["duration"])
        if end - start <= 0.05:
            continue
        segments.append(
            planmod.Segment(src_start=start, src_end=end, out_start=offset, out_end=offset + (end - start))
        )
        offset += end - start
    if not segments:
        raise planmod.PlanError("没有有效的保留片段")

    events = [
        subtitles.SubtitleEvent(0.0, offset, subtitle)
    ] if subtitle else []

    bgm_path = _src(bgm) if bgm else None
    out_path = unique_output(settings.out_dir, name or src.stem)
    return render_timeline(
        src,
        segments,
        offset,
        events,
        out_path,
        settings,
        logger,
        bgm=bgm_path,
        need="（手动模式：用户直接给时间戳，未调用云端）",
        keep_temp=keep_temp,
    )


def cmd_plan(args: argparse.Namespace) -> int:
    settings, logger = _ctx()
    src = _src(args.src)
    plan_obj, _ = _make_plan(settings, logger, src, args.need, args.mock, args.clean_frames)
    out_path = settings.archive_dir / "方案" / f"{src.stem}_{datetime.now():%Y%m%d_%H%M%S}_plan.json"
    planmod.save_plan(plan_obj, out_path)
    segments, total = planmod.build_timeline(plan_obj)
    print(f"[OK] 方案已保存: {out_path}")
    print(f"保留 {len(segments)} 段，总时长 {total:.2f}s，钩子: {plan_obj.hooks or '—'}")
    print(json.dumps(json.loads(out_path.read_text(encoding='utf-8')), ensure_ascii=False, indent=2))
    return 0


def cmd_render(args: argparse.Namespace) -> int:
    settings, logger = _ctx()
    src = _src(args.src)
    plan_obj = planmod.load_plan(Path(args.plan).expanduser())
    result = _render_plan(settings, logger, src, plan_obj, args.need or "", args.name, args.bgm, args.keep_temp)
    _report(result)
    return 0


def cmd_auto(args: argparse.Namespace) -> int:
    settings, logger = _ctx()
    src = _src(args.src)
    plan_obj, _ = _make_plan(settings, logger, src, args.need, args.mock, args.clean_frames)
    result = _render_plan(settings, logger, src, plan_obj, args.need, args.name, args.bgm, args.keep_temp)
    _report(result)
    return 0


def cmd_manual(args: argparse.Namespace) -> int:
    settings, logger = _ctx()
    src = _src(args.src)
    raw_ranges: list[str] = []
    for chunk in args.keep:
        raw_ranges.extend(part for part in chunk.split(",") if part.strip())
    ranges = [parse_range(item) for item in raw_ranges]
    result = _render_manual(settings, logger, src, ranges, args.subtitle or "", args.name, args.bgm, args.keep_temp)
    _report(result)
    return 0


def _execute_payload(settings, logger, payload: dict) -> dict:
    src = _src(payload["src"])
    if payload.get("mode") == "manual":
        ranges = [parse_range(item) for item in payload.get("keep", [])]
        return _render_manual(
            settings,
            logger,
            src,
            ranges,
            payload.get("subtitle", ""),
            payload.get("name", ""),
            payload.get("bgm"),
            bool(payload.get("keep_temp")),
        )
    plan_obj, _ = _make_plan(
        settings,
        logger,
        src,
        payload.get("need", ""),
        bool(payload.get("mock")),
        bool(payload.get("clean_frames")),
    )
    return _render_plan(
        settings,
        logger,
        src,
        plan_obj,
        payload.get("need", ""),
        payload.get("name", ""),
        payload.get("bgm"),
        bool(payload.get("keep_temp")),
    )


def cmd_queue(args: argparse.Namespace) -> int:
    settings, logger = _ctx()
    queue = TaskQueue(settings.queue_db)

    if args.action == "add":
        if args.mode == "manual" and not args.keep:
            raise planmod.PlanError("manual 任务必须提供 --keep")
        payload = {
            "mode": args.mode,
            "src": str(_src(args.src)),
            "need": args.need or "",
            "keep": args.keep or [],
            "subtitle": args.subtitle or "",
            "bgm": args.bgm,
            "name": args.name or "",
            "mock": args.mock,
            "clean_frames": args.clean_frames,
        }
        task_id = queue.add(args.mode, payload)
        print(f"[OK] 任务已入队: #{task_id}（{args.mode}）")
        return 0

    if args.action == "list":
        rows = queue.list(status=args.status)
        if not rows:
            print("队列为空")
            return 0
        print(f"{'ID':<5}{'状态':<10}{'模式':<8}{'重试':<6}{'素材':<36}创建时间")
        for row in rows:
            print(
                f"{row['id']:<5}{row['status']:<10}{row['mode']:<8}{row['retry_count']:<6}"
                f"{Path(row['payload'].get('src', '')).name:<36}{row['created_at']}"
            )
        return 0

    if args.action == "retry":
        if args.all:
            count = queue.retry_all_failed()
            print(f"[OK] 已重新排队 {count} 个失败任务")
            return 0
        if args.id is None:
            raise planmod.PlanError("retry 需要 --id，或用 --all 重置全部失败任务")
        if not queue.retry(args.id):
            print(f"[SKIP] 任务 #{args.id} 不是 failed 状态，无需重试")
            return 0
        print(f"[OK] 任务 #{args.id} 已重新排队")
        return 0

    # run
    done = failed = 0
    for _ in range(args.limit):
        task = queue.next_pending()
        if task is None:
            print("没有待执行任务")
            break
        queue.mark(task["id"], "running")
        logger.info("开始执行任务 #%d（重试 %d 次）", task["id"], task["retry_count"])
        try:
            result = _execute_payload(settings, logger, task["payload"])
            queue.mark(task["id"], "done", result=result)
            done += 1
            print(f"[OK] 任务 #{task['id']} → {result['output']}")
        except Exception as exc:  # 单任务失败不阻塞队列
            failed += 1
            if task["retry_count"] < args.retry:
                queue.retry(task["id"])
                logger.exception("任务 #%d 失败，已重新排队（第 %d 次重试）", task["id"], task["retry_count"] + 1)
                print(f"[RETRY] 任务 #{task['id']}: {exc}（将重试）")
            else:
                queue.mark(task["id"], "failed", error=str(exc))
                logger.exception("任务 #%d 失败", task["id"])
                print(f"[FAIL] 任务 #{task['id']}: {exc}")
    print(f"本轮完成 {done} 个，失败 {failed} 个")
    return 0


# --------------------------------------------------------------------------- 入口


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="run.py",
        description="端云协同 AI 剪辑 Agent：云端出方案，本地 FFmpeg 执行",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("doctor", help="检查 FFmpeg、目录、云端配置").set_defaults(func=cmd_doctor)

    p = sub.add_parser("info", help="读取素材信息")
    p.add_argument("--src", required=True)
    p.set_defaults(func=cmd_info)

    p = sub.add_parser("scan", help="盘点素材目录（视频/音频/图片）")
    p.add_argument("--dir", help="默认取 .env 的 VIDEO_SRC_DIR")
    p.add_argument("--limit", type=int, default=0, help="每类最多显示多少个，0 为全部")
    p.set_defaults(func=cmd_scan)

    p = sub.add_parser("frames", help="只抽关键帧，不上传")
    p.add_argument("--src", required=True)
    p.add_argument("--count", type=int, default=0, help="3–8 张，默认取 .env 的 FRAME_COUNT")
    p.add_argument("--out")
    p.set_defaults(func=cmd_frames)

    p = sub.add_parser("plan", help="调用云端（或 --mock）生成方案 JSON，不渲染")
    p.add_argument("--src", required=True)
    p.add_argument("--need", required=True)
    p.add_argument("--mock", action="store_true", help="不调用云端，生成模拟方案")
    p.add_argument("--clean-frames", action="store_true", help="出方案后清理抽帧缓存")
    p.set_defaults(func=cmd_plan)

    p = sub.add_parser("render", help="用现成方案 JSON 离线渲染")
    p.add_argument("--src", required=True)
    p.add_argument("--plan", required=True)
    p.add_argument("--bgm")
    p.add_argument("--name", help="成片主题名，默认取素材文件名")
    p.add_argument("--need", default="")
    p.add_argument("--keep-temp", action="store_true")
    p.set_defaults(func=cmd_render)

    p = sub.add_parser("auto", help="完整流程：抽帧 → 云端方案 → 自动剪辑")
    p.add_argument("--src", required=True)
    p.add_argument("--need", required=True)
    p.add_argument("--bgm")
    p.add_argument("--name")
    p.add_argument("--mock", action="store_true")
    p.add_argument("--keep-temp", action="store_true")
    p.add_argument("--clean-frames", action="store_true", help="出方案后清理抽帧缓存")
    p.set_defaults(func=cmd_auto)

    p = sub.add_parser("manual", help="手动模式：直接给时间戳，跳过云端")
    p.add_argument("--src", required=True)
    p.add_argument("--keep", nargs="+", required=True, help='如 "00:00:01-00:00:05" 可传多个')
    p.add_argument("--subtitle", default="", help="可选：全片字幕文本")
    p.add_argument("--bgm")
    p.add_argument("--name")
    p.add_argument("--keep-temp", action="store_true")
    p.set_defaults(func=cmd_manual)

    q = sub.add_parser("queue", help="本地任务队列")
    qsub = q.add_subparsers(dest="action", required=True)

    qa = qsub.add_parser("add", help="加入队列")
    qa.add_argument("--mode", choices=["auto", "manual"], required=True)
    qa.add_argument("--src", required=True)
    qa.add_argument("--need", default="")
    qa.add_argument("--keep", nargs="*", default=[])
    qa.add_argument("--subtitle", default="")
    qa.add_argument("--bgm")
    qa.add_argument("--name")
    qa.add_argument("--mock", action="store_true")
    qa.add_argument("--clean-frames", action="store_true")
    qa.set_defaults(func=cmd_queue)

    ql = qsub.add_parser("list", help="查看队列")
    ql.add_argument("--status", choices=["pending", "running", "done", "failed"])
    ql.set_defaults(func=cmd_queue)

    qr = qsub.add_parser("retry", help="把失败任务重新排队")
    qr.add_argument("--id", type=int, help="任务 ID")
    qr.add_argument("--all", action="store_true", help="重置全部失败任务")
    qr.set_defaults(func=cmd_queue)

    qrun = qsub.add_parser("run", help="顺序执行待处理任务（断网也可跑手动任务）")
    qrun.add_argument("--limit", type=int, default=10)
    qrun.add_argument("--retry", type=int, default=1, help="单个任务失败后最多重试几次")
    qrun.set_defaults(func=cmd_queue)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return args.func(args)
    except (planmod.PlanError, TimeFormatError, FFmpegError, CloudError, FileNotFoundError) as exc:
        print(f"\n[ERROR] {exc}", file=sys.stderr)
        try:
            settings, logger = _ctx()
            logger.error("执行失败: %s", exc)
        except Exception:
            pass
        return 1
    except KeyboardInterrupt:
        print("\n已中断", file=sys.stderr)
        return 130


if __name__ == "__main__":
    sys.exit(main())
