#!/bin/bash
# Drag your Meta export .zip onto this file (or run: ./run_mac.command file.zip)
# zip 파일을 이 파일 위로 끌어다 놓으세요.
cd "$(dirname "$0")" || exit 1
export PYTHONPATH="$PWD"
if ! command -v python3 >/dev/null 2>&1; then
  echo "[ERROR] Python 3.10+ not found. Install from https://www.python.org/downloads/"
  echo "[오류] 파이썬이 없습니다. README_ko.md 를 보세요."
  read -r -p "Press Enter to close"
  exit 1
fi
if [ $# -eq 0 ]; then
  echo "Drag and drop your export .zip onto run_mac.command. / zip 파일을 끌어다 놓으세요."
  read -r -p "Press Enter to close"
  exit 1
fi
for z in "$@"; do
  echo "=== $z ==="
  python3 -m snsnotes all "$z"
done
echo
echo "Done. Open the _snsnotes folder next to your zip. / 완료. zip 옆의 _snsnotes 폴더를 여세요."
echo "Read scan_report.md BEFORE uploading anything. / 업로드 전에 scan_report.md 를 먼저 읽으세요."
read -r -p "Press Enter to close"
