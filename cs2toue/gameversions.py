"""History of CS2 game versions, so a demo can be dated.

A demo header carries a build number; HLAE changelogs talk about versions like
`1.41.6.8`. This table is the bridge between the two. It is built from the commit
history of `game/csgo/steam.inf` in SteamDatabase/GameTracking-CS2, where every game
update leaves one entry:

    client   2000884      the build number Steam reports
    patch    1.41.7.5     the version HLAE changelogs refer to
    revision 10905598     the engine changelist
    date     2026-08-12

Matching is deliberately careful. A demo build number is matched against every
identifier the table has, and when nothing lines up the answer is "unknown" instead of
a guess - a wrong game version would lead to a wrong HLAE recommendation.
"""

from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass
from pathlib import Path

from .config import DATA_DIR
from .util import http_json, ok, version_tuple, warn

TABLE_PATH = DATA_DIR / "cs2_versions.json"
REPO = "SteamDatabase/GameTracking-CS2"
INF_PATH = "game/csgo/steam.inf"
COMMITS_API = f"https://api.github.com/repos/{REPO}/commits?path={INF_PATH}&per_page=100"
RAW = f"https://raw.githubusercontent.com/{REPO}/{{sha}}/{INF_PATH}"

MAX_AGE_DAYS = 7.0        # the game updates about weekly


@dataclass
class GameVersion:
    client: int = 0
    patch: str = ""
    revision: int = 0
    date: str = ""
    exact: bool = False       # did the demo build number match, or is this the nearest?
    matched_on: str = ""      # which column matched

    @property
    def label(self) -> str:
        if not self.patch:
            return "неизвестно"
        near = "" if self.exact else " (ближайшая известная)"
        return f"CS2 {self.patch}, сборка {self.client}, {self.date}{near}"


# ------------------------------------------------------------------ storage

def load() -> list:
    if not TABLE_PATH.is_file():
        return []
    try:
        return json.loads(TABLE_PATH.read_text(encoding="utf-8")).get("versions", [])
    except Exception:
        return []


def age_days() -> float:
    if not TABLE_PATH.is_file():
        return float("inf")
    return (time.time() - TABLE_PATH.stat().st_mtime) / 86400.0


def _parse_inf(text: str) -> dict:
    values = {}
    for line in text.splitlines():
        if "=" in line:
            k, _, v = line.partition("=")
            values[k.strip()] = v.strip()
    return values


def refresh(quiet: bool = True) -> list:
    """Append the game updates published since the newest entry we have."""
    import urllib.request

    rows = load()
    known = {r["client"] for r in rows}
    newest_date = max((r["date"] for r in rows), default="")
    try:
        commits = http_json(COMMITS_API)
    except Exception as exc:
        if not quiet:
            warn(f"не удалось обновить таблицу версий CS2: {exc}")
        return rows

    added = 0
    for commit in commits:
        date = commit["commit"]["committer"]["date"][:10]
        if newest_date and date < newest_date:
            break
        try:
            req = urllib.request.Request(RAW.format(sha=commit["sha"]),
                                         headers={"User-Agent": "cs2toUE/1.0"})
            with urllib.request.urlopen(req, timeout=30) as r:
                inf = _parse_inf(r.read().decode("utf-8", "replace"))
        except Exception:
            continue
        client = inf.get("ClientVersion", "")
        patch = inf.get("PatchVersion", "")
        if not client.isdigit() or not patch or int(client) in known:
            continue
        rows.append({"client": int(client), "patch": patch,
                     "revision": int(inf.get("SourceRevision", "0") or 0),
                     "date": date, "version_date": inf.get("VersionDate", "")})
        known.add(int(client))
        added += 1

    rows.sort(key=lambda r: r["client"])
    TABLE_PATH.parent.mkdir(parents=True, exist_ok=True)
    TABLE_PATH.write_text(json.dumps({"source": f"{REPO} :: {INF_PATH}",
                                      "count": len(rows), "versions": rows}, indent=1),
                          encoding="utf-8")
    if added and not quiet:
        ok(f"таблица версий CS2 дополнена: +{added}, всего {len(rows)}")
    return rows


def ensure_fresh(max_age_days: float = MAX_AGE_DAYS, quiet: bool = True) -> list:
    if age_days() <= max_age_days:
        return load()
    try:
        return refresh(quiet=quiet)
    except Exception:
        return load()


# ------------------------------------------------------------------ matching

def match(build_num: int, rows: list | None = None) -> GameVersion:
    """Find the game version a demo was recorded on.

    The build number is compared with every identifier in the table; if none matches
    exactly, the closest earlier build is reported and flagged as approximate.
    """
    rows = rows if rows is not None else load()
    if not rows or not build_num:
        return GameVersion()

    for column in ("client", "revision"):
        for r in rows:
            if r.get(column) == build_num:
                return GameVersion(client=r["client"], patch=r["patch"],
                                   revision=r.get("revision", 0), date=r["date"],
                                   exact=True, matched_on=column)

    # nearest earlier build, but only inside a range where the numbering is comparable
    for column in ("client", "revision"):
        candidates = [r for r in rows if 0 < r.get(column, 0) <= build_num]
        if not candidates:
            continue
        nearest = max(candidates, key=lambda r: r[column])
        spread = max(r[column] for r in rows) - min(r[column] for r in rows if r[column])
        if spread and (build_num - nearest[column]) <= spread:
            return GameVersion(client=nearest["client"], patch=nearest["patch"],
                               revision=nearest.get("revision", 0), date=nearest["date"],
                               exact=False, matched_on=column)
    return GameVersion()


def hlae_for_patch(patch: str, hlae_entries: list):
    """The HLAE build that suits a client on this game version."""
    from .hlae import resolver
    if not patch:
        return None
    stable = [e for e in hlae_entries if not e.get("prerelease")] or hlae_entries
    return resolver._newest_supporting_game(stable, patch)


def hlae_at_date(date: str, hlae_entries: list):
    """The HLAE build that was current on that day - what a person means by
    "the HLAE of that time"."""
    if not date:
        return None
    published = [e for e in hlae_entries
                 if not e.get("prerelease") and e.get("published") and e["published"] <= date]
    if not published:
        return None
    return max(published, key=lambda e: e["published"])


def compare(demo_patch: str, installed_patch: str) -> str:
    """Plain words about how far apart the demo and the installed client are."""
    if not demo_patch or not installed_patch:
        return ""
    a, b = version_tuple(demo_patch), version_tuple(installed_patch)
    if a == b:
        return "совпадает с вашим клиентом"
    if a < b:
        return "старше вашего клиента - обычно такие демки CS2 проигрывает нормально"
    return ("новее вашего клиента - обновите CS2, иначе демка может не проиграться")
