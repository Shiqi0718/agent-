# 端云协同 AI 剪辑 Agent

自用的**本地体力剪辑 + 云端方案设计**工具。

- 本地（Python + FFmpeg）：读素材、ffprobe、抽关键帧、裁切/拼接/字幕/BGM/混音/导出、任务队列、日志
- 云端（DeepSeek Flash，多模态）：看懂关键帧 + 理解需求，只返回结构化 JSON 剪辑方案
- **原始完整视频永不上传**：云端只收到「文字需求 + 3–8 张压缩后的关键帧」

平台：Windows（代码跨平台，Linux/macOS 也能跑）
依赖：Python 3.10+、FFmpeg（含 ffprobe）；无第三方 Python 包

---

## 固定目录（Windows）

```
D:\Trae work\Trae work处理文件夹\
├─ 视频素材\        放原始视频 / 音频 / 图片
├─ 视频成片\        成片 mp4 + srt + notes.md + plan.json
├─ 剪辑Agent日志\    每天一个 .log，5MB 滚动
├─ temp_frame\      关键帧缓存（可随时清空）
├─ temp_clip\       FFmpeg 切片中转（自动清理）
├─ 归档\            云端原始回复 / 权威方案副本
├─ BGM\             背景音乐素材（可选）
└─ 任务\            tasks.db（本地队列）
```

不填 `.env` 时自动使用上面的默认值。路径含空格，代码里统一用 raw 字符串 `r""` 和 `pathlib`，不需要手动转义。

---

## 架构

```
             ┌────────────── 本地（体力） ──────────────┐
视频素材 ──→ │ ffprobe 读信息 → 抽 3~8 张关键帧并压缩   │
             │        │                                  │
             │        ├── 需求 + 关键帧(base64) ─────────┼──→ DeepSeek Flash
             │        │                                  │    只回 JSON 方案
             │        │←────────── JSON ─────────────────┘
             │ 解析/校验 → 切片 → 拼接 → 字幕 → BGM → 导出 │
             └───────────────────────────────────────────┘
                          视频成片
```

| 本地做 | 云端做 |
|---|---|
| 遍历素材、ffprobe、抽帧压缩、裁切拼接、字幕压制、BGM 混音、音量、导出、队列、日志 | 看图 + 理解需求，给钩子/节奏/删留/字幕/卡点 |
| 断网也能执行手动任务与离线方案 | 不接触原始视频，只收少量关键帧 |

---

## 目录结构

```
endcloud-clip-agent/
├─ run.py                 # 命令行入口（所有命令从这里进）
├─ config.py              # 路径 / 抽帧 / 导出 / 云端配置（.env 驱动）
├─ core/
│  ├─ media.py            # 素材遍历、ffprobe、ffmpeg 执行、抽帧与压缩
│  ├─ plan.py             # JSON 方案解析、校验、时间轴换算、目标时长识别
│  ├─ llm.py              # DeepSeek Flash 调用（视觉识图）、重试、纠偏
│  ├─ editor.py           # 切片/拼接/烧字幕/BGM 混音/导出/版本号/说明
│  ├─ subtitles.py        # ASS / SRT 生成
│  ├─ queue.py            # SQLite 本地任务队列（支持重试）
│  ├─ logger.py           # 本地日志（滚动）
│  └─ timeutil.py         # 时间格式解析与格式化
├─ tests/
│  ├─ test_plan.py        # 方案解析、目标时长识别
│  ├─ test_correction.py  # 时长纠偏（假云端，不烧额度）
│  ├─ test_subtitles.py   # 字幕
│  ├─ test_e2e.py         # 端到端出片
│  └─ make_sample.py      # 生成 12 秒测试素材
├─ scripts/test_windows.bat
├─ scripts/demo_windows.bat
├─ .env.example
├─ requirements.txt
└─ README.md
```

---

## 安装

### 1. Python

Python 3.10+，安装时勾选 “Add Python to PATH”。

### 2. FFmpeg

```bat
winget install Gyan.FFmpeg
```

或下载 gyan.dev 的 `ffmpeg-release-essentials.zip`，把 `bin` 加入 PATH。验证：

```bat
ffmpeg -version
ffprobe -version
```

### 3. 配置

```bat
cd /d "项目所在目录\endcloud-clip-agent"
copy .env.example .env
notepad .env
```

必填：

```ini
LLM_API_KEY=你的 DeepSeek 密钥
LLM_BASE_URL=https://api.deepseek.com/v1
LLM_MODEL=deepseek-flash
LLM_VISION=true
LLM_JSON_MODE=true
LLM_MAX_TOKENS=4096
```

