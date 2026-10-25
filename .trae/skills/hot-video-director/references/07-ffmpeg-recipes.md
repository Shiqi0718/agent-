# 07 ffmpeg 成片配方（本环境可直接执行）

已确认可用：`ffmpeg 6.1`、`ffprobe`、`libx264`、`aac`、`libass`、`xfade`、`sidechaincompress`、`loudnorm`、`lut3d`。
中文字体：`Noto Sans CJK SC`、`Smiley Sans / 得意黑`（`/usr/share/fonts/opentype/smiley-sans/SmileySans-Oblique.otf`）。

> 所有命令默认在 `/workspace` 执行；输入输出用绝对路径更稳。
> 含中文的 filter 参数，整条命令用双引号包住更安全。

## 1. 先盘点素材

```bash
ffprobe -v error -show_entries \
format=duration,size,bit_rate:stream=index,codec_type,codec_name,width,height,r_frame_rate,sample_rate,channels \
-of json input.mp4
```

批量看所有素材时长：

```bash
for f in /workspace/footage/*.mp4; do
  printf "%s\t" "$f"
  ffprobe -v error -show_entries format=duration -of csv=p=0 "$f"
done
```

## 2. 横屏转竖屏（模糊背景填满，不变形）

```bash
ffmpeg -y -i in.mp4 -filter_complex \
"[0:v]scale=1080:1920:force_original_aspect_ratio=increase,crop=1080:1920,boxblur=24:2,eq=brightness=-0.08[bg];\
 [0:v]scale=1080:1920:force_original_aspect_ratio=decrease[fg];\
 [bg][fg]overlay=(W-w)/2:(H-h)/2" \
-c:v libx264 -preset slow -crf 18 -c:a aac -b:a 192k out_916.mp4
```

重构构图（裁切而非加背景）：

```bash
ffmpeg -y -i in.mp4 -vf "scale=1080:1920:force_original_aspect_ratio=increase,crop=1080:1920" \
-c:v libx264 -crf 18 -c:a aac -b:a 192k out_crop.mp4
```

## 3. 精确切片（重编码，帧级准确）

```bash
ffmpeg -y -ss 00:00:03.200 -i in.mp4 -t 1.800 \
-c:v libx264 -preset medium -crf 18 -c:a aac -b:a 192k clip_01.mp4
```

> `-ss` 放 `-i` 前是快速定位，放后面是精确解码。要卡帧就把 `-ss` 放 `-i` 后面。

## 4. 批量切片（Python，按 CSV 时间码）

```python
import csv, subprocess, pathlib

SRC = "/workspace/footage/main.mp4"
OUT = pathlib.Path("/workspace/edit/clips"); OUT.mkdir(parents=True, exist_ok=True)

with open("/workspace/edit/cuts.csv", newline="", encoding="utf-8") as f:
    for i, row in enumerate(csv.DictReader(f), 1):
        start, dur = float(row["start"]), float(row["dur"])
        dst = OUT / f"c{i:02d}.mp4"
        subprocess.run([
            "ffmpeg", "-y", "-ss", str(start), "-i", SRC, "-t", str(dur),
            "-c:v", "libx264", "-preset", "fast", "-crf", "18",
            "-c:a", "aac", "-b:a", "192k", str(dst)
        ], check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
```

CSV 格式：`start,dur`（秒，可小数）。

## 5. 拼接

同参数素材（快，不重编码）：

```bash
printf "file '%s'\n" /workspace/edit/clips/c*.mp4 > /workspace/edit/list.txt
ffmpeg -y -f concat -safe 0 -i /workspace/edit/list.txt -c copy out_concat.mp4
```

参数不一致（稳，重编码）：

```bash
ffmpeg -y -i a.mp4 -i b.mp4 -i c.mp4 -filter_complex \
"[0:v]scale=1080:1920,setsar=1,fps=30[v0];[1:v]scale=1080:1920,setsar=1,fps=30[v1];[2:v]scale=1080:1920,setsar=1,fps=30[v2];\
 [v0][0:a][v1][1:a][v2][2:a]concat=n=3:v=1:a=1[v][a]" \
-map "[v]" -map "[a]" -c:v libx264 -crf 18 -c:a aac -b:a 192k out.mp4
```

