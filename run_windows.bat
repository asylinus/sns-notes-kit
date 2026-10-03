@echo off
rem Double-click: opens the step-by-step wizard. Drag your Meta export .zip onto this file: wizard starts with that zip selected.
rem Command-line users: python -m snsnotes all your.zip
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
rem Run without a console window when possible (pyw/pythonw); errors pop up as a message box and go to %USERPROFILE%\.snsnotes\wizard.log
set "PYW="
where pyw >nul 2>nul && set "PYW=pyw -3"
if not defined PYW (where pythonw >nul 2>nul && set "PYW=pythonw")
if defined PYW (
  if "%~1"=="" (
    start "" %PYW% -m snsnotes wizard
  ) else (
    start "" %PYW% -m snsnotes wizard --zip "%~1"
  )
  exit /b 0
)
if "%~1"=="" (
  %PY% -m snsnotes wizard
) else (
  %PY% -m snsnotes wizard --zip "%~1"
)
if errorlevel 1 pause
