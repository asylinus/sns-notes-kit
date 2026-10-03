@echo off
rem Drag your Meta export .zip onto this file. Creates notes + NotebookLM files + privacy report next to the zip.
setlocal
chcp 65001 >nul
set "PYTHONUTF8=1"
set "PYTHONPATH=%~dp0"
set "PY="
where py >nul 2>nul && set "PY=py -3"
if not defined PY (where python >nul 2>nul && set "PY=python")
if not defined PY (
  echo [ERROR] Python 3.10+ is not installed. Get it from https://www.python.org/downloads/ ^(check "Add python.exe to PATH"^).
  echo [오류] 파이썬이 설치되어 있지 않습니다. README_ko.md 의 1단계를 보세요.
  pause
  exit /b 1
)
if "%~1"=="" (
  echo Drag and drop your export .zip file onto run_windows.bat.
  echo zip 파일을 이 파일 위로 끌어다 놓으세요.
  pause
  exit /b 1
)
:next
if "%~1"=="" goto done
echo.
echo === %~nx1 ===
%PY% -m snsnotes all "%~1"
shift
goto next
:done
echo.
echo Done. Open the "_snsnotes" folder next to your zip. / 완료. zip 옆의 "_snsnotes" 폴더를 여세요.
echo FIRST read scan_report.md before uploading anything anywhere. / 업로드 전에 scan_report.md 를 먼저 읽으세요.
pause
