# 端云协同 AI 剪辑 Agent

自用的**本地体力剪辑 + 云端方案设计**工具。原始视频始终留在本地，云端只拿到「你的文字需求 + 3–8 张关键帧」，返回结构化 JSON 剪辑方案，本地用 FFmpeg 执行。

- 平台：Windows（代码本身跨平台，Linux/macOS 也能跑）
- 依赖：Python 3.10+、FFmpeg（含 ffprobe）
- 无重型框架：只用了 Python 标准库 + FFmpeg 命令行

## 架构

```
                 ┌──────────────── 本地（体力） ────────────────┐
素材 D:\豆包\数据\视频素材
                 │ ffprobe 读信息 → 抽 3~8 张关键帧            │
                 │        │                                    │
                 │        ├── 用户需求 + 关键帧（base64）──────┼──→ 云端 LLM
                 │        │                                    │     只出 JSON 方案
                 │        │←──────────── JSON ──────────────────┘
                 │ 解析/校验 → 切片 → 拼接 → 字幕 → BGM → 导出 │
                 └─────────────────────────────────────────────┘
                              成片 D:\豆包\数据\视频成片
```

分工边界：

| 本地做 | 云端做 |
|---|---|
| 读素材、抽帧、裁切、拼接、字幕、混音、导出、队列、归档 | 理解需求、设计钩子/节奏/删留、给字幕文案与优化建议 |
| 断网也能执行手动任务 | 不接触原始视频，只收关键帧截图 |

## 目录结构

```
endcloud-clip-agent/
├─ run.py                 # 命令行入口（所有命令都从这里进）
├─ config.py              # 配置：路径 / 导出参数 / 云端接口
├─ core/
│  ├─ media.py            # ffprobe 读信息、ffmpeg 执行、关键帧抽取
│  ├─ plan.py             # JSON 方案解析、校验、时间轴换算
│  ├─ llm.py              # 云端调用（OpenAI 兼容接口）、重试
│  ├─ editor.py           # 切片/拼接/烧字幕/BGM 混音/导出/版本号
│  ├─ subtitles.py        # ASS / SRT 生成
│  ├─ queue.py            # SQLite 本地任务队列
│  ├─ logger.py           # 本地日志（滚动）
│  └─ timeutil.py         # 时间格式解析与格式化
├─ tests/
│  ├─ test_plan.py        # 方案解析、目标时长识别单测
│  ├─ test_correction.py  # 时长纠偏逻辑单测（假云端，不烧额度）
│  ├─ test_subtitles.py   # 字幕单测
│  ├─ test_e2e.py         # 端到端出片测试
│  └─ make_sample.py      # 生成 12 秒测试视频
├─ scripts/test_windows.bat
├─ .env.example
├─ requirements.txt
└─ README.md
```

运行时目录（默认在 `D:\豆包\数据`，自动创建）：

```
D:\豆包\数据\
├─ 视频素材\     放原始素材
├─ 视频成片\     成片 mp4 + srt + notes.md + plan.json
├─ 日志\         每天一个 .log，5MB 滚动
├─ 归档\抽帧\    每次抽的关键帧
├─ 归档\方案\    云端方案 JSON / 原始回复
└─ 任务\         tasks.db（队列）、临时\
```

## 安装

### 1. Python

需要 Python 3.10 及以上，安装时勾选“Add Python to PATH”。

### 2. FFmpeg

```bat
winget install Gyan.FFmpeg
```

或下载 gyan.dev 的 `ffmpeg-release-essentials.zip`，解压后把 `bin` 目录加入 PATH。

验证：

```bat
ffmpeg -version
ffprobe -version
```

### 3. 项目

本项目没有第三方 Python 包，直接运行即可：

```bat
cd /d "项目所在目录\endcloud-clip-agent"
copy .env.example .env
notepad .env
```

`.env` 至少填：

```ini
LLM_API_KEY=你的密钥
LLM_BASE_URL=https://api.openai.com/v1
LLM_MODEL=gpt-4o-mini
```

支持任何 OpenAI 兼容接口（OpenAI、DeepSeek、通义、Moonshot 等）。
模型不支持图片时设 `LLM_VISION=false`；服务不支持 `response_format` 时设 `LLM_JSON_MODE=false`。

## 快速开始

```bat
python run.py doctor
```

确认 FFmpeg、目录可写、云端配置都正常，再开始剪。

### 完整流程（云端出方案 → 本地自动剪）

```bat
python run.py auto --src "D:\豆包\数据\视频素材\a.mp4" --need "剪成 15 秒，前 3 秒用结果反差做钩子" --name 我的视频
```

执行顺序：读信息 → 抽 5 张关键帧 → 调云端 → 校验 JSON → 切片 → 拼接 → 烧字幕 → 导出。

**目标时长自动校验**：需求里写了明确时长（`剪成 10 秒`、`控制在 15 秒内`、`8-12 秒`、`两分钟`），本地会识别出来写进提示词；云端返回后按「保留段合计」核对，偏离超过 ±20%（且超过 1 秒）会带着差额自动纠偏一次。目标超过素材时长时按素材上限处理（不做慢放），日志会提示补素材或拆条。「前 3 秒做钩子」「每 8-12 秒一次变化」这类节奏描述不会被当成目标时长。

### 手动模式（跳过云端）

```bat
python run.py manual --src "D:\豆包\数据\视频素材\a.mp4" --keep 00:00:01-00:00:05 00:00:08-00:00:12 --subtitle "这一条的字幕" --name 手动版
```

