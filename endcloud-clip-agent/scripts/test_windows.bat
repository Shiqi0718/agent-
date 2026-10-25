@echo off
chcp 65001 >nul
cd /d "%~dp0\.."
echo ===== 环境检查 =====
python run.py doctor
if errorlevel 1 (
  echo [FAIL] 环境不满足，请先安装 FFmpeg 并加入 PATH
  exit /b 1
)
echo.
echo ===== 单元测试 + 端到端测试 =====
python -m unittest discover -s tests -p "test_*.py" -v
if errorlevel 1 (
  echo [FAIL] 测试未通过
  exit /b 1
)
echo.
echo [OK] 全部测试通过
