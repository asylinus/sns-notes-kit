#!/bin/bash
# Double-click (or run): opens the step-by-step wizard. Drag your Meta export .zip onto this file to start with it selected.
# 더블클릭하면 마법사가 열립니다. zip 파일을 이 파일 위로 끌어다 놓으면 그 파일이 미리 선택됩니다.
cd "$(dirname "$0")" || exit 1
export PYTHONPATH="$PWD"
if ! command -v python3 >/dev/null 2>&1; then
  echo "[ERROR] Python 3.10+ not found. Install from https://www.python.org/downloads/"
  echo "[오류] 파이썬이 없습니다. README.md 를 보세요."
  read -r -p "Press Enter to close"
  exit 1
fi
if [ $# -eq 0 ]; then
  python3 -m snsnotes wizard || read -r -p "Press Enter to close"
else
  python3 -m snsnotes wizard --zip "$1" || read -r -p "Press Enter to close"
fi
