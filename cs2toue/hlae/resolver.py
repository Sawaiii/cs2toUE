"""Pick the HLAE build that fits a given demo.

How HLAE version selection actually works (this trips a lot of people up):

* HLAE is a hook injected into a *running game*, so the release must match the game
  binary you play the demo with, not the demo file itself.
* For CS:GO demos (HL2DEMO) you need AfxHookSource, which only ships in HLAE releases
  from the CS:GO era - the newest HLAE has no Source 1 CS hook for it any more.
* For CS2 demos (PBDEMS2) you need AfxHookSource2, and the release must not be newer
  than the CS2 client you have installed: each "Adjusted to CS2 update (x.y.z.w)" note
  in the HLAE changelog marks a release that only works from that game patch onwards.

So: the demo tells us which *family* of HLAE is needed, the installed game tells us the
exact build inside that family, and data/hlae_rules.json lets a user pin any demo build
range to an exact HLAE version once they have verified a combination works.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

from ..config import DATA_DIR
from ..util import version_tuple
from . import index as hlae_index

RULES_PATH = DATA_DIR / "hlae_rules.json"

DEFAULT_RULES = {
    "version": 1,
    "comment": "Edit freely: pins win over policies. Add a pin once you verified a combo.",
    "pins": [],
    "allow_prerelease": False,
    "policies": {
        "source2": {"strategy": "match_installed_game", "min_version": "2.148.0"},
        "source1": {"strategy": "newest_with_csgo_hook", "max_version": ""},
    },
}


@dataclass
class Resolution:
    version: str = ""
    entry: dict = field(default_factory=dict)
    reason: str = ""
    warnings: list = field(default_factory=list)
    hook_dll: str = "AfxHookSource2.dll"


def load_rules() -> dict:
    if RULES_PATH.is_file():
        return json.loads(RULES_PATH.read_text(encoding="utf-8"))
    RULES_PATH.parent.mkdir(parents=True, exist_ok=True)
    RULES_PATH.write_text(json.dumps(DEFAULT_RULES, indent=2), encoding="utf-8")
    return dict(DEFAULT_RULES)


def save_rules(rules: dict) -> Path:
    RULES_PATH.write_text(json.dumps(rules, indent=2, ensure_ascii=False), encoding="utf-8")
    return RULES_PATH


def add_pin(match: dict, hlae_version: str, note: str = "") -> dict:
    rules = load_rules()
    rules.setdefault("pins", []).insert(0, {"match": match, "hlae": hlae_version, "note": note})
    save_rules(rules)
    return rules


def _pin_matches(match: dict, demo) -> bool:
    if "engine" in match and match["engine"] != demo.engine:
        return False
    if "game" in match and match["game"] != demo.game:
        return False
    if "build_min" in match and demo.build_num < int(match["build_min"]):
        return False
    if "build_max" in match and demo.build_num > int(match["build_max"]):
        return False
    if "netproto_min" in match and demo.network_protocol < int(match["netproto_min"]):
        return False
    if "netproto_max" in match and demo.network_protocol > int(match["netproto_max"]):
        return False
    if "demo_version_name" in match and match["demo_version_name"] != demo.demo_version_name:
        return False
    return True


def _newest_supporting_game(entries: list, installed_version: str) -> dict | None:
    """Newest release that has not yet been adjusted to a CS2 patch newer than ours.

    Releases are walked oldest -> newest carrying the highest "adjusted to" marker seen
    so far, because a release without a marker inherits the requirement of the one
    before it.
    """
    want = version_tuple(installed_version)
    ordered = sorted(entries, key=lambda e: version_tuple(e["version"]))
    best = None
    required = (0, 0, 0, 0)
    for e in ordered:
        if not e.get("hook_source2"):
            continue
        required = max(required, hlae_index.cs2_update_of(e))
        if required <= want:
            best = e
    return best


def resolve(demo, cfg=None, entries: list | None = None, forced: str = "") -> Resolution:
    entries = entries if entries is not None else hlae_index.load()
    rules = load_rules()
    res = Resolution()

    if forced and forced != "auto":
        if forced == "latest":
            entry = hlae_index.latest(entries)
        else:
            entry = hlae_index.get(forced, entries)
        if not entry:
            raise ValueError(f"HLAE version {forced} is not in the index (try: hlae refresh)")
        res.entry, res.version = entry, entry["version"]
        res.reason = "forced by user / config"
        res.hook_dll = "AfxHookSource2.dll" if demo.engine == "source2" else "AfxHookSource.dll"
        return res

    if not rules.get("allow_prerelease"):
        # pre-releases are opt-in: they usually target a game build that is not public yet
        entries = [e for e in entries if not e.get("prerelease")] or entries

    for pin in rules.get("pins", []):
        if _pin_matches(pin.get("match", {}), demo):
            entry = hlae_index.get(pin["hlae"], entries)
            if entry:
                res.entry, res.version = entry, entry["version"]
                res.reason = f"pinned rule: {pin.get('note') or pin.get('match')}"
                res.hook_dll = ("AfxHookSource2.dll" if demo.engine == "source2"
                                else "AfxHookSource.dll")
                return res
            res.warnings.append(f"pin points at unknown HLAE {pin['hlae']}, ignoring")

    if demo.engine == "source1":
        pol = rules["policies"]["source1"]
        cands = hlae_index.with_csgo_support(entries)
        if pol.get("max_version"):
            cap = version_tuple(pol["max_version"])
            cands = [e for e in cands if version_tuple(e["version"]) <= cap]
        if not cands:
            raise ValueError("no HLAE release in the index ships AfxHookSource (CS:GO hook)")
        entry = max(cands, key=lambda e: version_tuple(e["version"]))
        res.entry, res.version = entry, entry["version"]
        res.hook_dll = "AfxHookSource.dll"
        res.reason = (f"CS:GO demo (netproto {demo.network_protocol}) -> newest HLAE that still "
                      f"ships AfxHookSource ({entry['published']})")
        res.warnings.append(
            "CS:GO demos need a CS:GO client; current CS2 cannot play them. Use a legacy "
            "csgo.exe install (csgo_legacy branch) together with this HLAE build."
        )
        return res

    # --- Source 2 / CS2
    pol = rules["policies"]["source2"]
    installed = ""
    if cfg is not None and getattr(cfg, "cs2_exe", ""):
        from .. import steam
        installed = steam.installed_version(cfg.cs2_exe).get("PatchVersion", "")

    entry = None
    if installed and pol.get("strategy") == "match_installed_game":
        entry = _newest_supporting_game(entries, installed)
        if entry:
            res.reason = (f"CS2 demo (build {demo.build_num}); installed CS2 is {installed}, so the "
                          f"newest HLAE not adjusted past it is {entry['version']}")
    if not entry:
        entry = hlae_index.latest(entries)
        res.reason = (f"CS2 demo (build {demo.build_num}); installed CS2 version unknown, "
                      f"falling back to the latest HLAE")
        res.warnings.append(
            "Set cs2_exe in the config (cs2toue setup) so the HLAE build can be matched to "
            "your installed CS2 patch."
        )
    minv = pol.get("min_version")
    if minv and version_tuple(entry["version"]) < version_tuple(minv):
        res.warnings.append(f"selected HLAE {entry['version']} is older than the CS2 minimum {minv}")

    res.entry, res.version = entry, entry["version"]
    res.hook_dll = "AfxHookSource2.dll"
    return res
