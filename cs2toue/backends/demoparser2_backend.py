"""CS2 (Source 2) data backend built on demoparser2 (LaihoE/demoparser).

Turns a .dem into the cs2toUE scene format: one motion track per player and per
grenade projectile, plus rounds and gameplay events.
"""

from __future__ import annotations

import math
from pathlib import Path

from ..scene import Actor, Scene
from ..util import Fail, info, warn

# props we always want
CORE_PROPS = ["X", "Y", "Z", "pitch", "yaw", "health", "is_alive", "team_name",
              "active_weapon_name"]
# props that come and go between demoparser2 / CS2 versions - probed one by one
OPTIONAL_PROPS = ["armor_value", "is_scoped", "is_walking", "is_airborne", "duck_amount",
                  "velocity_X", "velocity_Y", "velocity_Z", "flash_duration",
                  "is_defusing", "has_defuser", "has_helmet", "player_name"]

EVENTS = ["round_start", "round_end", "round_officially_ended", "round_freeze_end",
          "player_death", "player_hurt", "weapon_fire", "player_jump", "player_footstep",
          "bomb_planted", "bomb_defused", "bomb_exploded", "bomb_begindefuse",
          "hegrenade_detonate", "flashbang_detonate", "smokegrenade_detonate",
          "smokegrenade_expired", "inferno_startburn", "inferno_expire", "decoy_started"]

EYE_HEIGHT_STAND = 64.0   # source units
EYE_HEIGHT_DUCK = 46.0


def available() -> bool:
    try:
        import demoparser2  # noqa: F401
        return True
    except Exception:
        return False


def _parser(demo_path):
    try:
        from demoparser2 import DemoParser
    except Exception as exc:
        raise Fail(
            "demoparser2 is not installed. Run install.cmd, or: pip install demoparser2 pandas"
        ) from exc
    return DemoParser(str(demo_path))


def _probe_props(parser, props, probe_ticks):
    """Keep only the props this demo / demoparser2 build actually knows."""
    try:
        parser.parse_ticks(props, ticks=probe_ticks)
        return props
    except Exception:
        pass
    good = []
    for p in props:
        try:
            parser.parse_ticks([p], ticks=probe_ticks)
            good.append(p)
        except Exception:
            warn(f"property not available in this demo, skipping: {p}")
    return good


def _columns(df, names):
    """{name: numpy array or None} - avoids itertuples column renaming surprises."""
    return {n: (df[n].to_numpy() if n in df.columns else None) for n in names}


def _at(arr, i, default=None):
    if arr is None:
        return default
    v = arr[i]
    return default if v is None else v


def _text(v) -> str:
    if v is None:
        return ""
    if isinstance(v, float) and math.isnan(v):
        return ""
    s = str(v)
    return "" if s in ("nan", "None", "<NA>") else s


def _num(v, default=0.0):
    try:
        f = float(v)
        return default if math.isnan(f) else f
    except (TypeError, ValueError):
        return default