## 6. 变速（含音频保音高）

1.5 倍速：

```bash
ffmpeg -y -i in.mp4 -filter_complex \
"[0:v]setpts=PTS/1.5[v];[0:a]atempo=1.5[a]" \
-map "[v]" -map "[a]" -c:v libx264 -crf 18 -c:a aac out_fast.mp4
```

0.5 倍速（音频用 0.5，视频 setpts=PTS/0.5）：

```bash
ffmpeg -y -i in.mp4 -filter_complex \
"[0:v]setpts=PTS/0.5[v];[0:a]atempo=0.5[a]" \
-map "[v]" -map "[a]" -c:v libx264 -crf 18 -c:a aac out_slow.mp4
```

> `atempo` 单次范围 0.5–2.0，超出要串联（如 0.3 = `atempo=0.5,atempo=0.6`）。

## 7. 转场（xfade，两段之间 0.25s 叠化）

```bash
ffmpeg -y -i a.mp4 -i b.mp4 -filter_complex \
"[0:v]scale=1080:1920,fps=30,setsar=1[v0];[1:v]scale=1080:1920,fps=30,setsar=1[v1];\
 [v0][v1]xfade=transition=fade:duration=0.25:offset=3.75[v]" \
-map "[v]" -c:v libx264 -crf 18 out_xfade.mp4
```

`offset` = 第一段时长 − 转场时长。常用 `transition`：`fade`、`fadeblack`、`fadewhite`、`slideleft`、`wipeleft`。

## 8. 字幕：生成 ASS + 烧录

### 8.1 生成 ASS 的时间轴（Python）

```python
# 输入 [(start_sec, end_sec, "文本"), ...]，输出 styled.ass
def ts(t):
    h, r = divmod(t, 3600); m, s = divmod(r, 60)
    return f"{int(h)}:{int(m):02d}:{s:05.2f}"

def build_ass(lines, path="/workspace/edit/styled.ass", font="Smiley Sans"):
    header = f"""[Script Info]
ScriptType: v4.00+
PlayResX: 1080
PlayResY: 1920
WrapStyle: 2

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Main,{font},68,&H00FFFFFF,&H000000FF,&H00101010,&H64000000,-1,0,0,0,100,100,1,0,1,4,2,2,60,140,360,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""
    body = "".join(
        f"Dialogue: 0,{ts(a)},{ts(b)},Main,,0,0,0,,{txt}\n"
        for a, b, txt in lines
    )
    with open(path, "w", encoding="utf-8") as f:
        f.write(header + body)
```

关键词高亮：在文本里写 `{\c&H4DD8FF&}关键词{\c&HFFFFFF&}`（ASS 颜色是 BGR，`4DD8FF` = #FFD84D 金黄）。

### 8.2 烧录

```bash
ffmpeg -y -i in.mp4 -vf "ass=/workspace/edit/styled.ass" \
-c:v libx264 -crf 18 -c:a aac -b:a 192k out_sub.mp4
```

> 字幕要压在视频上，必须重新编码；`-c copy` 不行。
> 硬字幕的位置由 ASS 的 `MarginV` 控制，竖屏建议 320–420。

### 8.3 简单横排字幕（不写 ASS 时）

```bash
ffmpeg -y -i in.mp4 -vf "drawtext=fontfile=/usr/share/fonts/opentype/smiley-sans/SmileySans-Oblique.otf:\
text='3个快捷键':fontsize=72:fontcolor=white:borderw=4:bordercolor=black:x=(w-text_w)/2:y=h-380" \
-c:v libx264 -crf 18 -c:a aac out_drawtext.mp4
```

## 9. 声音

### 9.1 人声 + BGM + 自动 ducking

