@echo off
chcp 65001 >nul
cd /d "%~dp0\.."

set ROOT=D:\Trae work\Trae work处理文件夹
set SRC=%ROOT%\视频素材\demo.mp4

echo ===== 0. 自检 =====
python run.py doctor
if errorlevel 1 (
  echo [FAIL] 环境不满足，请先安装 FFmpeg 并加入 PATH
  exit /b 1
)

echo.
echo ===== 1. 盘点素材目录 =====
python run.py scan --limit 10

echo.
echo ===== 2. 读取信息 + 抽 5 张关键帧 =====
if not exist "%SRC%" (
  echo [SKIP] 没找到示例素材：%SRC%
  echo 把任意一条视频改名为 demo.mp4 放进视频素材目录，或修改本脚本里的 SRC。
  goto :manual
)
python run.py info --src "%SRC%"
python run.py frames --src "%SRC%" --count 5

echo.
echo ===== 3. 模式 A：抽帧 + DeepSeek Flash 出方案 + 自动出片 =====
python run.py auto --src "%SRC%" --need "剪成 9 秒，前 3 秒用结果反差做钩子，节奏要快" --name 示例_AI --clean-frames

:manual
echo.
echo ===== 4. 模式 B：手动时间戳，跳过云端 =====
if exist "%SRC%" (
  python run.py manual --src "%SRC%" --keep 00:00:01-00:00:04 00:00:06-00:00:09 --subtitle "手动模式示例" --name 示例_手动
)

echo.
echo [DONE] 成片输出目录：%ROOT%\视频成片