def parse(demo_path, demo_info, out_dir, tick_start=0, tick_end=0, step=1,
          with_grenades=True, with_events=True, camera=None, max_players=0) -> Scene:
    parser = _parser(demo_path)
    tick_end = tick_end or demo_info.playback_ticks or 0
    if not tick_end:
        raise Fail("could not determine demo length; pass --to explicitly")
    step = max(1, int(step))
    ticks = list(range(int(tick_start), int(tick_end) + 1, step))
    if not ticks:
        raise Fail("empty tick range")
    info(f"parsing ticks {ticks[0]}..{ticks[-1]} step {step} ({len(ticks)} samples)")

    props = CORE_PROPS + _probe_props(parser, OPTIONAL_PROPS, ticks[:2])
    df = parser.parse_ticks(props, ticks=ticks)
    if df is None or len(df) == 0:
        raise Fail("demoparser2 returned no ticks - is the tick range inside the demo?")

    tickrate = demo_info.tickrate or 64.0
    t0 = ticks[0]

    sc = Scene()
    sc.meta = {
        "demo": str(Path(demo_path).name),
        "demo_path": str(demo_path),
        "map": demo_info.map_name,
        "game": demo_info.game,
        "build_num": demo_info.build_num,
        "tickrate": tickrate,
        "tick_start": ticks[0],
        "tick_end": ticks[-1],
        "tick_step": step,
        "sample_fps": round(tickrate / step, 3),
        "space": "source",
        "units": "source units (1 unit = 1 inch), angles = QAngle degrees",
        "server": demo_info.server_name,
        "backend": "demoparser2",
    }

    # ------------------------------------------------------------- rounds
    round_starts = list(demo_info.round_start_ticks or [])
    tracks = {}

    # ------------------------------------------------------------- players
    has = {c: (c in df.columns) for c in props}
    key = "steamid" if "steamid" in df.columns else "name"
    groups = list(df.groupby(key, sort=False))
    if max_players:
        groups = groups[:max_players]
    info(f"players in range: {len(groups)}")

    weapons = set()
    for pid, g in groups:
        g = g.sort_values("tick")
        first = g.iloc[0]
        name = str(first.get("name", "") or first.get("player_name", "") or pid)
        team = str(first.get("team_name", "") or "")
        actor_id = f"player_{pid}"
        # column arrays instead of itertuples: faster, and immune to pandas renaming
        col = _columns(g, ("tick", "X", "Y", "Z", "pitch", "yaw", "health", "is_alive",
                           "active_weapon_name", "duck_amount", "is_airborne",
                           "velocity_X", "velocity_Y", "velocity_Z"))
        rows = []
        prev = None
        for i in range(len(g)):
            tick = int(_at(col["tick"], i, 0))
            x, y, z = (_num(_at(col["X"], i)), _num(_at(col["Y"], i)), _num(_at(col["Z"], i)))
            # speed drives the animation choice in Unreal; prefer the real velocity prop
            move_yaw = ""
            if col["velocity_X"] is not None:
                vx, vy = _num(_at(col["velocity_X"], i)), _num(_at(col["velocity_Y"], i))
                speed = math.hypot(vx, vy)
                if speed > 1.0:
                    move_yaw = round(math.degrees(math.atan2(vy, vx)), 2)
            elif prev is not None:
                dt = (tick - prev[0]) / tickrate
                dx, dy = x - prev[1], y - prev[2]
                speed = math.hypot(dx, dy) / dt if dt > 0 else 0.0
                if speed > 1.0:
                    move_yaw = round(math.degrees(math.atan2(dy, dx)), 2)
            else:
                speed = 0.0
            prev = (tick, x, y, z)
            airborne = _at(col["is_airborne"], i, False)
            duck = _num(_at(col["duck_amount"], i), 0.0)
            weapon = _text(_at(col["active_weapon_name"], i, ""))
            if weapon:
                weapons.add(weapon)
            alive = _at(col["is_alive"], i, True)
            rows.append([
                tick,
                round((tick - t0) / tickrate, 6),
                round(x, 4), round(y, 4), round(z, 4),
                round(_num(_at(col["pitch"], i)), 4), round(_num(_at(col["yaw"], i)), 4), 0.0,
                "",                                   # fov: players carry none
                1 if (alive is None or bool(alive)) else 0,
                int(_num(_at(col["health"], i), 0)),
                weapon,
                round(duck, 3),
                round(speed, 2),
                1 if airborne else 0,
                move_yaw,
            ])
        if not rows:
            continue
        last = g.iloc[-1]
        sc.actors.append(Actor(
            id=actor_id, kind="player", name=name, steamid=str(pid), team=team,
            meta={"team_last": str(last.get("team_name", "") or "")},
        ))
        tracks[actor_id] = rows

    # ------------------------------------------------------------- grenades
    if with_grenades:
        try:
            gdf = parser.parse_grenades()
        except Exception as exc:
            gdf = None
            warn(f"grenade trajectories unavailable: {exc}")
        if gdf is not None and len(gdf):
            gdf = gdf[(gdf["tick"] >= ticks[0]) & (gdf["tick"] <= ticks[-1])]
            gkey = "entity_id" if "entity_id" in gdf.columns else "grenade_type"
            for gid, g in gdf.groupby(gkey, sort=False):
                g = g.sort_values("tick")
                gtype = str(g.iloc[0].get("grenade_type", "grenade"))
                thrower = str(g.iloc[0].get("name", "") or "")
                actor_id = f"grenade_{gtype}_{gid}"
                col = _columns(g, ("tick", "X", "Y", "Z"))
                rows = []
                for i in range(len(g)):
                    tick = int(_at(col["tick"], i, 0))
                    rows.append([
                        tick, round((tick - t0) / tickrate, 6),
                        round(_num(_at(col["X"], i)), 4), round(_num(_at(col["Y"], i)), 4),
                        round(_num(_at(col["Z"], i)), 4),
                        0.0, 0.0, 0.0, "", 1, 0, gtype, 0.0, 0.0, 0, "",
                    ])
                if len(rows) < 2:
                    continue
                sc.actors.append(Actor(id=actor_id, kind="grenade", name=gtype,
                                       meta={"thrower": thrower, "type": gtype}))
                tracks[actor_id] = rows

    # ------------------------------------------------------------- events
    if with_events:
        try:
            parsed = parser.parse_events(EVENTS)
        except Exception as exc:
            parsed = []
            warn(f"events unavailable: {exc}")
        for name, edf in parsed or []:
            if edf is None or not len(edf):
                continue
            cols = [c for c in edf.columns if c != "tick"][:12]
            for d in edf.to_dict("records"):
                tick = int(_num(d.get("tick"), -1))
                if tick < ticks[0] or tick > ticks[-1]:
                    continue
                payload = {}
                for c in cols:
                    v = d.get(c)
                    if v is None or (isinstance(v, float) and math.isnan(v)):
                        continue
                    payload[c] = v if isinstance(v, (int, float, str, bool)) else str(v)
                sc.events.append({
                    "tick": tick,
                    "time": round((tick - t0) / tickrate, 6),
                    "type": name,
                    "data": payload,
                })
                if name == "round_start" and tick not in round_starts:
                    round_starts.append(tick)
        sc.events.sort(key=lambda e: (e["tick"], e["type"]))

    # ------------------------------------------------------------- camera
    if camera:
        cam_actor, cam_rows = _make_camera(camera, sc, tracks, tickrate, t0)
        if cam_actor:
            sc.actors.append(cam_actor)
            tracks[cam_actor.id] = cam_rows

    # ------------------------------------------------------------- effects
    if with_events and sc.events:
        from .. import effects as fx
        try:
            sc.effects = fx.build(sc, tracks)
            info(f"effects placed: {len(sc.effects)}")
        except Exception as exc:
            warn(f"effects could not be built: {exc}")

    round_starts = sorted(set(int(t) for t in round_starts))
    sc.rounds = [{"number": i + 1, "start_tick": t,
                  "end_tick": (round_starts[i + 1] - 1 if i + 1 < len(round_starts) else ticks[-1])}
                 for i, t in enumerate(round_starts)]
    sc.weapons = sorted(weapons)

    sc.save(out_dir, tracks)
    return sc


def _make_camera(camera_spec, sc, tracks, tickrate, t0):
    """camera_spec: 'player:<steamid|name>' - a POV camera riding a player."""
    spec = str(camera_spec)
    if not spec.startswith("player:"):
        return None, None
    want = spec.split(":", 1)[1].strip().lower()
    target = None
    for a in sc.actors:
        if a.kind != "player":
            continue
        if want in (a.steamid.lower(), a.name.lower()) or want == "first":
            target = a
            break
    if target is None:
        warn(f"camera target not found: {spec}")
        return None, None
    rows = []
    for r in tracks[target.id]:
        row = list(r)
        row[4] = round(row[4] + EYE_HEIGHT_STAND, 4)   # z -> eye height
        row[8] = 90.0                                   # fov
        row[11] = "camera"
        rows.append(row)
    actor = Actor(id="camera_pov", kind="camera", name=f"POV {target.name}",
                  meta={"follows": target.id, "fov": 90.0})
    info(f"camera track from player {target.name}")
    return actor, rows
