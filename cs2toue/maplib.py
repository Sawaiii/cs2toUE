"""Map library: find the maps inside a CS2 install, convert them once, reuse forever.

Decompiling a map takes minutes, so every conversion is recorded in

    <workspace>/maps/library.json

together with the size and timestamp of the source .vpk.  Asking for the same map
again returns the cached folder instantly; if Valve patched the map (the vpk changed)
the entry goes stale and the map is rebuilt.

The heavy lifting is done by Source 2 Viewer / ValveResourceFormat, the open source
Source 2 decompiler - see assets.py.
"""

from __future__ import annotations

import json
import shutil
import time
from dataclasses import dataclass, asdict, field
from pathlib import Path

from . import assets, steam
from .util import Fail, human, info, ok, warn

# folders inside a CS2 install that hold map vpks
MAP_DIRS = ("game/csgo/maps", "game/csgo_core/maps", "game/csgo_imported/maps")
SKIP_SUFFIXES = ("_vanity", "_preview")


@dataclass
class MapFile:
    name: str
    vpk: str
    size: int = 0
    mtime: float = 0.0
    source: str = "game"        # game | workshop
    workshop_id: str = ""

    def key(self) -> str:
        return f"{self.name}:{self.size}:{int(self.mtime)}"


@dataclass
class MapBuild:
    name: str = ""
    vpk: str = ""
    vpk_size: int = 0
    vpk_mtime: float = 0.0
    out: str = ""
    files: int = 0
    main: str = ""
    bytes: int = 0
    converted_at: str = ""
    flags: list = field(default_factory=list)
    source: str = "game"

    def key(self) -> str:
        return f"{self.name}:{self.vpk_size}:{int(self.vpk_mtime)}"


# ---------------------------------------------------------------- discovery

def scan(cfg, cs2_dir: str = "") -> list:
    """Every map vpk in the install, plus subscribed workshop maps."""
    root = Path(cs2_dir) if cs2_dir else steam.game_root(cfg.cs2_exe)
    out = []
    seen = set()
    if root and Path(root).is_dir():
        root = Path(root)
        # accept both the install root and the .../game/csgo folder
        if root.name == "csgo" and (root.parent.name == "game"):
            root = root.parent.parent
        for sub in MAP_DIRS:
            d = root / sub
            if not d.is_dir():
                continue
            for vpk in sorted(d.glob("*.vpk")):
                name = vpk.stem
                if name in seen or name.endswith(SKIP_SUFFIXES):
                    continue
                seen.add(name)
                st = vpk.stat()
                out.append(MapFile(name=name, vpk=str(vpk), size=st.st_size,
                                   mtime=st.st_mtime, source="game"))
    # workshop maps: steamapps/workshop/content/730/<id>/<map>.vpk
    for lib in steam.library_folders():
        wdir = Path(lib) / "steamapps" / "workshop" / "content" / "730"
        if not wdir.is_dir():
            continue
        for vpk in sorted(wdir.glob("*/*.vpk")):
            name = vpk.stem
            if name in seen:
                continue
            seen.add(name)
            st = vpk.stat()
            out.append(MapFile(name=name, vpk=str(vpk), size=st.st_size, mtime=st.st_mtime,
                               source="workshop", workshop_id=vpk.parent.name))
    return out


def find(cfg, name: str, cs2_dir: str = "") -> MapFile | None:
    name = Path(str(name)).stem
    for m in scan(cfg, cs2_dir):
        if m.name == name:
            return m
    return None


# ---------------------------------------------------------------- library

def library_path(cfg) -> Path:
    return cfg.ws / "maps" / "library.json"


def load_library(cfg) -> dict:
    p = library_path(cfg)
    if not p.is_file():
        return {}
    try:
        raw = json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return {}
    return {k: MapBuild(**v) for k, v in raw.get("maps", {}).items()}


def save_library(cfg, lib: dict) -> Path:
    p = library_path(cfg)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps({"maps": {k: asdict(v) for k, v in lib.items()}}, indent=1),
                 encoding="utf-8")
    return p


def cached(cfg, map_file: MapFile) -> MapBuild | None:
    """A previous conversion that still matches the current vpk, or None."""
    lib = load_library(cfg)
    build = lib.get(map_file.name)
    if not build:
        return None
    if not Path(build.out).is_dir():
        return None
    if build.key() != map_file.key():
        return None
    return build


def status(cfg) -> list:
    """[(MapFile, MapBuild|None, state)] for every map in the install."""
    lib = load_library(cfg)
    rows = []
    for m in scan(cfg):
        build = lib.get(m.name)
        if not build or not Path(build.out).is_dir():
            state = "not converted"
            build = None
        elif build.key() != m.key():
            state = "stale (map was updated)"
        else:
            state = "ready"
        rows.append((m, build, state))
    return rows


# ---------------------------------------------------------------- convert

def convert(cfg, name_or_file, force: bool = False, cs2_dir: str = "") -> MapBuild:
    map_file = name_or_file if isinstance(name_or_file, MapFile) else find(cfg, name_or_file, cs2_dir)
    if not map_file:
        raise Fail(f"map {name_or_file} not found in the CS2 install "
                   f"(cs2toue maps list shows what is there)")

    if not force:
        hit = cached(cfg, map_file)
        if hit:
            ok(f"{map_file.name}: already converted, reusing {hit.out}")
            return hit

    out_dir = cfg.ws / "maps" / map_file.name
    if out_dir.exists() and force:
        shutil.rmtree(out_dir, ignore_errors=True)
    out_dir.mkdir(parents=True, exist_ok=True)

    info(f"converting {map_file.name} ({human(map_file.size)}) - this takes a few minutes")
    started = time.time()
    assets.export_map(cfg, map_file.vpk, out_dir)

    meshes = sorted(list(out_dir.rglob("*.glb")) + list(out_dir.rglob("*.gltf")))
    if not meshes:
        raise Fail(f"no glb/gltf produced for {map_file.name} - see the Source2Viewer output above")
    total = sum(p.stat().st_size for p in out_dir.rglob("*") if p.is_file())
    main = max(meshes, key=lambda p: p.stat().st_size)

    build = MapBuild(
        name=map_file.name, vpk=map_file.vpk, vpk_size=map_file.size, vpk_mtime=map_file.mtime,
        out=str(out_dir), files=len(meshes), main=str(main), bytes=total,
        converted_at=time.strftime("%Y-%m-%d %H:%M:%S"),
        flags=list(assets.ASSET_FLAGS["map"]), source=map_file.source,
    )
    lib = load_library(cfg)
    lib[map_file.name] = build
    save_library(cfg, lib)
    ok(f"{map_file.name} converted in {time.time() - started:.0f}s -> {out_dir} "
       f"({len(meshes)} mesh files, {human(total)})")
    return build


def ensure(cfg, map_name: str) -> MapBuild | None:
    """Used by the demo pipeline: convert the demo map unless it is already cached."""
    if not map_name:
        return None
    map_file = find(cfg, map_name)
    if not map_file:
        warn(f"map {map_name} is not in the CS2 install - skipping map conversion")
        return None
    return convert(cfg, map_file)


def remove(cfg, name: str) -> None:
    lib = load_library(cfg)
    build = lib.pop(name, None)
    if build and Path(build.out).is_dir():
        shutil.rmtree(build.out, ignore_errors=True)
    save_library(cfg, lib)
    ok(f"removed {name} from the map library")
