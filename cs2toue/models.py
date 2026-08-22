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
import os
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
    re.compile(r"agents/models/.+\.vmdl_c$", re.I),      # CS2 keeps agents here
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


def _clean_listing(lines) -> list:
    """Source 2 Viewer prints "path CRC:0011.. size:1234" - keep just the path.

    The trailing metadata is why every "ends with .vmdl_c" pattern used to miss:
    the line does not end with the extension at all.
    """
    out = []
    for ln in lines:
        ln = ln.strip()
        if not ln:
            continue
        cut = ln.find(" CRC:")
        if cut == -1:
            cut = ln.find(" size:")
        out.append((ln[:cut] if cut != -1 else ln).strip())
    return out


def list_vpk(cfg, pak: Path, use_cache: bool = True) -> list:
    """All file paths inside a vpk (cached - the listing takes a while)."""
    cache = _listing_cache(cfg, pak)
    if use_cache and cache.is_file():
        # older caches hold the raw lines, so clean on read as well as on write
        return _clean_listing(cache.read_text(encoding="utf-8",
                                              errors="replace").splitlines())
    exe = assets.ensure_cli(cfg)
    info(f"listing {pak.name} (first time only, this takes a moment)")
    text = run_capture([exe, "-i", str(pak), "--vpk_list"])
    lines = _clean_listing(text.splitlines())
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
    lib = {k: ModelBuild(**v) for k, v in raw.get("models", {}).items()}
    moved = False
    for name, b in lib.items():
        local = cfg.ws / "models" / name
        if not Path(b.out).is_dir() and local.is_dir():
            b.out = str(local)
            main_name = Path(b.main).name if b.main else ""
            b.main = str(local / main_name) if main_name and (local / main_name).is_file() else b.main
            moved = True
    if moved:
        save_library(cfg, lib)
    return lib


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

def export(cfg, model, force: bool = False, cs2_dir: str = "",
           anims: str = "core", scene_dir: str = "") -> ModelBuild:
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
           "--gltf_textures_adapt"]
    # A full agent is ~1 GB and 2062 clips; the builder uses a couple of hundred.
    # Filtering at export is the only place it is cheap - Unreal would otherwise
    # import every one of them.
    wanted = []
    mode = (anims or "core").strip().lower()
    if mode == "none":
        pass
    elif mode == "all":
        cmd.append("--gltf_export_animations")
    else:
        wanted = clips_for_scene(scene_dir) if scene_dir else (
            wanted_clips() if mode == "core"
            else [w.strip() for w in str(anims).split(",") if w.strip()])
        cmd += ["--gltf_export_animations", "--gltf_animation_list", ",".join(wanted)]
    if wanted:
        info(f"{name}: {len(wanted)} clip name(s) requested "
             f"({'scene' if scene_dir else mode})")
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

# How the sequence builder picks clips.
#
# Verified against a real CS2 export (ctm_sas, 2062 clips): the game does NOT keep one
# locomotion set. Clips live per weapon family
#
#     animation/anims/world/<family>/<set>/run_ne_rifle
#
# with family in rifle | pistol | knife | grenade | equipment, eight directions each,
# and per-gun clips on top (shoot_ak, idle_m4a4, draw_awp). There is no death clip at
# all - CS2 kills are ragdoll, so a "death" animation simply does not exist and the
# builder holds the last pose instead of inventing one.
DIRECTIONS = ("n", "ne", "e", "se", "s", "sw", "w", "nw")

# state -> clip name prefix inside a family
FAMILY_STATES = {
    "run": "run_{dir}_",
    "walk": "walk_{dir}_",
    "crouch_walk": "crouch_{dir}_",
    "jump": "jump_{dir}_",
    "air": "inair_{dir}_",
}
FAMILY_SINGLE = {
    "idle": ("idle_{fam}", "idle_"),
    "crouch_idle": ("idle_crouch_{fam}", "idle_crouch_"),
    "jump_stand": ("jump_stand_{fam}",),
    "air_stand": ("inair_stand_{fam}",),
}

