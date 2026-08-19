"""Map / model extraction through Source 2 Viewer (ValveResourceFormat).

Source2Viewer-CLI decompiles a CS2 map .vpk into glTF/glb, which Unreal imports
directly (Interchange glTF importer, UE 5.x).  The CLI is fetched automatically into
the workspace, so nothing has to be installed by hand.

Note from the VRF docs: CLI flags are not a stable API. They live in ASSET_FLAGS below
so they are easy to adjust without touching the rest of the tool.
"""

from __future__ import annotations

import shutil
import zipfile
from pathlib import Path

from .util import Fail, download, http_json, info, ok, run, warn

VRF_LATEST_API = "https://api.github.com/repos/ValveResourceFormat/ValveResourceFormat/releases/latest"
VRF_ASSET = "cli-windows-x64.zip"

ASSET_FLAGS = {
    "map": ["-d", "-e", "vwrld_c", "--gltf_export_format", "glb",
            "--gltf_export_materials", "--gltf_textures_adapt"],
    "model": ["-d", "--gltf_export_format", "glb", "--gltf_export_materials",
              "--gltf_export_animations"],
}


def cli_path(cfg) -> Path | None:
    if cfg.source2viewer_cli and Path(cfg.source2viewer_cli).is_file():
        return Path(cfg.source2viewer_cli)
    local = cfg.ws / "tools" / "source2viewer" / "Source2Viewer-CLI.exe"
    if local.is_file():
        return local
    hits = sorted((cfg.ws / "tools").rglob("Source2Viewer-CLI.exe")) if (cfg.ws / "tools").is_dir() else []
    return hits[0] if hits else None


def ensure_cli(cfg) -> Path:
    exe = cli_path(cfg)
    if exe:
        return exe
    cfg.ensure_dirs()
    info("fetching Source 2 Viewer CLI")
    rel = http_json(VRF_LATEST_API)
    asset = next((a for a in rel["assets"] if a["name"] == VRF_ASSET), None)
    if not asset:
        raise Fail(f"{VRF_ASSET} not found in Source2Viewer release {rel.get('tag_name')}")
    zip_path = cfg.downloads_dir / f"s2v-{rel['tag_name']}-{VRF_ASSET}"
    if not zip_path.is_file() or zip_path.stat().st_size != asset["size"]:
        download(asset["browser_download_url"], zip_path, asset["size"])
    dest = cfg.ws / "tools" / "source2viewer"
    if dest.exists():
        shutil.rmtree(dest, ignore_errors=True)
    dest.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(zip_path) as zf:
        zf.extractall(dest)
    exe = cli_path(cfg)
    if not exe:
        raise Fail("Source2Viewer-CLI.exe not found after extraction")
    cfg.source2viewer_cli = str(exe)
    cfg.save()
    ok(f"Source 2 Viewer CLI {rel.get('tag_name')} ready")
    return exe


def export_map(cfg, vpk_path, out_dir=None, extra_flags=None) -> Path:
    """Decompile a map .vpk to glb. Returns the output folder."""
    exe = ensure_cli(cfg)
    vpk_path = Path(vpk_path)
    if not vpk_path.is_file():
        raise Fail(f"map vpk not found: {vpk_path}")
    out_dir = Path(out_dir or (cfg.assets_dir / vpk_path.stem))
    out_dir.mkdir(parents=True, exist_ok=True)
    cmd = [exe, "-i", str(vpk_path), "-o", str(out_dir)] + ASSET_FLAGS["map"]
    from . import steam
    root = steam.game_root(cfg.cs2_exe)
    if root:
        cmd += ["--game", str(root / "game" / "csgo")]
    cmd += list(extra_flags or [])
    run(cmd)
    glbs = sorted(out_dir.rglob("*.glb")) + sorted(out_dir.rglob("*.gltf"))
    if glbs:
        ok(f"map exported: {len(glbs)} mesh file(s) under {out_dir}")
        for g in glbs[:5]:
            print(f"    {g}")
    else:
        warn(f"no glb/gltf produced in {out_dir} - check the flags in assets.ASSET_FLAGS")
    return out_dir


def export_model(cfg, vmdl_path, out_dir=None, extra_flags=None) -> Path:
    exe = ensure_cli(cfg)
    out_dir = Path(out_dir or (cfg.assets_dir / "models"))
    out_dir.mkdir(parents=True, exist_ok=True)
    cmd = ([exe, "-i", str(vmdl_path), "-o", str(out_dir)] + ASSET_FLAGS["model"]
           + list(extra_flags or []))
    run(cmd)
    return out_dir
