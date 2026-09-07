@echo off
setlocal
cd /d "%~dp0"
chcp 65001 >nul
set "PYTHONUTF8=1"

echo 将打开闲鱼登录窗口，请用手机闲鱼 App 扫描窗口中的二维码并确认登录。
echo 登录成功后，本窗口会自动保存本机登录态；不会上传 Cookie，也不会执行购买、发布或私信。
echo.
".\.venv\Scripts\python.exe" -X utf8 -m cd_monitor.cli xianyu-login-state --output "data\xianyu_state.json" --timeout-seconds 300
set "LOGIN_EXIT=%ERRORLEVEL%"

if not "%LOGIN_EXIT%"=="0" (
  echo.
  echo 未检测到有效登录态。请重新双击本文件并在 5 分钟内完成扫码。
  pause
  exit /b %LOGIN_EXIT%
)

echo.
echo 闲鱼登录态已保存。本机采集器现在可以安全开始首轮低频比价。
pause
exit /b 0
