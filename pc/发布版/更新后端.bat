@echo off
chcp 65001 >nul
REM 红果漫剧 PC 版 - 用最新后端 exe 替换安装目录里的旧版
REM 解决：连播开启后 ReferenceError + btn-auto 默认 active + 在线预热后 2 集

echo === 红果漫剧 PC 版后端更新 ===
echo.

set SRC=%~dp0..\backend\dist_backend\红果后端.exe
set DST=%LOCALAPPDATA%\Programs\红果漫剧\resources\backend\红果后端.exe
set DST2=%LOCALAPPDATA%\红果漫剧\resources\backend\红果后端.exe

if not exist "%SRC%" (
  echo 找不到源文件: %SRC%
  echo 请确认 pc\backend\dist_backend\红果后端.exe 存在
  pause
  exit /b 1
)

echo 源: %SRC%
echo 目标1: %DST%
echo 目标2: %DST2%
echo.

REM 尝试两个常见安装路径
if exist "%DST%" (
  echo 检测到安装位置1，正在替换...
  taskkill /F /IM "红果后端.exe" 2>nul
  timeout /t 2 >nul
  copy /Y "%SRC%" "%DST%"
  echo 完成！现在可以重新打开红果漫剧
  goto end
)

if exist "%DST2%" (
  echo 检测到安装位置2，正在替换...
  taskkill /F /IM "红果后端.exe" 2>nul
  timeout /t 2 >nul
  copy /Y "%SRC%" "%DST2%"
  echo 完成！现在可以重新打开红果漫剧
  goto end
)

echo 没找到标准安装目录。
echo 你的安装路径可能是自定义的，请手动：
echo   1. 先关闭红果漫剧（包括关闭后端进程）
echo   2. 把 "%SRC%" 复制到你的安装目录的 resources\backend\下
echo   3. 覆盖原来的 红果后端.exe
echo.

:end
pause