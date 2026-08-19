"""Build data/cs2_versions.json - the history of CS2 game versions.

Source: the commit history of game/csgo/steam.inf in SteamDatabase/GameTracking-CS2.
Every commit there is one game update, and the file itself carries the numbers that
identify it:

    ClientVersion=2000884      the build number Steam reports
    PatchVersion=1.41.7.5      the version HLAE changelogs refer to
    SourceRevision=10905598    the engine changelist
    VersionDate=Aug 12 2026

A demo header carries a build number, so with this table a demo can be dated and tied
to the HLAE release that was current at the time.

    python tools/build_cs2_versions.py [--since 2023-01-01]
"""

import json
import re
import sys
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from cs2toue.config import DATA_DIR

REPO = "SteamDatabase/GameTracking-CS2"
PATH = "game/csgo/steam.inf"
COMMITS = f"https://api.github.com/repos/{REPO}/commits?path={PATH}&per_page=100&page={{page}}"
RAW = f"https://raw.githubusercontent.com/{REPO}/{{sha}}/{PATH}"
OUT = DATA_DIR / "cs2_versions.json"

UA = {"User-Agent": "cs2toUE/1.0 version-table-builder"}


def fetch(url: str) -> str:
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=60) as r:
        return r.read().decode("utf-8", "replace")


def commit_list(max_pages: int = 10) -> list:
    out = []
    for page in range(1, max_pages + 1):
        batch = json.loads(fetch(COMMITS.format(page=page)))
        if not batch:
            break
        for c in batch:
            out.append({"sha": c["sha"], "date": c["commit"]["committer"]["date"][:10]})
        if len(batch) < 100:
            break
    return out


def parse_inf(text: str) -> dict:
    values = {}
    for line in text.splitlines():
        if "=" in line:
            k, _, v = line.partition("=")
            values[k.strip()] = v.strip()
    return values


def one(commit: dict) -> dict | None:
    try:
        inf = parse_inf(fetch(RAW.format(sha=commit["sha"])))
    except Exception:
        return None
    client = inf.get("ClientVersion", "")
    patch = inf.get("PatchVersion", "")
    if not client or not patch:
        return None
    return {
        "client": int(client) if client.isdigit() else 0,
        "patch": patch,
        "revision": int(inf.get("SourceRevision", "0") or 0),
        "date": commit["date"],
        "version_date": inf.get("VersionDate", ""),
    }


def main():
    print("reading the commit history of", PATH)
    commits = commit_list()
    print(f"  {len(commits)} updates")

    rows = []
    with ThreadPoolExecutor(max_workers=8) as pool:
        for i, row in enumerate(pool.map(one, commits), 1):
            if row:
                rows.append(row)
            if i % 50 == 0:
                print(f"  {i}/{len(commits)}")

    by_client = {}
    for row in rows:
        keep = by_client.get(row["client"])
        if keep is None or row["date"] < keep["date"]:
            by_client[row["client"]] = row       # keep the earliest sighting
    rows = sorted(by_client.values(), key=lambda r: r["client"])

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps({
        "source": f"{REPO} :: {PATH}",
        "count": len(rows),
        "versions": rows,
    }, indent=1), encoding="utf-8")
    print(f"written: {OUT}  ({len(rows)} versions, "
          f"{rows[0]['date']} .. {rows[-1]['date']})")
    print(f"  oldest: client {rows[0]['client']}  patch {rows[0]['patch']}")
    print(f"  newest: client {rows[-1]['client']}  patch {rows[-1]['patch']}")


if __name__ == "__main__":
    main()
