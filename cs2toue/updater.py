"""In-app updates from the GitHub releases of the project.

The program checks its own repository, and when a newer release is out it downloads
*only the program files* - a ~40 MB zip instead of the whole installer - and swaps them
in place. Everything the user accumulated stays untouched: the workspace with HLAE
builds, converted maps and exported scenes, the config, and the HLAE pins.

A running exe cannot overwrite itself, so the swap is done by a tiny helper script that
waits for the program to exit, copies the new files over, and starts it again.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import time
import zipfile
from dataclasses import dataclass, field
from pathlib import Path

from . import __version__
from .config import CONFIG_PATH, FROZEN, PROJECT_DIR
from .util import Fail, download, http_json, human, info, ok, version_tuple, warn

REPO = "Sawaiii/cs2toUE"
LATEST_API = f"https://api.github.com/repos/{REPO}/releases/latest"
RELEASES_PAGE = f"https://github.com/{REPO}/releases"

APP_ASSET = "cs2toUE-app.zip"        # program files only - what an update needs
SETUP_ASSET = "cs2toUE-Setup.exe"    # full installer - fallback for a fresh install

CHECK_INTERVAL = 24 * 3600           # do not hit the API more than once a day

@dataclass
class Update:
    available: bool = False
    version: str = ""
    current: str = __version__
    notes: str = ""
    url: str = ""
    size: int = 0
    asset: str = ""
    published: str = ""
    error: str = ""
    checked_at: float = field(default_factory=time.time)

    @property
    def summary(self) -> str:
        if self.error:
            return f"проверка обновлений не удалась: {self.error}"
        if not self.available:
            return f"установлена последняя версия ({self.current})"
        return f"доступна версия {self.version} ({human(self.size)}), у вас {self.current}"


def _state_path(cfg) -> Path:
    return cfg.cache_dir / "update_check.json"


def _load_state(cfg) -> dict:
    p = _state_path(cfg)
    if p.is_file():
        try:
            return json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {}


def _save_state(cfg, data: dict) -> None:
    p = _state_path(cfg)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(data, indent=1, ensure_ascii=False), encoding="utf-8")


def check(cfg=None, current: str = "", force: bool = False) -> Update:
    """Ask GitHub for the latest release. Cached for a day unless force=True."""
    current = current or __version__
    if cfg is not None and not force:
        state = _load_state(cfg)
        if state and time.time() - state.get("checked_at", 0) < CHECK_INTERVAL:
            cached = Update(**{k: v for k, v in state.items() if k in Update.__annotations__})
            cached.current = current
            cached.available = bool(cached.version) and \
                version_tuple(cached.version) > version_tuple(current)
            return cached

    upd = Update(current=current)
    try:
        rel = http_json(LATEST_API)
    except Exception as exc:
        upd.error = str(exc)
        return upd

    upd.version = str(rel.get("tag_name", "")).lstrip("vV")
    upd.notes = (rel.get("body") or "")[:2000]
    upd.published = (rel.get("published_at") or "")[:10]
    assets = {a["name"]: a for a in rel.get("assets", [])}
    asset = assets.get(APP_ASSET) or assets.get(SETUP_ASSET)
    if asset:
        upd.asset = asset["name"]
        upd.url = asset["browser_download_url"]
        upd.size = asset["size"]
    upd.available = bool(upd.version) and version_tuple(upd.version) > version_tuple(current)

    if cfg is not None:
        _save_state(cfg, {"checked_at": time.time(), "version": upd.version,
                          "notes": upd.notes, "url": upd.url, "size": upd.size,
                          "asset": upd.asset, "published": upd.published})
    return upd


# ------------------------------------------------------------------ applying

def _install_dir() -> Path:
    return PROJECT_DIR


def stage(cfg, upd: Update) -> Path:
    """Download and unpack the new version next to the old one."""
    if not upd.url:
        raise Fail(f"в релизе {upd.version} нет файла для обновления - "
                   f"скачайте установщик вручную: {RELEASES_PAGE}")
    if upd.asset != APP_ASSET:
        raise Fail(f"этот релиз опубликован только установщиком ({upd.asset}). "
                   f"Скачайте его: {RELEASES_PAGE}")

    cfg.ensure_dirs()
    archive = cfg.downloads_dir / f"cs2toUE-{upd.version}-app.zip"
    if not archive.is_file() or archive.stat().st_size != upd.size:
        info(f"скачивание обновления {upd.version} ({human(upd.size)})")
        download(upd.url, archive, upd.size)

    staged = cfg.ws / "update" / upd.version
    if staged.exists():
        shutil.rmtree(staged, ignore_errors=True)
    staged.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(archive) as zf:
        zf.extractall(staged)
    # the zip may carry a single top level folder
    entries = list(staged.iterdir())
    if len(entries) == 1 and entries[0].is_dir() and not (staged / "cs2toUE.exe").is_file():
        inner = entries[0]
        for item in list(inner.iterdir()):
            shutil.move(str(item), str(staged / item.name))
        inner.rmdir()
    if not (staged / "cs2toUE.exe").is_file():
        raise Fail("в архиве обновления нет cs2toUE.exe")
    ok(f"обновление {upd.version} распаковано")
    return staged


HELPER = r"""@echo off
rem cs2toUE updater - generated automatically, deletes itself at the end.
setlocal
set "SRC={staged}"
set "DST={install}"
set "LOG={log}"

