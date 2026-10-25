"""全局配置：所有路径、导出参数、云端接口均可用 .env 覆盖。

设计原则：
- 代码里不硬编码密钥；
- Windows 默认指向 D:\\豆包\\数据，其它系统回退到项目内 data/；
- 所有目录在启动时自动创建，缺目录不会中断任务。
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent


def load_env_file(path: Path) -> None:
    """极简 .env 读取，避免额外依赖。已存在的环境变量优先。"""
    if not path.exists():
        return
    for raw in path.read_text(encoding="utf-8-sig").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, val = line.split("=", 1)
        key = key.strip()
        val = val.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = val


def _env(key: str, default: str = "") -> str:
    return os.environ.get(key, default)


def _env_int(key: str, default: int) -> int:
    try:
        return int(float(_env(key, str(default))))
    except ValueError:
        return default


def _env_float(key: str, default: float) -> float:
    try:
        return float(_env(key, str(default)))
    except ValueError:
        return default


def _env_bool(key: str, default: bool = False) -> bool:
    raw = _env(key, "").strip().lower()
    if not raw:
        return default
    return raw in {"1", "true", "yes", "on"}


def _default_data_root() -> str:
    if os.name == "nt":
        return r"D:\豆包\数据"
    return str(PROJECT_ROOT / "data")


@dataclass
class Settings:
    # 路径
    data_root: Path
    src_dir: Path
    out_dir: Path
    log_dir: Path
    archive_dir: Path
    task_dir: Path
    temp_dir: Path
    queue_db: Path

    # 云端大模型（OpenAI 兼容接口）
    llm_api_key: str
    llm_base_url: str
    llm_model: str
    llm_timeout: int
    llm_retries: int
    llm_json_mode: bool
    llm_vision: bool

    # 抽帧
    frame_count: int
    frame_width: int

    # 导出
    out_width: int
    out_height: int
    out_fps: int
    out_crf: int
    out_preset: str
    bgm_volume: float

    # 字幕
    subtitle_font: str
    subtitle_size: int
    subtitle_margin_v: int
    subtitle_fonts_dir: str

    def ensure_dirs(self) -> None:
        for d in (
            self.data_root,
            self.src_dir,
            self.out_dir,
            self.log_dir,
            self.archive_dir,
            self.task_dir,
            self.temp_dir,
        ):
            d.mkdir(parents=True, exist_ok=True)

    @property
    def cloud_ready(self) -> bool:
        return bool(self.llm_api_key and self.llm_base_url and self.llm_model)


_settings: Settings | None = None


def get_settings(refresh: bool = False) -> Settings:
    global _settings
    if _settings is not None and not refresh:
        return _settings

    load_env_file(PROJECT_ROOT / ".env")

    root = Path(_env("DATA_ROOT") or _default_data_root()).expanduser()
    src_dir = Path(_env("VIDEO_SRC_DIR") or (root / "视频素材")).expanduser()
    out_dir = Path(_env("VIDEO_OUT_DIR") or (root / "视频成片")).expanduser()
    log_dir = Path(_env("LOG_DIR") or (root / "日志")).expanduser()
    archive_dir = Path(_env("ARCHIVE_DIR") or (root / "归档")).expanduser()
    task_dir = Path(_env("TASK_DIR") or (root / "任务")).expanduser()
    queue_db = Path(_env("QUEUE_DB") or (task_dir / "tasks.db")).expanduser()
    temp_dir = Path(_env("TEMP_DIR") or (task_dir / "临时")).expanduser()

    frame_count = min(8, max(3, _env_int("FRAME_COUNT", 5)))

    default_font = "Microsoft YaHei" if os.name == "nt" else "Noto Sans CJK SC"
    default_fonts_dir = r"C:\Windows\Fonts" if os.name == "nt" else "/usr/share/fonts"

    _settings = Settings(
        data_root=root,
        src_dir=src_dir,
        out_dir=out_dir,
        log_dir=log_dir,
        archive_dir=archive_dir,
        task_dir=task_dir,
        temp_dir=temp_dir,
        queue_db=queue_db,
        llm_api_key=_env("LLM_API_KEY"),
        llm_base_url=_env("LLM_BASE_URL", "https://api.openai.com/v1").rstrip("/"),
        llm_model=_env("LLM_MODEL", "gpt-4o-mini"),
        llm_timeout=_env_int("LLM_TIMEOUT", 120),
        llm_retries=_env_int("LLM_MAX_RETRY", 2),
        llm_json_mode=_env_bool("LLM_JSON_MODE", True),
        llm_vision=_env_bool("LLM_VISION", True),
        frame_count=frame_count,
        frame_width=_env_int("FRAME_WIDTH", 720),
        out_width=_env_int("OUT_WIDTH", 1080),
        out_height=_env_int("OUT_HEIGHT", 1920),
        out_fps=_env_int("OUT_FPS", 30),
        out_crf=_env_int("OUT_CRF", 18),
        out_preset=_env("OUT_PRESET", "veryfast"),
        bgm_volume=_env_float("BGM_VOLUME", 0.25),
        subtitle_font=_env("SUBTITLE_FONT", default_font),
        subtitle_size=_env_int("SUBTITLE_SIZE", 68),
        subtitle_margin_v=_env_int("SUBTITLE_MARGIN_V", 0),
        subtitle_fonts_dir=_env("SUBTITLE_FONTS_DIR", default_fonts_dir),
    )
    _settings.ensure_dirs()
    return _settings
