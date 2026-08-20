"""Player (and weapon) model library.

Same idea as maplib: find what is inside the CS2 install, export once through Source 2
Viewer, remember it.  A model is exported as .glb with its skeleton and animation clips,
which Unreal imports as a SkeletalMesh plus a set of AnimSequences - that is all the
sequence builder needs to put real characters on the demo tracks instead of cylinders.

Model paths are not hard-coded: the VPK is listed and filtered, so a CS2 update that
moves or renames agents does not break anything.
"""

from __future__ import annotations

import json
import re
import shutil
import time
from dataclasses import dataclass, asdict, field
from pathlib import Path

from . import assets, steam
from .util import Fail, human, info, ok, run_capture, warn

# where the game keeps its content archives
PAK_CANDIDATES = ("game/csgo/pak01_dir.vpk", "game/csgo_core/pak01_dir.vpk",
                  "game/csgo_imported/pak01_dir.vpk")

PLAYER_PATTERNS = (
    re.compile(r"characters/models/.+\.vmdl_c$", re.I),
    re.compile(r"models/player/.+\.vmdl_c$", re.I),
)
WEAPON_PATTERN = re.compile(r"weapons/models/.+\.vmdl_c$", re.I)

# names that are parts / props rather than a full agent
SKIP_TOKENS = ("_gloves", "_glove", "/hands", "_hands", "_arms", "shared", "_lod")

TEAM_HINTS = {"ct": ("ctm_", "/ct_", "_ct_", "sas", "gign", "fbi", "seal", "swat", "idf"),
              "t": ("tm_", "/t_", "_t_", "phoenix", "leet", "anarchist", "pirate", "balkan",
                    "professional", "separatist", "guerilla")}


@dataclass
class ModelBuild:
    name: str = ""
    vpk_path: str = ""          # path inside the vpk
    kind: str = "player"        # player | weapon
    team: str = ""
    out: str = ""
    main: str = ""              # the biggest glb
    files: int = 0
    bytes: int = 0
    animations: list = field(default_factory=list)
    converted_at: str = ""


# ------------------------------------------------------------------ discovery

def pak_files(cfg, cs2_dir: str = "") -> list:
    root = steam.game_root_from_any(cs2_dir) if cs2_dir else steam.game_root(cfg.cs2_exe)
    if not root:
        raise Fail("CS2 folder is unknown - run: cs2toue setup --cs2-exe <path to cs2.exe>")
    root = Path(root)
    out = [root / p for p in PAK_CANDIDATES if (root / p).is_file()]
    if not out:
        out = sorted(root.rglob("pak01_dir.vpk"))
    if not out:
        raise Fail(f"no pak01_dir.vpk found under {root}")
    return out


def _listing_cache(cfg, pak: Path) -> Path:
    st = pak.stat()
    return cfg.cache_dir / f"vpklist_{pak.parent.name}_{st.st_size}.txt"


def list_vpk(cfg, pak: Path, use_cache: bool = True) -> list:
    """All file paths inside a vpk (cached - the listing takes a while)."""
    cache = _listing_cache(cfg, pak)
    if use_cache and cache.is_file():
        return cache.read_text(encoding="utf-8", errors="replace").splitlines()
    exe = assets.ensure_cli(cfg)
    info(f"listing {pak.name} (first time only, this takes a moment)")
    text = run_capture([exe, "-i", str(pak), "--vpk_list"])
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    cache.parent.mkdir(parents=True, exist_ok=True)
    cache.write_text("\n".join(lines), encoding="utf-8")
    return lines


def _guess_team(path: str) -> str:
    low = path.lower()
    for team, hints in TEAM_HINTS.items():
        if any(h in low for h in hints):
            return team.upper()
    return ""


def find_models(cfg, kind: str = "player", filter_text: str = "", cs2_dir: str = "") -> list:
    """[{name, path, team, vpk}] for every player/weapon model in the install."""
    patterns = PLAYER_PATTERNS if kind == "player" else (WEAPON_PATTERN,)
    out, seen = [], set()
    for pak in pak_files(cfg, cs2_dir):
        for line in list_vpk(cfg, pak):
            if not any(p.search(line) for p in patterns):
                continue
            low = line.lower()
            if any(tok in low for tok in SKIP_TOKENS):
                continue
            name = Path(line).stem
            if name in seen:
                continue
            if filter_text and filter_text.lower() not in low:
                continue
            seen.add(name)
            out.append({"name": name, "path": line, "team": _guess_team(line),
                        "vpk": str(pak), "kind": kind})
    return sorted(out, key=lambda m: (m["team"], m["name"]))


# ------------------------------------------------------------------ library

def library_path(cfg) -> Path:
    return cfg.ws / "models" / "library.json"


def load_library(cfg) -> dict:
    p = library_path(cfg)
    if not p.is_file():
        return {}
    try:
        raw = json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return {}
    return {k: ModelBuild(**v) for k, v in raw.get("models", {}).items()}


def save_library(cfg, lib: dict) -> Path:
    p = library_path(cfg)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps({"models": {k: asdict(v) for k, v in lib.items()}}, indent=1),
                 encoding="utf-8")
    return p


def cached(cfg, name: str) -> ModelBuild | None:
    build = load_library(cfg).get(name)
    if build and Path(build.out).is_dir():
        return build
    return None


# ------------------------------------------------------------------ export