echo update to {version} started %DATE% %TIME%> "%LOG%"
set TRIES=0

rem Wait until nothing is holding the files: both the window and the console build
rem count, because either one can be the process that asked for the update.
:waitloop
set /a TRIES+=1
if %TRIES% gtr 60 goto :copy
tasklist /fi "imagename eq cs2toUE.exe" | find /i "cs2toUE.exe" >nul && (ping -n 2 127.0.0.1 >nul & goto :waitloop)
tasklist /fi "imagename eq cs2toue-cli.exe" | find /i "cs2toue-cli.exe" >nul && (ping -n 2 127.0.0.1 >nul & goto :waitloop)

:copy
robocopy "%SRC%" "%DST%" /e /is /it /r:2 /w:1 /njh /njs /ndl /nfl /nc /ns >> "%LOG%"
if errorlevel 8 (
  echo RESULT FAILED>> "%LOG%"
  goto :done
)
echo RESULT OK>> "%LOG%"
rmdir /s /q "%SRC%" 2>nul

{restart}

:done
endlocal
del "%~f0"
"""


def apply(cfg, staged: Path, restart: bool = True, wait: int = 0, version: str = "") -> bool:
    """Write and launch the helper that swaps the files once this process exits.

    The helper runs hidden but *with* a console: a detached process has none at all,
    and cmd.exe cannot run a batch file without one - that silently did nothing.
    """
    install = _install_dir()
    restart_line = f'start "" "{install / "cs2toUE.exe"}"' if restart else "rem no restart"
    updir = cfg.ws / "update"
    updir.mkdir(parents=True, exist_ok=True)
    log = updir / "update.log"
    if log.exists():
        log.unlink()
    helper = updir / "apply_update.cmd"
    helper.write_text(
        HELPER.format(staged=staged, install=install, log=log,
                      version=version or "new version", restart=restart_line),
        encoding="utf-8",
    )
    subprocess.Popen(
        ["cmd", "/c", str(helper)],
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0)
        | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0),
        cwd=str(cfg.ws),
    )
    if not wait:
        return True
    # only the command line waits: the window has to close before the swap can happen
    deadline = time.time() + wait
    while time.time() < deadline:
        if log.is_file():
            text = log.read_text(encoding="utf-8", errors="replace")
            if "RESULT OK" in text:
                return True
            if "RESULT FAILED" in text:
                raise Fail(f"замена файлов не удалась, старая версия на месте - см. {log}")
        time.sleep(0.5)
    return False


def update(cfg, current: str = "", force: bool = False, restart: bool = True,
           wait: int = 0) -> Update:
    """Full flow: check, download, stage, hand over to the helper.

    wait > 0 (command line): stay until the swap is done and report the real outcome.
    wait == 0 (window): the helper waits for this process to close first.
    """
    if not FROZEN:
        raise Fail("обновление работает только для установленной программы; "
                   "в исходниках используйте git pull")
    upd = check(cfg, current, force=True)
    if upd.error:
        raise Fail(f"не удалось проверить обновления: {upd.error}")
    if not upd.available and not force:
        info(upd.summary)
        return upd
    staged = stage(cfg, upd)
    # never wait here: the helper cannot replace an exe that this very process is
    # running, so it waits for us to exit first
    apply(cfg, staged, restart, wait=0, version=upd.version)
    ok(f"обновление до {upd.version} применится сразу после выхода из программы"
       + (" и она запустится снова" if restart else ""))
    return upd
