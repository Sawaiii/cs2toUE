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


def _ue_environment(cfg) -> dict:
    """Unreal must not write its caches to C:.

    The editor keeps the DerivedDataCache and its temp files in the user profile by
    default; on a machine with a full system drive that is not a slowdown but a crash
    at startup ("no writable nodes available"). Everything goes to the workspace, on
    the same drive the user already chose for this program.
    """
    import os
    env = os.environ.copy()
    ddc = cfg.ws / "ue_ddc"
    tmp = cfg.ws / "tmp"
    for d in (ddc, tmp):
        d.mkdir(parents=True, exist_ok=True)
    env["UE-LocalDataCachePath"] = str(ddc)
    env.setdefault("UE-SharedDataCachePath", str(ddc))
    env["TMP"] = env["TEMP"] = str(tmp)
    return env


def _ddc_args(cfg) -> list:
    """Use the plain file cache instead of ZenServer for headless runs.

    UE 5.4+ fronts the local cache with a Zen server process that installs itself into
    the user profile on C: - on a full system drive that copy fails and the whole
    editor aborts. The NoZenLocalFallback graphs keep the classic file cache, which we
    already point at the workspace via UE-LocalDataCachePath.
    """
    installed = Path(cfg.ue_engine or "") / "Engine" / "Build" / "InstalledBuild.txt"
    graph = "InstalledNoZenLocalFallback" if installed.is_file() else "NoZenLocalFallback"
    return [f"-ddc={graph}", "-NoZenAutoLaunch"]


def run_script(cfg, script, script_args, dry_run: bool = False) -> list:
    """Run one of our scripts inside a full offscreen editor.

    Not the pythonscript commandlet: that mode never boots the level-editor machinery,
    and the first spawned actor takes the whole process down. The real editor with
    NullRHI (no rendering, no window) is how Epic's own render pipelines run headless.
    The scripts call quit_editor() themselves when they see -Unattended.

    Forward slashes on purpose: UE swallows a backslash-u sequence in -script/-ExecCmds
    values as an escape, and the path loses a segment.
    """
    editor, project = ensure(cfg)
    arg = " ".join(str(a).replace(chr(92), "/") for a in [script] + list(script_args))
    # Never put a comma inside an argument value: Unreal splits -ExecCmds on commas
    # and would run everything after the first one as separate console commands.
    # RenderOffscreen, not NullRHI: without a real RHI this engine build cannot spawn
    # actors from a class (camera) - two different crash paths confirmed on 5.5. The
    # first boot of a project compiles shaders and takes long; the result lands in the
    # workspace cache and later runs start in seconds.
    cmd = [editor, project, f"-ExecCmds=py {arg}",
           "-stdout", "-FullStdOutLogOutput", "-RenderOffscreen", "-NoSplash",
           "-Unattended", "-NoZenAutoLaunch",
           # engine warnings ("video memory exhausted") must not end up burnt into
           # rendered frames
           "-NoScreenMessages"]
    installed = Path(cfg.ue_engine or "") / "Engine" / "Build" / "InstalledBuild.txt"
    cmd.append("-ddc=InstalledNoZenLocalFallback" if installed.is_file()
               else "-ddc=NoZenLocalFallback")
    if dry_run:
        print(" ".join(f'"{c}"' if " " in str(c) else str(c) for c in cmd))
        return cmd
    info("запуск Unreal без окна; первый запуск проекта компилирует шейдеры "
         "и может занять десятки минут, дальше - секунды")
    run(cmd, check=False, env=_ue_environment(cfg))
    return cmd


def build_sequence(cfg, scene_dir, extra=None, dry_run: bool = False):
    args = [Path(scene_dir).resolve(), f"--scale={cfg.ue_scale}"] + list(extra or [])
    return run_script(cfg, BUILD_SEQUENCE, args, dry_run)


def import_map(cfg, map_dir, extra=None, dry_run: bool = False):
    args = [Path(map_dir).resolve()] + list(extra or [])
    return run_script(cfg, IMPORT_MAP, args, dry_run)