```bash
ffmpeg -y -i video.mp4 -i bgm.wav -filter_complex \
"[0:a]asplit=2[voice][sc];\
 [1:a]volume=0.35,aloop=loop=-1:size=2e9[bgm];\
 [bgm][sc]sidechaincompress=threshold=0.03:ratio=8:attack=20:release=400[duck];\
 [voice][duck]amix=inputs=2:duration=first:dropout_transition=0[a]" \
-map 0:v -map "[a]" -c:v libx264 -crf 18 -c:a aac -b:a 192k out_mix.mp4
```

### 9.2 响度统一到平台标准

```bash
ffmpeg -y -i in.mp4 -af "loudnorm=I=-14:TP=-1.5:LRA=11:print_format=summary" \
-c:v copy -c:a aac -b:a 192k out_loud.mp4
```

### 9.3 人声基础处理（高通 + 提亮 + 压缩）

```bash
ffmpeg -y -i in.mp4 -af "highpass=f=90,equalizer=f=3500:t=q:w=1.2:g=3,acompressor=threshold=-18dB:ratio=3:attack=10:release=150" \
-c:v copy -c:a aac -b:a 192k out_voice.mp4
```

## 10. 调色

一级（对比 + 饱和）：

```bash
ffmpeg -y -i in.mp4 -vf "eq=contrast=1.08:brightness=0.01:saturation=1.1" \
-c:v libx264 -crf 18 -c:a copy out_color.mp4
```

曲线（提黑、压高光）：

```bash
ffmpeg -y -i in.mp4 -vf "curves=preset=medium_contrast" -c:v libx264 -crf 18 -c:a copy out_curve.mp4
```

LUT（.cube）：

```bash
ffmpeg -y -i in.mp4 -vf "lut3d=/workspace/luts/film.cube,format=yuv420p" \
-c:v libx264 -crf 18 -c:a copy out_lut.mp4
```

## 11. 水印 / 台标

```bash
ffmpeg -y -i in.mp4 -i logo.png -filter_complex \
"[1:v]scale=160:-1[logo];[0:v][logo]overlay=W-w-60:H-h-300:format=auto" \
-c:v libx264 -crf 18 -c:a copy out_logo.mp4
```

## 12. 导出发布版（抖音）

```bash
ffmpeg -y -i master.mov \
-c:v libx264 -preset slow -profile:v high -level 4.1 \
-b:v 10M -maxrate 14M -bufsize 20M -pix_fmt yuv420p -r 30 \
-c:a aac -b:a 192k -ar 48000 -ac 2 \
-movflags +faststart out_douyin.mp4
```

B站横屏：

```bash
ffmpeg -y -i master.mov \
-c:v libx264 -preset slow -crf 18 -maxrate 16M -bufsize 32M -pix_fmt yuv420p \
-c:a aac -b:a 320k -ar 48000 -movflags +faststart out_bilibili.mp4
```

## 13. 质量自检

```bash
ffprobe -v error -show_entries format=duration,bit_rate:stream=codec_name,width,height,r_frame_rate,channels \
-of default=noprint_wrappers=1 out_douyin.mp4
```

抽 3 帧看构图与字幕：

```bash
ffmpeg -y -i out_douyin.mp4 -vf "select='eq(n,30)+eq(n,300)+eq(n,900)',scale=540:-1" -vsync 0 /workspace/edit/check_%02d.png
```

## 14. 常见报错

| 报错 | 原因 | 解决 |
|---|---|---|
| `Filtergraph ... has an unconnected output` | 滤镜链没 map | 检查 `-map "[v]"` |
| `Invalid argument` on `-vf` | 路径/引号/中文 | 加引号，或把路径改成英文 |
| 字幕不显示 | 未重编码 / 字体缺失 | 用 `-c:v libx264`；`fontfile` 写绝对路径 |
| 音画不同步 | 变速后未处理音频 | 视频 `setpts` 必须配 `atempo` |
| 花屏/绿屏 | 像素格式不兼容 | 末尾加 `format=yuv420p` |
| 文件太大 | 用 CRF 但码率失控 | 改 `-b:v` + `-maxrate` |