def export(cfg, model, force: bool = False, cs2_dir: str = "") -> ModelBuild:
    """model: a dict from find_models(), or a model name."""
    if isinstance(model, str):
        hits = [m for m in find_models(cfg, "player", model, cs2_dir)
                if m["name"].lower() == model.lower()]
        if not hits:
            hits = find_models(cfg, "player", model, cs2_dir)
        if not hits:
            hits = find_models(cfg, "weapon", model, cs2_dir)
        if not hits:
            raise Fail(f"model {model} not found - try: cs2toue models list --filter {model}")
        model = hits[0]

    name = model["name"]
    if not force:
        hit = cached(cfg, name)
        if hit:
            ok(f"{name}: already exported, reusing {hit.out}")
            return hit

    exe = assets.ensure_cli(cfg)
    out_dir = cfg.ws / "models" / name
    if out_dir.exists() and force:
        shutil.rmtree(out_dir, ignore_errors=True)
    out_dir.mkdir(parents=True, exist_ok=True)

    info(f"exporting model {name} ({model['path']})")
    started = time.time()
    cmd = [exe, "-i", model["vpk"], "-f", model["path"], "-o", str(out_dir), "-d",
           "--gltf_export_format", "glb", "--gltf_export_materials",
           "--gltf_export_animations", "--gltf_textures_adapt"]
    cmd += assets.game_info_args(cfg)
    from .util import run
    run(cmd, check=False)

    meshes = sorted(list(out_dir.rglob("*.glb")) + list(out_dir.rglob("*.gltf")))
    if not meshes:
        raise Fail(f"no glb produced for {name} - check the Source2Viewer output above")
    main = max(meshes, key=lambda p: p.stat().st_size)
    total = sum(p.stat().st_size for p in out_dir.rglob("*") if p.is_file())

    build = ModelBuild(
        name=name, vpk_path=model["path"], kind=model.get("kind", "player"),
        team=model.get("team", ""), out=str(out_dir), main=str(main), files=len(meshes),
        bytes=total, animations=_animation_names(main),
        converted_at=time.strftime("%Y-%m-%d %H:%M:%S"),
    )
    lib = load_library(cfg)
    lib[name] = build
    save_library(cfg, lib)
    ok(f"{name} exported in {time.time() - started:.0f}s -> {out_dir} ({human(total)}, "
       f"{len(build.animations)} animations)")
    return build


def _animation_names(glb_path) -> list:
    """Read the animation names straight out of the glb JSON chunk (no dependencies)."""
    try:
        import struct
        with open(glb_path, "rb") as fh:
            magic, _ver, _len = struct.unpack("<4sII", fh.read(12))
            if magic != b"glTF":
                return []
            chunk_len, chunk_type = struct.unpack("<II", fh.read(8))
            if chunk_type != 0x4E4F534A:      # 'JSON'
                return []
            data = json.loads(fh.read(chunk_len).decode("utf-8", "replace"))
        return [a.get("name", "") for a in data.get("animations", []) if a.get("name")]
    except Exception:
        return []


def remove(cfg, name: str) -> None:
    lib = load_library(cfg)
    build = lib.pop(name, None)
    if build and Path(build.out).is_dir():
        shutil.rmtree(build.out, ignore_errors=True)
    save_library(cfg, lib)
    ok(f"removed model {name}")


# ------------------------------------------------------------------ ue mapping

# how the sequence builder picks a clip for a movement state
STATE_KEYWORDS = {
    "death": ("death", "die", "dead"),
    "crouch_walk": ("crouch_walk", "crouchwalk", "duck_walk", "crouch_move"),
    "crouch_idle": ("crouch_idle", "crouchidle", "duck_idle", "crouch"),
    "run": ("run", "sprint"),
    "walk": ("walk",),
    "idle": ("idle", "stand"),
}


def match_animations(names: list) -> dict:
    """Map movement states onto whatever animation clips a model happens to ship."""
    out = {}
    low = [(n, n.lower()) for n in names]
    for state, keys in STATE_KEYWORDS.items():
        for key in keys:
            hit = next((n for n, l in low if key in l), None)
            if hit:
                out[state] = hit
                break
    return out


def write_ue_mapping(cfg, scene_dir, package: str = "/Game/cs2toUE/Models") -> Path:
    """Generate ue_mapping.json for a scene from the exported model library.

    Asset paths follow what Unreal creates when import_models.py brings the glb in:
    <package>/<model name>/<asset>.  Nothing is verified here - the sequence builder
    falls back to a cylinder for anything it cannot load, and the file is meant to be
    edited by hand afterwards.
    """
    lib = load_library(cfg)
    if not lib:
        raise Fail("no models exported yet - cs2toue models export <name>")
    mapping = {}
    for team, want in (("CT", "CT"), ("TERRORIST", "T")):
        pick = next((b for b in lib.values() if b.kind == "player" and b.team == want), None)
        if not pick:
            pick = next((b for b in lib.values() if b.kind == "player"), None)
        if not pick:
            continue
        base = f"{package}/{pick.name}"
        mapping[f"player.{team}"] = {
            "skeletal_mesh": f"{base}/{pick.name}.{pick.name}",
            "animations": {state: f"{base}/{clip}.{clip}"
                           for state, clip in match_animations(pick.animations).items()},
            "model": pick.name,
        }
    mapping.setdefault("grenade", "/Engine/BasicShapes/Sphere.Sphere")
    mapping.setdefault("default", "/Engine/BasicShapes/Cylinder.Cylinder")
    path = Path(scene_dir) / "ue_mapping.json"
    path.write_text(json.dumps(mapping, indent=1), encoding="utf-8")
    ok(f"mapping written: {path}")
    return path
