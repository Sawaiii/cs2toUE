"""Turn gameplay events into placed effects with a position and a lifetime.

This runs at export time, where the tracks are still in memory, so the Unreal side does
not have to match events against players - it just spawns what the list says.

Everything stays in Source space (units, QAngles); the sequence builder converts.
"""

from __future__ import annotations

import math

# how long an effect lives when the demo does not tell us (seconds)
DEFAULT_LIFETIME = {
    "smoke": 18.0,      # CS2 smoke
    "molotov": 7.0,
    "he": 0.7,
    "flash": 0.5,
    "decoy": 15.0,
    "bomb": 1.5,
    "tracer": 0.06,
}

EYE_HEIGHT = 64.0
TRACER_LENGTH = 3000.0      # source units, used when the shot did not kill anybody

# event -> effect kind, and the event that ends it
SPAWNERS = {
    "smokegrenade_detonate": ("smoke", "smokegrenade_expired"),
    "inferno_startburn": ("molotov", "inferno_expire"),
    "hegrenade_detonate": ("he", None),
    "flashbang_detonate": ("flash", None),
    "decoy_started": ("decoy", "decoy_detonate"),
    "bomb_exploded": ("bomb", None),
}


def _pos_from_event(data):
    for keys in (("x", "y", "z"), ("X", "Y", "Z")):
        if all(k in data for k in keys):
            try:
                return [float(data[keys[0]]), float(data[keys[1]]), float(data[keys[2]])]
            except (TypeError, ValueError):
                pass
    return None


def _row_at(rows, time_s):
    """Track row closest to a moment in time (rows are ordered)."""
    if not rows:
        return None
    lo, hi = 0, len(rows) - 1
    while lo < hi:
        mid = (lo + hi) // 2
        if rows[mid][1] < time_s:
            lo = mid + 1
        else:
            hi = mid
    return rows[lo]


def _grenade_end(tracks, actor_id):
    rows = tracks.get(actor_id)
    if not rows:
        return None
    last = rows[-1]
    return [last[2], last[3], last[4]]


def build(scene, tracks, max_tracers: int = 4000) -> list:
    """[{type, time, duration, pos, ...}] from scene.events + the motion tracks."""
    out = []
    by_steamid = {}
    for actor in scene.actors:
        if actor.kind == "player" and actor.steamid:
            by_steamid[str(actor.steamid)] = actor.id
    grenade_by_entity = {}
    for actor in scene.actors:
        if actor.kind == "grenade":
            grenade_by_entity[actor.id.rsplit("_", 1)[-1]] = actor.id

    # lifetimes that the demo actually reports (detonate -> expire)
    ends = {}
    for ev in scene.events:
        for kind, end_event in SPAWNERS.values():
            if end_event and ev["type"] == end_event:
                key = (kind, str(ev["data"].get("entityid", "")))
                ends.setdefault(key, ev["time"])

    # kills, so a tracer can stop at the body it hit instead of in the air
    kills = {}
    for ev in scene.events:
        if ev["type"] != "player_death":
            continue
        att = str(ev["data"].get("attacker_steamid", ""))
        vic = str(ev["data"].get("user_steamid", ""))
        if att and vic:
            kills[(int(ev["tick"]), att)] = vic

    for ev in scene.events:
        spawner = SPAWNERS.get(ev["type"])
        if spawner:
            kind, _end_event = spawner
            data = ev["data"]
            pos = _pos_from_event(data)
            if pos is None:
                entity = str(data.get("entityid", ""))
                actor_id = grenade_by_entity.get(entity)
                if actor_id:
                    pos = _grenade_end(tracks, actor_id)
            if pos is None:
                thrower = by_steamid.get(str(data.get("userid_steamid", ""))
                                         or str(data.get("user_steamid", "")))
                row = _row_at(tracks.get(thrower, []), ev["time"]) if thrower else None
                pos = [row[2], row[3], row[4]] if row else None
            if pos is None:
                continue
            end = ends.get((kind, str(data.get("entityid", ""))))
            duration = (end - ev["time"]) if end and end > ev["time"] else DEFAULT_LIFETIME[kind]
            out.append({
                "type": kind,
                "time": round(ev["time"], 4),
                "duration": round(float(duration), 3),
                "pos": [round(p, 2) for p in pos],
            })

    # tracers: one per shot, from the muzzle along the view direction
    tracers = 0
    for ev in scene.events:
        if ev["type"] != "weapon_fire" or tracers >= max_tracers:
            continue
        sid = str(ev["data"].get("user_steamid", ""))
        actor_id = by_steamid.get(sid)
        row = _row_at(tracks.get(actor_id, []), ev["time"]) if actor_id else None
        if not row:
            continue
        start = [row[2], row[3], row[4] + EYE_HEIGHT]
        victim = kills.get((int(ev["tick"]), sid))
        end = None
        if victim:
            vrow = _row_at(tracks.get(by_steamid.get(victim, ""), []), ev["time"])
            if vrow:
                end = [vrow[2], vrow[3], vrow[4] + 40.0]
        if end is None:
            pitch, yaw = math.radians(row[5]), math.radians(row[6])
            end = [
                start[0] + TRACER_LENGTH * math.cos(pitch) * math.cos(yaw),
                start[1] + TRACER_LENGTH * math.cos(pitch) * math.sin(yaw),
                start[2] - TRACER_LENGTH * math.sin(pitch),
            ]
        out.append({
            "type": "tracer",
            "time": round(ev["time"], 4),
            "duration": DEFAULT_LIFETIME["tracer"],
            "pos": [round(p, 2) for p in start],
            "end": [round(p, 2) for p in end],
            "weapon": str(ev["data"].get("weapon", "")),
            "actor": actor_id,
        })
        tracers += 1

    out.sort(key=lambda e: e["time"])
    return out
