import sys

from .cli import main


def _windowless_setup():
    """Under pythonw (no console window) stdout/stderr are None: send them to a log file."""
    from pathlib import Path
    log_dir = Path.home() / ".snsnotes"
    log_dir.mkdir(parents=True, exist_ok=True)
    log = open(log_dir / "wizard.log", "a", encoding="utf-8", buffering=1)
    sys.stdout = sys.stderr = log
    return log_dir / "wizard.log"


def _show_error(msg: str):
    try:
        import ctypes
        ctypes.windll.user32.MessageBoxW(None, msg, "SNS 노트 마법사", 0x10)
    except Exception:
        pass


if sys.stdout is None or sys.stderr is None:
    log_path = _windowless_setup()
    try:
        code = main()
    except SystemExit as e:
        code = e.code if isinstance(e.code, int) else 1
    except Exception:
        import traceback
        traceback.print_exc()
        code = 1
    if code:
        _show_error(f"마법사를 시작하지 못했습니다.\nCould not start the wizard.\n\n기록(log): {log_path}")
    sys.exit(code)
else:
    sys.exit(main())