# what the demo calls a weapon -> which family its animations live in
FAMILY_BY_WEAPON = {
    "knife": "knife", "bayonet": "knife", "daggers": "knife", "karambit": "knife",
    "grenade": "grenade", "flashbang": "grenade", "molotov": "grenade",
    "decoy": "grenade", "incendiary": "grenade", "explosive": "grenade",
    "c4": "equipment", "healthshot": "equipment", "taser": "equipment", "zeus": "equipment",
}
PISTOLS = ("glock", "usp", "p2000", "p250", "deagle", "deserteagle", "revolver",
           "elite", "berettas", "tec9", "fiveseven", "cz75", "cz75a")

# the demo prints display names, CS2 names its clips differently
GUN_ALIASES = {
    "deserteagle": "deagle", "dualberettas": "elite", "r8revolver": "revolver",
    "m4a1s": "m4a1s", "m4a1silencer": "m4a1s", "usps": "usp", "uspsilencer": "usp",
    "ak47": "ak", "mp5sd": "mp5sd", "sawedoff": "sawedoff", "ssg08": "ssg08",
    "scar20": "scar20", "sg553": "sg556", "galilar": "galilar", "galil": "galilar",
    "mag7": "mag7", "xm1014": "xm1014", "negev": "negev", "m249": "m249",
    "ump45": "ump45", "pp19bizon": "bizon", "bizon": "bizon", "mac10": "mac10",
    "shadowdaggers": "knife", "butterflyknife": "knife", "huntsmanknife": "knife",
}


def _family_for(weapon: str) -> str:
    """rifle is the default: it is where CS2 keeps rifles, smgs and shotguns alike."""
    low = re.sub(r"[^a-z0-9]", "", str(weapon).lower())
    for token, fam in FAMILY_BY_WEAPON.items():
        if token in low:
            return fam
    if any(p in low for p in PISTOLS):
        return "pistol"
    return "rifle"


def _gun_token(weapon: str) -> str:
    """Demo weapon name -> the token CS2 uses in clip names (Desert Eagle -> deagle)."""
    low = re.sub(r"[^a-z0-9]", "", str(weapon).lower())
    return GUN_ALIASES.get(low, low)


# every gun token CS2 uses in clip names, taken from a full ctm_sas export
GUN_TOKENS = (
    "ak", "aug", "awp", "bizon", "cz75", "cz75a", "deagle", "elite", "famas",
    "fiveseven", "g3sg1", "galilar", "glock", "hkp", "m249", "m4a1s", "m4a4",
    "mac10", "mag7", "mp5sd", "mp7", "mp9", "negev", "nova", "p250", "p90",
    "revolver", "sawedoff", "scar20", "sg556", "ssg08", "taser", "tec9",
    "ump45", "usp", "xm1014", "molotov", "c4", "healthshot",
    "bayonet", "bowie", "butterfly", "canis", "cord", "css", "falchion", "flip",
    "gut", "karambit", "kukri", "m9", "navaja", "outdoor", "push", "skeleton",
    "stiletto", "tactical", "talon", "ursus",
)
FAMILIES = ("rifle", "pistol", "knife", "grenade", "equipment")


def wanted_clips(families=None, guns=None) -> list:
    """Clip names the sequence builder can actually use.

    A full agent carries ~2062 clips and a gigabyte of animation data; the builder
    touches a couple of hundred of them. Names that a given model does not have are
    harmless - the exporter simply finds nothing to match.
    """
    families = tuple(families or FAMILIES)
    guns = tuple(guns or GUN_TOKENS)
    out = []
    for fam in families:
        for template in ("run_{d}_{f}", "walk_{d}_{f}", "crouch_{d}_{f}",
                         "jump_{d}_{f}", "inair_{d}_{f}"):
            out += [template.format(d=d, f=fam) for d in DIRECTIONS]
        out += [f"idle_{fam}", f"idle_crouch_{fam}",
                f"jump_stand_{fam}", f"inair_stand_{fam}",
                f"jump_crouch_stand_{fam}", f"inair_crouch_stand_{fam}"]
    for gun in guns:
        out += [f"shoot_{gun}", f"idle_{gun}", f"draw_{gun}", f"idle_crouch_{gun}"]
    out += ["throw_overhand_grenade", "throw_underhand_grenade", "flashed", "breathing"]
    return sorted(set(out))