`--keep` 可传多个，也支持逗号分隔。

### 已有方案，离线渲染

```bat
python run.py plan   --src "D:\...\a.mp4" --need "..." --mock
python run.py render --src "D:\...\a.mp4" --plan "D:\豆包\数据\归档\方案\xxx_plan.json" --bgm "D:\...\bgm.mp3"
```

`--mock` 不调云端，用本地占位方案跑通链路，适合没配密钥时测试。

### 分段调试

```bat
python run.py info   --src "D:\...\a.mp4"
python run.py frames --src "D:\...\a.mp4" --count 6
```

### 任务队列

```bat
python run.py queue add  --mode manual --src "D:\...\a.mp4" --keep 00:00:02-00:00:08 --name 排队任务
python run.py queue list
python run.py queue run
```

队列存在 SQLite 里，重启后继续；单条失败只标记 failed，不影响其它任务。

## 云端方案 JSON 规范

云端只能返回这个结构，本地才会执行：

```json
{
  "hooks": "前 3 秒用结果反差，先给成品再讲过程",
  "cut_list": [
    {"start": "00:00:00", "end": "00:00:04", "action": "保留", "subtitle": "先看成品效果"},
    {"start": "00:00:04", "end": "00:00:07", "action": "删除", "subtitle": ""},
    {"start": "00:00:07", "end": "00:00:12", "action": "保留", "subtitle": "关键一步别眨眼"}
  ],
  "bgm_time": "00:00:02,00:00:08",
  "comment": "中段再快 0.5 秒，结尾留一句提问"
}
```

本地校验规则：

- 四个字段必须齐全，`cut_list` 不能为空；
- `action` 只能是「保留 / 删除」（也接受 keep/drop）；
- `start`、`end` 支持 `HH:MM:SS`、`HH:MM:SS.mmm`、秒数；
- 至少一个「保留」，越界片段自动截断到视频结尾；
- 需求里识别到目标时长时，「保留」合计偏离 ±20%（且超过 1 秒）自动纠偏一次；纠偏失败则采用更接近目标的那一版；
- 返回散文、代码块外多余文字会被拒绝，重试后仍失败则报错，不会带着错误方案硬剪。

## 输出与命名

- 成片：`{日期}_{主题}_v{N}.mp4`，例如 `20261025_我的视频_v1.mp4`，旧版不覆盖
- 字幕：同名 `.srt`
- 说明：同名 `_notes.md`（源素材、片段表、参数、可调项）
- 方案：成片旁 `_plan.json`，归档目录另有权威副本

## 断网 / 没有 API 时

| 场景 | 做法 |
|---|---|
| 没有密钥，想先试流程 | `--mock` |
| 已有方案，只想重剪 | `render --plan ...` |
| 完全断网 | `manual` 或 `render`，队列里的手动任务也能跑 |

## 测试

```bat
python -m unittest discover -s tests -p "test_*.py" -v
```

或双击 `scripts\test_windows.bat`，会先跑 `doctor` 再跑全部测试。

第一次端到端测试会自动用 FFmpeg 生成 12 秒彩条素材，不需要外部文件。

## 常见问题

**提示找不到 ffmpeg**
装完 FFmpeg 后必须重开命令行窗口，PATH 才会生效；或直接把 `ffmpeg.exe` 所在目录加进系统环境变量。

**字幕是方块或位置不对**
Windows 默认字体 `Microsoft YaHei` 一般都有。换字体改 `.env` 的 `SUBTITLE_FONT`；位置改 `SUBTITLE_MARGIN_V`（0 为自动）。

**中文路径导致字幕烧录报错**
极少数 FFmpeg 构建对非 ASCII 路径的滤镜参数支持不好。把临时目录换到纯英文路径即可：

```ini
TEMP_DIR=C:\temp\clip
```

素材和成片仍可放在 D 盘中文目录。

**API 返回 401/403**
密钥错误或无权限。检查 `.env` 有没有 BOM/引号，`LLM_BASE_URL` 是否包含 `/v1`。

**云端返回的 JSON 总被拒**
换支持 JSON 输出的模型；或把 `LLM_JSON_MODE` 设为 false 再试。拒绝原因会写进日志。

**成片上下有黑边**
源视频比例和 `OUT_WIDTH/OUT_HEIGHT` 不一致，默认按比例缩放并补黑边。想跟随源尺寸，把两者设为 0。

**想要更快的预览**
临时把 `.env` 里 `OUT_PRESET=ultrafast`、`OUT_CRF=30`、`OUT_WIDTH=540`，确认后再用默认参数重导。

## 后续可扩展的位置

- **批量剪辑**：`queue add` 已经在，写个脚本循环入队即可；`_execute_payload` 是统一入口。
- **自动识别钩子**：在 `core/` 加一个 `highlights.py`，先用 FFmpeg 的场景检测/音量峰值出候选点，再拼进 `_make_plan` 的提示词。
- **多素材/B-roll**：`cut_list` 增加 `source` 字段即可，`build_timeline` 与 `cut_segment` 改成按片段来源取素材。
- **换模型供应商**：只改 `.env`；接口层在 `core/llm.py` 的 `_post`。
- **更精细的混音**：`editor.finalize` 的 `amix` 可换成 `sidechaincompress` 做真正 ducking。

## 安全

- 密钥只在本地 `.env`，不进代码、不进日志、不进成片
- 原始视频不上传；只有抽出的少量 JPEG 关键帧会以 base64 发给云端
- `.env` 已加入 `.gitignore`