`deepseek-flash` 是推理模型，思考过程也占 completion tokens。`LLM_MAX_TOKENS` 太小会出现「只有思考、正文为空」，4096 是安全值。

### 4. 自检

```bat
python run.py doctor
```

会检查 FFmpeg、8 个目录可写、云端配置、视觉开关、抽帧与导出参数。

---

## 快速开始

### 盘点素材（只读文件名，不上传）

```bat
python run.py scan
python run.py scan --dir "D:\Trae work\Trae work处理文件夹\视频素材" --limit 20
```

### 模式 A：AI 分析模式（推荐）

```bat
python run.py auto --src "D:\Trae work\Trae work处理文件夹\视频素材\a.mp4" --need "剪成 9 秒，前 3 秒用结果反差做钩子，节奏要快" --name 我的视频
```

执行顺序：读信息 → 抽 5 张关键帧并压缩 → 发给 DeepSeek Flash → 校验 JSON → 切片 → 拼接 → 烧字幕 → 导出。

- `--clean-frames`：出方案后清理 `temp_frame` 里的本次抽帧
- `--mock`：不调云端，用本地占位方案跑通链路
- `--bgm "D:\...\bgm.mp3"`：叠加 BGM（循环 + 原声混音）

**目标时长自动校验**：需求里写了明确时长（`剪成 10 秒`、`控制在 15 秒内`、`8-12 秒`、`两分钟`）会自动识别并写进提示词；云端返回后按 keep 段合计核对，偏离 ±20%（且超过 1 秒）会带着差额自动纠偏一次。「前 3 秒做钩子」「每 8-12 秒一次变化」这类节奏描述不会被当成目标时长。

### 模式 B：手动模式（跳过云端）

```bat
python run.py manual --src "D:\...\视频素材\a.mp4" --keep 00:00:01-00:00:05 00:00:08-00:00:12 --subtitle "这一条的字幕" --name 手动版
```

`--keep` 可传多个，也支持逗号分隔。完全断网可用。

### 只要方案，不渲染

```bat
python run.py plan --src "D:\...\a.mp4" --need "剪成 15 秒高能卡点"
```

### 已有方案，离线重剪

```bat
python run.py render --src "D:\...\a.mp4" --plan "D:\...\某方案_plan.json" --bgm "D:\...\bgm.mp3"
```

### 分段调试

```bat
python run.py info   --src "D:\...\a.mp4"
python run.py frames --src "D:\...\a.mp4" --count 6
```

### 任务队列

```bat
python run.py queue add   --mode manual --src "D:\...\a.mp4" --keep 00:00:02-00:00:08 --name 排队任务
python run.py queue add   --mode auto   --src "D:\...\a.mp4" --need "剪成 12 秒" --name 云端任务
python run.py queue list
python run.py queue run   --limit 5 --retry 1
python run.py queue retry --id 3
python run.py queue retry --all
```

队列存在 SQLite，重启后继续；单条失败只标记 failed，不影响其它任务；`--retry N` 让失败任务自动重排 N 次。

---

## 云端方案 JSON 规范

云端只能返回这个结构（字段齐全，否则本地拒绝执行）：

```json
{
  "hooks": "别眨眼！3 秒后整个画面彻底变样",
  "overall_suggest": "前 3 秒连续彩色条纹制造刺激，之后每 1.8 秒一刀对齐鼓点",
  "cut_list": [
    {"start": "00:00:00.600", "end": "00:00:03.600", "action": "keep", "subtitle": "别眨眼，三秒后全变", "note": "钩子段，与第一记重鼓对齐"},
    {"start": "00:00:03.600", "end": "00:00:04.200", "action": "delete", "subtitle": "", "note": "删掉拖沓过渡，卡在空拍"},
    {"start": "00:00:04.200", "end": "00:00:06.000", "action": "keep", "subtitle": "线条开始飙起来了", "note": "快速硬切"}
  ],
  "bgm_plan": {
    "bgm_name": "Trap 风格鼓点，BPM 140-150",
    "beat_points": ["00:00:00.600", "00:00:03.600", "00:00:04.200"]
  }
}
```

本地校验规则：

- 四个字段必须齐全，`cut_list` 非空；
- `action` 只接受 keep / delete（也兼容「保留 / 删除」）；
- `start`/`end` 支持 `HH:MM:SS`、`HH:MM:SS.mmm`、纯秒数；
- 至少一个 keep，越界片段自动截断到视频结尾，重叠片段自动裁剪；
- 返回散文、Markdown 说明会被拒绝，重试后仍失败就报错，不会带着错误方案硬剪。