def clips_for_scene(scene_dir) -> list:
    """Only what this scene needs: the weapons its players actually hold."""
    import csv as _csv
    scene_dir = Path(scene_dir)
    try:
        scene = json.loads((scene_dir / "scene.json").read_text(encoding="utf-8"))
    except Exception:
        return wanted_clips()
    weapons = set()
    for actor in scene.get("actors", []):
        if actor.get("kind") != "player" or not actor.get("track"):
            continue
        track = scene_dir / str(actor["track"]).replace("/", os.sep)
        if not track.is_file():
            continue
        with open(track, "r", encoding="utf-8", newline="") as fh:
            for row in _csv.DictReader(fh):
                w = (row.get("weapon") or "").strip()
                if w:
                    weapons.add(w)
    if not weapons:
        return wanted_clips()
    families = {_family_for(w) for w in weapons}
    guns = {resolve_gun(w, GUN_TOKENS) for w in weapons}
    guns.discard("")
    info(f"scene uses {len(weapons)} weapon(s): "
         f"families {sorted(families)}, guns {sorted(guns)}")
    return wanted_clips(families, guns)


def resolve_gun(weapon: str, guns) -> str:
    """Best gun token for a demo weapon name among the ones a model actually ships.

    Exact hit, then alias, then the longest token that is a prefix of the name:
    "glock18" finds "glock" without another table entry.
    """
    low = re.sub(r"[^a-z0-9]", "", str(weapon).lower())
    for cand in (low, GUN_ALIASES.get(low, "")):
        if cand and cand in guns:
            return cand
    best = ""
    for token in guns:
        if (low.startswith(token) or token.startswith(low)) and len(token) > len(best):
            best = token
    return best


def _by_family(names: list) -> dict:
    out = {}
    for full in names:
        m = re.search(r"/world/([a-z0-9_]+)/", full)
        if m:
            out.setdefault(m.group(1), []).append(full)
    return out


def match_animations(names: list) -> dict:
    """Group a model's clips the way CS2 itself does.

    Returns {"families": {fam: {state: clip | {dir: clip}}},
             "guns":     {gun_token: {"shoot": clip, "idle": clip, "draw": clip}}}
    so the sequence builder can pick by the weapon a player is actually holding.
    """
    families, guns = {}, {}
    for fam, clips in _by_family(names).items():
        tail = {c.rsplit("/", 1)[-1]: c for c in clips}
        entry = {}
        for state, template in FAMILY_STATES.items():
            found = {}
            for d in DIRECTIONS:
                prefix = template.format(dir=d)
                hit = next((full for name, full in tail.items()
                            if name.startswith(prefix) and fam in name), None)
                if hit:
                    found[d] = hit
            if found:
                found["default"] = found.get("n") or next(iter(found.values()))
                entry[state] = found
        for state, candidates in FAMILY_SINGLE.items():
            for cand in candidates:
                want = cand.format(fam=fam)
                hit = tail.get(want) or next(
                    (full for name, full in tail.items()
                     if name.startswith(want) and name.endswith(fam)), None)
                if hit:
                    entry[state] = hit
                    break
        if entry:
            families[fam] = entry

        for name, full in tail.items():
            for kind in ("shoot", "idle", "draw", "throw"):
                m = re.fullmatch(kind + r"_([a-z0-9]+)", name)
                if m and m.group(1) not in ("crouch", fam):
                    guns.setdefault(m.group(1), {})[kind] = full
    return {"families": families, "guns": guns}


def _manifest(cfg, build, package: str) -> dict:
    """{asset name: real /Game path} for a model that is already in the project.

    Two sources, in order: the file the importer writes, and - when an import was
    interrupted before it could write one - the project's Content folder itself.
    Scanning is the more reliable of the two: the .uasset files are the ground truth
    for what Unreal named things.
    """
    out = {}
    try:
        raw = json.loads((Path(build.out) / "imported_assets.json").read_text(
            encoding="utf-8"))
        for path in raw.get("assets", []):
            leaf = str(path).split("/")[-1].split(".")[0]
            out[leaf] = path if "." in str(path).split("/")[-1] else f"{path}.{leaf}"
    except Exception:
        pass
    if out:
        return out
    project = Path(getattr(cfg, "ue_project", "") or "")
    if not project.is_file():
        return out
    folder = project.parent / "Content" / package.replace("/Game/", "").strip("/") / build.name
    if not folder.is_dir():
        return out
    for f in sorted(folder.glob("*.uasset")):
        out[f.stem] = f"{package.rstrip('/')}/{build.name}/{f.stem}.{f.stem}"
    return out


