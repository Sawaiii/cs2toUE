"""Configuration.

Everything cs2toUE downloads or generates lives under one workspace root, which
defaults to <project>/workspace on the same drive as the program (deliberately NOT
the system TEMP dir, which usually sits on C:).
"""

from __future__ import annotations

import json
import os
import shutil
import sys
from dataclasses import dataclass, asdict, field
from pathlib import Path

PKG_DIR = Path(__file__).resolve().parent

# Frozen (PyInstaller) build: the program lives next to the exe, while the read-only
# copies of data/ and ue/ sit inside the bundle. Everything writable stays next to the
# exe so the app keeps working from any folder without touching C:.
FROZEN = bool(getattr(sys, "frozen", False))
if FROZEN:
    PROJECT_DIR = Path(sys.executable).resolve().parent
    BUNDLE_DIR = Path(getattr(sys, "_MEIPASS", PROJECT_DIR))
else:
    PROJECT_DIR = PKG_DIR.parent
    BUNDLE_DIR = PROJECT_DIR


def _is_writable(path: Path) -> bool:
    try:
        path.mkdir(parents=True, exist_ok=True)
        probe = path / ".cs2toue_write_test"
        probe.write_text("", encoding="utf-8")
        probe.unlink()
        return True
    except OSError:
        return False


# Where the program is allowed to write. Normally that is its own folder, which keeps an
# install completely self contained. If somebody installs into C:\Program Files (or any
# other protected place), everything writable moves to the user profile instead, so the
# downloads, the config and the workspace keep working without administrator rights.
if _is_writable(PROJECT_DIR):
    APP_DIR = PROJECT_DIR
else:
    APP_DIR = Path(os.environ.get("LOCALAPPDATA") or os.path.expanduser("~")) / "cs2toUE"
    APP_DIR.mkdir(parents=True, exist_ok=True)

DATA_DIR = APP_DIR / "data"
UE_SCRIPT_DIR = (APP_DIR / "ue") if FROZEN else (PKG_DIR / "ue")
CONFIG_PATH = APP_DIR / "cs2toue.config.json"
DEFAULT_WORKSPACE = APP_DIR / "workspace"


def default_workspace() -> Path:
    return DEFAULT_WORKSPACE


def bootstrap_files(version: str = "") -> None:
    """Unpack data/ and the Unreal scripts next to the exe.

    The Unreal scripts have to be real files on disk - UnrealEditor-Cmd runs them by
    path, it cannot read them out of the bundle.

    What gets overwritten matters. The scripts belong to the program and must follow it
    on every update, otherwise an updated build would keep driving Unreal with the
    scripts of the version it was first installed as. The files under data/ are the
    opposite: the HLAE index, the CS2 version table and the user pins are refreshed at
    runtime, so they are only written when missing.
    """
    if not FROZEN:
        return
    marker = APP_DIR / ".bootstrap"
    previous = marker.read_text(encoding="utf-8").strip() if marker.is_file() else ""
    upgraded = bool(version) and previous != version

    for name, dest, overwrite in (("data", DATA_DIR, False),
                                  ("ue", UE_SCRIPT_DIR, upgraded)):
        src = BUNDLE_DIR / name
        if not src.is_dir():
            continue
        dest.mkdir(parents=True, exist_ok=True)
        for item in src.rglob("*"):
            if item.is_dir() or "__pycache__" in item.parts:
                continue
            target = dest / item.relative_to(src)
            if target.exists() and not overwrite:
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            try:
                shutil.copy2(item, target)
            except OSError:
                pass

    if version and previous != version:
        try:
            marker.write_text(version, encoding="utf-8")
        except OSError:
            pass


@dataclass
class Config:
    # where everything is stored
    workspace: str = field(default_factory=lambda: str(default_workspace()))
    # external tools
    cs2_exe: str = ""              # ...\Counter-Strike Global Offensive\game\bin\win64\cs2.exe
    csgo_exe: str = ""             # legacy CS:GO csgo.exe (if you still have one)
    steam_path: str = ""
    source2viewer_cli: str = ""    # Source2Viewer-CLI.exe
    mmcfg: str = ""                # moviemaking cfg parent folder (USRLOCALCSGO)
    # unreal - always chosen by the user, never auto-picked silently
    ue_engine: str = ""            # ...\UE_5.4
    ue_editor: str = ""            # ...\Engine\Binaries\Win64\UnrealEditor-Cmd.exe
    ue_project: str = ""           # ...\MyProject.uproject
    # conversion defaults
    ue_scale: float = 2.54         # 1 Source unit == 1 inch == 2.54 cm == 2.54 uu
    export_fps: float = 30.0
    hlae_channel: str = "auto"     # auto | latest | pinned version, see hlae/resolver.py
    extra: dict = field(default_factory=dict)

    # ---------------------------------------------------------------- paths

    @property
    def ws(self) -> Path:
        return Path(self.workspace)

    @property
    def hlae_dir(self) -> Path:
        return self.ws / "hlae"

    @property
    def downloads_dir(self) -> Path:
        return self.ws / "downloads"

    @property
    def exports_dir(self) -> Path:
        return self.ws / "exports"

    @property
    def assets_dir(self) -> Path:
        return self.ws / "assets"

    @property
    def cache_dir(self) -> Path:
        return self.ws / "cache"

    def ensure_dirs(self) -> None:
        for p in (self.ws, self.hlae_dir, self.downloads_dir, self.exports_dir,
                  self.assets_dir, self.cache_dir):
            p.mkdir(parents=True, exist_ok=True)

    # ---------------------------------------------------------------- io

    @classmethod
    def load(cls, path: Path | None = None) -> "Config":
        path = Path(path or CONFIG_PATH)
        cfg = cls()
        if path.is_file():
            raw = json.loads(path.read_text(encoding="utf-8"))
            for k, v in raw.items():
                if hasattr(cfg, k):
                    setattr(cfg, k, v)
                else:
                    cfg.extra[k] = v
        env_ws = os.environ.get("CS2TOUE_WORKSPACE")
        if env_ws:
            cfg.workspace = env_ws
        return cfg

    def save(self, path: Path | None = None) -> Path:
        path = Path(path or CONFIG_PATH)
        path.write_text(json.dumps(asdict(self), indent=2, ensure_ascii=False), encoding="utf-8")
        return path
