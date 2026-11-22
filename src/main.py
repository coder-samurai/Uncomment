from __future__ import annotations

import os
import sys
from pathlib import Path

os.environ.setdefault(
    "QT_LOGGING_RULES",
    "qt.qpa.wayland.textinput=false;qt.multimedia.ffmpeg=false",
)

from PyQt6.QtWidgets import QApplication

from ui.main_window import MainWindow

def _resolve_app_dirs() -> tuple[Path, Path]:
    env_override = os.environ.get("UNCOMMENT_DATA_DIR", "").strip()
    if env_override:
        base = Path(env_override).expanduser().resolve()
        return base / "data", base / "profile_data"

    if not getattr(sys, "frozen", False):
        src_dir = Path(__file__).resolve().parent
        return src_dir / "data", src_dir / "profile_data"

    if sys.platform == "win32":
        root = Path(os.environ.get("LOCALAPPDATA") or Path.home() / "AppData" / "Local") / "Uncomment"
    elif sys.platform == "darwin":
        root = Path.home() / "Library" / "Application Support" / "Uncomment"
    else:
        xdg = os.environ.get("XDG_DATA_HOME", "").strip()
        root = (Path(xdg) if xdg else Path.home() / ".local" / "share") / "Uncomment"

    return root / "data", root / "profile_data"

def main() -> int:
    data_dir, profile_dir = _resolve_app_dirs()
    data_dir.mkdir(parents=True, exist_ok=True)
    profile_dir.mkdir(parents=True, exist_ok=True)

    app = QApplication(sys.argv)
    app.setApplicationName("Uncomment")
    app.setStyle("Fusion")

    window = MainWindow(data_dir=data_dir, profile_dir=profile_dir)
    window.show()
    return app.exec()

if __name__ == "__main__":
    raise SystemExit(main())