def _resolve_asset(clip: str, manifest: dict, base: str) -> str:
    """Real imported path for a clip, falling back to the guessed name."""
    if manifest:
        flat = clip.replace("/", "_")
        hit = manifest.get(flat)
        if hit:
            return hit
        tail = clip.rsplit("/", 1)[-1]
        for name, path in manifest.items():
            if name.endswith(tail):
                return path
    leaf = clip.rsplit("/", 1)[-1]
    return f"{base}/{leaf}.{leaf}"


def _pick_mesh(manifest: dict, base: str, name: str) -> str:
    """The skeletal mesh Unreal made - its name is derived from the vmdl, not the model."""
    for asset, path in manifest.items():
        low = asset.lower()
        if "physicsasset" in low or "skeleton" in low.replace("skeletal", ""):
            continue
        if "body" in low or low.endswith(name.lower()):
            return path
    return f"{base}/{name}.{name}"


def _asset_paths(node, base: str, manifest=None):
    """Turn every clip name in the nested match_animations result into an asset path.

    The structure is {"families": {...}, "guns": {...}} several levels deep, so this
    walks it instead of assuming a flat table - a plain dict comprehension used to
    iterate the *characters* of the path string.
    """
    if isinstance(node, dict):
        return {k: _asset_paths(v, base, manifest) for k, v in node.items()}
    if isinstance(node, str):
        return _resolve_asset(node, manifest or {}, base)
    return node


# family prefixes CS2 puts in weapon model names
WEAPON_PREFIXES = ("weapon_", "v_", "w_", "rif_", "smg_", "pist_", "snip_", "shot_",
                   "mach_", "eq_")


def weapon_keys(model_name: str) -> list:
    """Lookup keys for a weapon model: its own name and the token a demo name gives.

    "weapon_rif_ak47" -> ["ak47", "ak", "weapon_rif_ak47"], so whichever spelling the
    demo uses finds the model.
    """
    low = model_name.lower()
    for prefix in WEAPON_PREFIXES:
        while low.startswith(prefix):
            low = low[len(prefix):]
    flat = re.sub(r"[^a-z0-9]", "", low)
    keys = [flat, model_name.lower()]
    alias = GUN_ALIASES.get(flat)
    if alias:
        keys.append(alias)
    for token in GUN_TOKENS:
        if flat.startswith(token) and len(token) >= 3:
            keys.append(token)
    if "knife" in flat or "dagger" in flat:
        keys.append("knife")
    return [k for i, k in enumerate(keys) if k and k not in keys[:i]]


def _pick_weapon_mesh(manifest: dict, base: str, name: str) -> str:
    for asset, path in manifest.items():
        low = asset.lower()
        if "physicsasset" in low or "skeleton" in low:
            continue
        if low.endswith(name.lower()) or name.lower() in low:
            return path
    return f"{base}/{name}.{name}"


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
        man = _manifest(cfg, pick, package)
        if man:
            info(f"{pick.name}: {len(man)} imported asset(s) known, using real paths")
        mapping[f"player.{team}"] = {
            "skeletal_mesh": _pick_mesh(man, base, pick.name),
            "animations": _asset_paths(match_animations(pick.animations), base, man),
            "model": pick.name,
        }
    # weapons: one entry per exported weapon model, so the sequence builder can put
    # the right gun into the right hands at the right time
    for b in lib.values():
        if b.kind != "weapon":
            continue
        base = f"{package}/{b.name}"
        man = _manifest(cfg, b, package)
        asset = _pick_weapon_mesh(man, base, b.name)
        # keyed by the token the demo will resolve to, not by the file name:
        # "weapon_rif_ak47" and the demo's "AK-47" both land on "ak47"
        for key in weapon_keys(b.name):
            mapping.setdefault(f"weapon.{key}", asset)
    mapping.setdefault("weapon_bone", "hand_R")
    mapping.setdefault("grenade", "/Engine/BasicShapes/Sphere.Sphere")
    mapping.setdefault("default", "/Engine/BasicShapes/Cylinder.Cylinder")
    path = Path(scene_dir) / "ue_mapping.json"
    path.write_text(json.dumps(mapping, indent=1), encoding="utf-8")
    ok(f"mapping written: {path}")
    return path
