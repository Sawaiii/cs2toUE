"""The intermediate scene format that sits between a demo and Unreal Engine.

A scene folder looks like this:

    <name>/
        scene.json          meta, actor list, rounds, events
        tracks/<id>.csv     one motion track per actor

Everything in the tracks is still in *Source space* (units, QAngles, game ticks) - the
conversion to Unreal happens in the UE-side script, where the scale is a user choice.
CSV was picked on purpose: it streams, it is diffable, and the Unreal Python API can
read it with the standard library.
"""

from __future__ import annotations

import csv
import json
from dataclasses import dataclass, field, asdict
from pathlib import Path

FORMAT = "cs2toue.scene"
FORMAT_VERSION = 1

TRACK_COLUMNS = ["tick", "time", "x", "y", "z", "pitch", "yaw", "roll",
                 "fov", "alive", "health", "weapon", "duck", "speed"]


@dataclass
class Actor:
    id: str
    kind: str = "player"          # player | camera | grenade | bomb
    name: str = ""
    steamid: str = ""
    team: str = ""
    color: list = field(default_factory=list)
    track: str = ""               # relative path of the csv
    frames: int = 0
    meta: dict = field(default_factory=dict)


@dataclass
class Scene:
    meta: dict = field(default_factory=dict)
    actors: list = field(default_factory=list)
    events: list = field(default_factory=list)
    rounds: list = field(default_factory=list)
    effects: list = field(default_factory=list)
    weapons: list = field(default_factory=list)

    # ------------------------------------------------------------------ write

    def save(self, out_dir, tracks: dict) -> Path:
        """tracks: {actor_id: iterable of row dicts or row lists in TRACK_COLUMNS order}"""
        out_dir = Path(out_dir)
        (out_dir / "tracks").mkdir(parents=True, exist_ok=True)
        by_id = {a.id: a for a in self.actors}
        for actor_id, rows in tracks.items():
            actor = by_id.get(actor_id)
            if actor is None:
                continue
            rel = f"tracks/{actor_id}.csv"
            path = out_dir / rel
            n = 0
            with open(path, "w", encoding="utf-8", newline="") as fh:
                w = csv.writer(fh)
                w.writerow(TRACK_COLUMNS)
                for row in rows:
                    if isinstance(row, dict):
                        row = [row.get(c, "") for c in TRACK_COLUMNS]
                    w.writerow(row)
                    n += 1
            actor.track = rel
            actor.frames = n
        self.meta.setdefault("format", FORMAT)
        self.meta.setdefault("format_version", FORMAT_VERSION)
        self.meta.setdefault("space", "source")
        payload = {
            # "_dir" is a runtime helper set by load(); a machine path in the file
            # would churn on every save and break nothing but look like data
            "meta": {k: v for k, v in self.meta.items() if k != "_dir"},
            "rounds": self.rounds,
            "effects": self.effects,
            "weapons": self.weapons,
            "actors": [asdict(a) for a in self.actors],
            "events": self.events,
        }
        path = out_dir / "scene.json"
        path.write_text(json.dumps(payload, indent=1, ensure_ascii=False), encoding="utf-8")
        return path

    # ------------------------------------------------------------------- read

    @classmethod
    def load(cls, scene_dir) -> "Scene":
        scene_dir = Path(scene_dir)
        path = scene_dir / "scene.json" if scene_dir.is_dir() else scene_dir
        raw = json.loads(Path(path).read_text(encoding="utf-8"))
        sc = cls(meta=raw.get("meta", {}), events=raw.get("events", []),
                 effects=raw.get("effects", []),
                 rounds=raw.get("rounds", []), weapons=raw.get("weapons", []))
        sc.actors = [Actor(**a) for a in raw.get("actors", [])]
        sc.meta["_dir"] = str(Path(path).parent)
        return sc

    def read_track(self, actor: Actor):
        base = Path(self.meta.get("_dir", "."))
        with open(base / actor.track, "r", encoding="utf-8", newline="") as fh:
            for row in csv.DictReader(fh):
                yield row
