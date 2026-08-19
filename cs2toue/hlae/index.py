"""Index of every published HLAE release.

Built straight from the GitHub releases API of advancedfx/advancedfx, so "any HLAE
version" really means any - the index carries the download URL of each release zip
plus the metadata needed to pick one:

  * which AfxHookSource  (CS:GO / Source 1) it shipped
  * which AfxHookSource2 (CS2 / Source 2) it shipped
  * which CS2 game update it was adjusted to ("Adjusted to CS2 update (1.41.6.8)")

data/hlae_index.json is a snapshot committed with the tool; `cs2toue hlae refresh`
rebuilds it from the network.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

from ..config import DATA_DIR
import time

from ..util import http_json, ok, version_tuple, warn

INDEX_PATH = DATA_DIR / "hlae_index.json"
RELEASES_API = "https://api.github.com/repos/advancedfx/advancedfx/releases?per_page=100&page={page}"

RE_CS2_UPDATE = re.compile(r"CS2 update \(?([0-9]+(?:\.[0-9]+){1,3})\)?", re.I)
RE_HOOK_S2 = re.compile(r"AfxHookSource2 ([0-9][0-9.]*)")
RE_HOOK_S1 = re.compile(r"AfxHookSource ([0-9][0-9.]*)")
RE_HOOK_GS = re.compile(r"AfxHookGoldSrc ([0-9][0-9.]*)")


def _entry_from_release(rel: dict) -> dict | None:
    zips = [a for a in rel.get("assets", [])
            if a["name"].lower().endswith(".zip") and not a["name"].lower().endswith(".asc")]
    if not zips:
        return None
    zips.sort(key=lambda a: a["size"], reverse=True)
    asset = zips[0]
    body = rel.get("body") or ""
    return {
        "tag": rel["tag_name"],
        "version": rel["tag_name"].lstrip("vV"),
        "published": (rel.get("published_at") or "")[:10],
        "prerelease": bool(rel.get("prerelease")),
        "zip_name": asset["name"],
        "zip_url": asset["browser_download_url"],
        "zip_size": asset["size"],
        "hook_source2": (RE_HOOK_S2.search(body).group(1) if RE_HOOK_S2.search(body) else ""),
        "hook_source": (RE_HOOK_S1.search(body).group(1) if RE_HOOK_S1.search(body) else ""),
        "hook_goldsrc": (RE_HOOK_GS.search(body).group(1) if RE_HOOK_GS.search(body) else ""),
        "cs2_updates": sorted(set(RE_CS2_UPDATE.findall(body)), key=version_tuple),
        "html_url": rel.get("html_url", ""),
    }


def refresh(max_pages: int = 10) -> list:
    """Pull every release from GitHub and rewrite data/hlae_index.json."""
    entries = []
    for page in range(1, max_pages + 1):
        batch = http_json(RELEASES_API.format(page=page))
        if not batch:
            break
        for rel in batch:
            e = _entry_from_release(rel)
            if e:
                entries.append(e)
        if len(batch) < 100:
            break
    entries.sort(key=lambda e: version_tuple(e["version"]), reverse=True)
    INDEX_PATH.parent.mkdir(parents=True, exist_ok=True)
    INDEX_PATH.write_text(
        json.dumps({"source": "advancedfx/advancedfx releases", "count": len(entries),
                    "releases": entries}, indent=1),
        encoding="utf-8",
    )
    return entries


def age_days() -> float:
    """How old the local snapshot is. Infinite when there is none."""
    if not INDEX_PATH.is_file():
        return float("inf")
    return (time.time() - INDEX_PATH.stat().st_mtime) / 86400.0


def ensure_fresh(max_age_days: float = 1.0, quiet: bool = True) -> list:
    """Refresh the index if it got stale.

    HLAE ships new builds constantly, so a snapshot from install day goes out of date
    within weeks. This runs before anything that reads the index; if the network is
    down, the old snapshot is kept and used - being offline must not break the tool.
    """
    if age_days() <= max_age_days:
        return load()
    try:
        entries = refresh()
        if not quiet:
            ok(f"HLAE index refreshed: {len(entries)} releases")
        return entries
    except Exception as exc:
        if not quiet:
            warn(f"could not refresh the HLAE index ({exc}), using the local copy")
        if INDEX_PATH.is_file():
            return load()
        raise


def load(auto_refresh: bool = False) -> list:
    if not INDEX_PATH.is_file():
        if not auto_refresh:
            raise FileNotFoundError(
                f"{INDEX_PATH} is missing - run: cs2toue hlae refresh"
            )
        return refresh()
    data = json.loads(INDEX_PATH.read_text(encoding="utf-8"))
    return data["releases"]


def get(version: str, entries: list | None = None) -> dict | None:
    entries = entries if entries is not None else load()
    want = version_tuple(version)
    for e in entries:
        if version_tuple(e["version"]) == want or e["tag"] == version:
            return e
    return None


def latest(entries: list | None = None) -> dict:
    entries = entries if entries is not None else load()
    stable = [e for e in entries if not e["prerelease"]] or entries
    return max(stable, key=lambda e: version_tuple(e["version"]))


def with_csgo_support(entries: list | None = None) -> list:
    """Releases that still shipped AfxHookSource (the CS:GO / Source 1 hook)."""
    entries = entries if entries is not None else load()
    return [e for e in entries if e["hook_source"]]


def cs2_update_of(entry: dict) -> tuple:
    """Highest CS2 game version this release was explicitly adjusted to (or (0,)*4)."""
    return max((version_tuple(v) for v in entry.get("cs2_updates", [])), default=(0, 0, 0, 0))
