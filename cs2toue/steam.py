"""Locate Steam / CS2 and read the installed game build (from steam.inf)."""

from __future__ import annotations

import re
from pathlib import Path

CS2_APPID = 730
CS2_FOLDER = "Counter-Strike Global Offensive"


def _steam_path_from_registry() -> str:
    try:
        import winreg
    except ImportError:
        return ""
    for root, key in ((winreg.HKEY_CURRENT_USER, r"Software\Valve\Steam"),
                      (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\WOW6432Node\Valve\Steam")):
        try:
            with winreg.OpenKey(root, key) as k:
                for name in ("SteamPath", "InstallPath"):
                    try:
                        val = winreg.QueryValueEx(k, name)[0]
                        if val:
                            return str(Path(val))
                    except OSError:
                        continue
        except OSError:
            continue
    return ""


def steam_path() -> str:
    p = _steam_path_from_registry()
    if p and Path(p).is_dir():
        return p
    for guess in (r"C:\Program Files (x86)\Steam", r"D:\Steam", r"S:\Steam"):
        if Path(guess).is_dir():
            return guess
    return ""


def library_folders() -> list:
    """All Steam library roots, parsed out of libraryfolders.vdf."""
    roots = []
    sp = steam_path()
    if not sp:
        return roots
    roots.append(Path(sp))
    vdf = Path(sp) / "steamapps" / "libraryfolders.vdf"
    if vdf.is_file():
        text = vdf.read_text(encoding="utf-8", errors="replace")
        for m in re.finditer(r'"path"\s+"([^"]+)"', text):
            p = Path(m.group(1).replace("\\\\", "\\"))
            if p.is_dir() and p not in roots:
                roots.append(p)
    return roots


def find_cs2_exe() -> str:
    for root in library_folders():
        exe = root / "steamapps" / "common" / CS2_FOLDER / "game" / "bin" / "win64" / "cs2.exe"
        if exe.is_file():
            return str(exe)
    return ""


def game_root(cs2_exe: str) -> Path | None:
    """...\\Counter-Strike Global Offensive from a path to cs2.exe."""
    if not cs2_exe:
        return None
    p = Path(cs2_exe)
    for parent in p.parents:
        if (parent / "game" / "csgo").is_dir():
            return parent
    return None


def installed_version(cs2_exe: str) -> dict:
    """Parse game/csgo/steam.inf -> {'PatchVersion': '1.41.6.8', 'ClientVersion': ...}."""
    root = game_root(cs2_exe)
    if not root:
        return {}
    inf = root / "game" / "csgo" / "steam.inf"
    if not inf.is_file():
        return {}
    out = {}
    for line in inf.read_text(encoding="utf-8", errors="replace").splitlines():
        if "=" in line:
            k, _, v = line.partition("=")
            out[k.strip()] = v.strip()
    return out


def map_vpk(cs2_exe: str, map_name: str) -> str:
    """Path to <map>.vpk inside the CS2 install, if present."""
    root = game_root(cs2_exe)
    if not root or not map_name:
        return ""
    name = map_name.split("/")[-1]
    for sub in ("game/csgo/maps", "game/csgo/panorama/maps", "game/csgo_core/maps"):
        cand = root / sub / f"{name}.vpk"
        if cand.is_file():
            return str(cand)
    hits = list((root / "game").rglob(f"{name}.vpk"))
    return str(hits[0]) if hits else ""