---

## 关键帧压缩

- 抽帧到 `temp_frame\素材名_时间戳\`
- 默认宽 720、`-q:v 3`
- 单张超过 `FRAME_MAX_KB`（默认 180KB）时，自动降到 640 宽并用 `-q:v 5/8/11` 重压
- 压缩失败会回滚原图，保证方案调用不断档
- 日志里会打印本次上传了几张、合计多少 KB

实测：12 秒测试素材抽 5 张，合计约 125KB。

---

## 输出与命名

- 成片：`{日期}_{主题}_v{N}.mp4`，旧版不覆盖
- 字幕：同名 `.srt`
- 说明：同名 `_notes.md`（源素材、钩子、整体节奏、BGM 建议、卡点、片段表、可调项）
- 方案：成片旁 `_plan.json`，`归档\方案\` 另有权威副本
- 云端原始回复：`归档\方案\..._raw.txt`（纠偏时含 `_raw1/_raw2`）

---

## 断网 / 没有 API 时

| 场景 | 做法 |
|---|---|
| 没有密钥，想先试流程 | `--mock` |
| 已有方案，只想重剪 | `render --plan ...` |
| 完全断网 | `manual` 或 `render`；队列里的手动任务也能跑 |

---

## 测试

```bat
python -m unittest discover -s tests -p "test_*.py" -v
```

或双击 `scripts\test_windows.bat`（先 `doctor` 再跑全部测试）。

示例脚本 `scripts\demo_windows.bat`：自检 → 盘点素材 → 读信息/抽帧 → AI 模式出片 → 手动模式出片。先把任意一条视频命名为 `demo.mp4` 放进「视频素材」目录即可。

端到端测试会用 FFmpeg 自动生成 12 秒彩条素材，不依赖外部文件，也不会调真实云端。

---

## 常见问题

**提示找不到 ffmpeg**
装完 FFmpeg 后重开命令行窗口，PATH 才会生效。

**云端返回空内容 / 只有思考过程**
`deepseek-flash` 是推理模型，`LLM_MAX_TOKENS` 太小会把预算全花在 `reasoning_content`。调到 4096，日志里会提示当前思考内容长度。

**接口返回 400：Prompt must contain the word 'json'**
`LLM_JSON_MODE=true` 时，提示词里必须出现 “json” 字样。本项目的系统提示词已包含；自建 prompt 时注意别删。

**API 返回 401/403**
密钥错误或无权限。检查 `.env` 有没有 BOM/引号，`LLM_BASE_URL` 是否包含 `/v1`。

**字幕是方块或位置不对**
Windows 默认 `Microsoft YaHei` 一般都有。换字体改 `SUBTITLE_FONT`；位置改 `SUBTITLE_MARGIN_V`（0 为自动）。

**中文路径导致字幕烧录报错**
极少数 FFmpeg 构建对非 ASCII 路径的滤镜参数支持不好。把中转目录换到纯英文路径：

```ini
TEMP_DIR=C:\temp\clip
```

素材和成片仍可放在 D 盘中文目录。

**成片上下有黑边**
源视频比例和 `OUT_WIDTH/OUT_HEIGHT` 不一致，默认等比缩放 + 补黑边。想跟随源尺寸，把两者设为 0。

**想要更快的预览**
临时 `OUT_PRESET=ultrafast`、`OUT_CRF=30`、`OUT_WIDTH=540`，确认后再用默认参数重导。

---

## 后续可扩展的位置

- **批量剪辑**：`scan` 已能盘点素材；`_execute_payload` 是统一入口，循环 `queue add` 即可。
- **字幕翻译**：在 `core/` 加 `translate.py`，把 `Segment.subtitle` 批量送云端翻译，再交给 `subtitles.write_ass`。
- **画面评分**：加 `core/score.py`，用 FFmpeg 场景检测 + 关键帧，给候选段落打「清晰度/信息密度/变化率」分，作为 `_make_plan` 的额外提示。
- **多素材/B-roll**：`cut_list` 加 `source` 字段，`build_timeline` 与 `cut_segment` 按来源取素材。
- **换模型供应商**：只改 `.env`；接口层在 `core/llm.py` 的 `_post`。
- **更精细的混音**：`editor.finalize` 的 `amix` 可换 `sidechaincompress` 做真正 ducking。

---

## 安全

- 密钥只在本地 `.env`，不进代码、不进日志、不进成片
- **原始视频不上传**；只有 3–8 张压缩 JPEG 以 base64 发给 DeepSeek Flash
- `.env` 已加入 `.gitignore`
