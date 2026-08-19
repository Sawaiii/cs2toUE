"""Finding and driving an Unreal Engine installation.

The engine folder and the project are always user choices - cs2toUE only *suggests*
what it found (registry, Epic launcher manifest, usual folders on every drive).  Nothing
is picked silently, because a machine can easily carry three engine versions and a dozen
projects.

    cs2toue ue detect                      what is on this machine
    cs2toue ue set --engine "D:\\UE_5.4" --project "D:\\Work\\My.uproject"
    cs2toue ue build <scene folder>        runs build_sequence.py inside the editor
    cs2toue ue map <map name or folder>    runs import_map.py inside the editor
"""

from __future__ import annotations

import json
from pathlib import Path

from .ue import BUILD_SEQUENCE, IMPORT_MAP
from .util import Fail, info, ok, run, warn

EDITOR_NAMES = ("UnrealEditor-Cmd.exe", "UE4Editor-Cmd.exe")


# ------------------------------------------------------------------ discovery

def _from_registry() -> list:
    out = []
    try:
        import winreg
    except ImportError:
        return out
    for root, key in ((winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\EpicGames\Unreal Engine"),
                      (winreg.HKEY_CURRENT_USER, r"SOFTWARE\EpicGames\Unreal Engine")):
        try:
            with winreg.OpenKey(root, key) as k:
                for i in range(winreg.QueryInfoKey(k)[0]):
                    ver = winreg.EnumKey(k, i)
                    try:
                        with winreg.OpenKey(k, ver) as sub:
                            path = winreg.QueryValueEx(sub, "InstalledDirectory")[0]
                            if path:
                                out.append(Path(path))
                    except OSError:
                        continue
        except OSError:
            continue
    return out


def _from_launcher_manifest() -> list:
    out = []
    dat = Path(r"C:\ProgramData\Epic\UnrealEngineLauncher\LauncherInstalled.dat")
    if not dat.is_file():
        return out
    try:
        data = json.loads(dat.read_text(encoding="utf-8", errors="replace"))
    except Exception:
        return out
    for item in data.get("InstallationList", []):
        name = str(item.get("AppName", ""))
        loc = item.get("InstallLocation")
        if loc and name.upper().startswith("UE_"):
            out.append(Path(loc))
    return out


def _from_disks() -> list:
    out = []
    roots = []
    for letter in "CDEFGHIJKLMNOPQRSTUVWXYZ":
        drive = Path(f"{letter}:\\")
        if drive.exists():
            roots += [drive / "Program Files" / "Epic Games", drive / "Epic Games",
                      drive / "UE", drive]
    for root in roots:
        try:
            if not root.is_dir():
                continue
            for child in root.glob("UE_*"):
                if child.is_dir():
                    out.append(child)
        except OSError:
            continue
    return out


def editor_cmd(engine_dir) -> Path | None:
    """<engine>/Engine/Binaries/Win64/UnrealEditor-Cmd.exe"""
    p = Path(engine_dir)
    if p.is_file() and p.name in EDITOR_NAMES:
        return p
    for name in EDITOR_NAMES:
        cand = p / "Engine" / "Binaries" / "Win64" / name
        if cand.is_file():
            return cand
    # user pointed at .../Engine or .../Binaries/Win64
    for name in EDITOR_NAMES:
        for depth in (p, p / "Binaries" / "Win64"):
            cand = depth / name
            if cand.is_file():
                return cand
    return None


def detect_engines() -> list:
    seen, out = set(), []
    for p in _from_registry() + _from_launcher_manifest() + _from_disks():
        try:
            key = str(Path(p).resolve()).lower()
        except OSError:
            continue
        if key in seen:
            continue
        seen.add(key)
        exe = editor_cmd(p)
        if exe:
            out.append({"dir": str(p), "editor": str(exe), "version": _version_of(p)})
    return out


def _version_of(engine_dir) -> str:
    build = Path(engine_dir) / "Engine" / "Build" / "Build.version"
    if build.is_file():
        try:
            data = json.loads(build.read_text(encoding="utf-8"))
            return f"{data.get('MajorVersion')}.{data.get('MinorVersion')}.{data.get('PatchVersion')}"
        except Exception:
            pass
    return Path(engine_dir).name


def detect_projects(search_dirs=None) -> list:
    """.uproject files in the usual places (Documents/Unreal Projects and given dirs)."""
    out = []
    roots = [Path.home() / "Documents" / "Unreal Projects"]
    for d in (search_dirs or []):
        roots.append(Path(d))
    for root in roots:
        if not root.is_dir():
            continue
        for up in list(root.glob("*/*.uproject")) + list(root.glob("*.uproject")):
            out.append(str(up))
    return sorted(set(out))


# ------------------------------------------------------------------ config

def set_engine(cfg, path: str) -> str:
    exe = editor_cmd(path)
    if not exe:
        raise Fail(f"UnrealEditor-Cmd.exe not found under {path}. Point --engine at the engine "
                   f"folder (the one containing Engine\\Binaries\\Win64) or at the exe itself.")
    cfg.ue_engine = str(Path(exe).parents[3]) if exe.parents[2].name == "Binaries" else str(path)
    cfg.ue_editor = str(exe)
    cfg.save()
    ok(f"Unreal engine: {cfg.ue_engine}  ({_version_of(cfg.ue_engine)})")
    return cfg.ue_editor


def set_project(cfg, path: str) -> str:
    p = Path(path)
    if p.is_dir():
        hits = sorted(p.glob("*.uproject"))
        if not hits:
            raise Fail(f"no .uproject file inside {p}")
        p = hits[0]
    if not p.is_file() or p.suffix.lower() != ".uproject":
        raise Fail(f"not an Unreal project: {path}")
    cfg.ue_project = str(p)
    cfg.save()
    ok(f"Unreal project: {p}")
    return cfg.ue_project


def ensure(cfg):
    """(editor exe, project) or a clear error telling the user what to set."""
    editor = cfg.ue_editor
    if not editor or not Path(editor).is_file():
        found = detect_engines()
        if len(found) == 1:
            info(f"using the only Unreal install found: {found[0]['dir']}")
            editor = set_engine(cfg, found[0]["dir"])
        else:
            hint = "\n".join(f"    {e['version']:<10} {e['dir']}" for e in found)
            raise Fail("Unreal engine folder is not set.\n"
                       + (f"  found on this machine:\n{hint}\n" if found else "")
                       + '  set it with: cs2toue ue set --engine "<folder with Engine\\Binaries>"')
    if not cfg.ue_project or not Path(cfg.ue_project).is_file():
        found = detect_projects()
        hint = "\n".join(f"    {p}" for p in found[:10])
        raise Fail("Unreal project is not set.\n"
                   + (f"  found on this machine:\n{hint}\n" if found else "")
                   + '  set it with: cs2toue ue set --project "<path to .uproject>"')
    return editor, cfg.ue_project


def run_script(cfg, script, script_args, dry_run: bool = False) -> list:
    editor, project = ensure(cfg)
    arg = " ".join([str(script)] + [str(a) for a in script_args])
    cmd = [editor, project, "-run=pythonscript", f"-script={arg}",
           "-unattended", "-nosplash", "-stdout", "-FullStdOutLogOutput"]
    if dry_run:
        print(" ".join(f'"{c}"' if " " in str(c) else str(c) for c in cmd))
        return cmd
    info("starting Unreal - this opens the project headless, it can take a minute")
    run(cmd, check=False)
    return cmd


def build_sequence(cfg, scene_dir, extra=None, dry_run: bool = False):
    args = [Path(scene_dir).resolve(), f"--scale={cfg.ue_scale}"] + list(extra or [])
    return run_script(cfg, BUILD_SEQUENCE, args, dry_run)


def import_map(cfg, map_dir, extra=None, dry_run: bool = False):
    args = [Path(map_dir).resolve()] + list(extra or [])
    return run_script(cfg, IMPORT_MAP, args, dry_run)
