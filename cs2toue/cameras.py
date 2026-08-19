"""Camera rigs for moviemaking.

Raw demo data is jittery: a player looks around at 128 tick and every twitch lands in the
track. A camera built straight from that is unwatchable, so every rig here goes through
the same two stages - build a raw path, then smooth it properly (positions with a
gaussian window, angles through unit vectors so 179 -> -179 never causes a spin).

Rigs:
    pov     first person, from the eyes
    follow  third person chase camera with lag
    orbit   circles the target
    static  fixed point that keeps looking at the target
    action  frames the centre of the action (the shooter and whoever is dying)

The result is a normal camera track in the scene *and* an HLAE .cam file, so the same
move can be rendered in Unreal or played back inside CS2 with `mirv_camio import`.
"""

from __future__ import annotations

import math

from .hlae import camio
from .scene import TRACK_COLUMNS

# indices into a track row
I_TICK, I_TIME, I_X, I_Y, I_Z, I_PITCH, I_YAW, I_ROLL, I_FOV, I_ALIVE = range(10)

EYE_HEIGHT = 64.0
DUCK_EYE_HEIGHT = 46.0

DEFAULTS = {
    "pov": {"smooth": 0.12, "fov": 90.0, "distance": 0.0, "height": 0.0},
    "follow": {"smooth": 0.45, "fov": 75.0, "distance": 180.0, "height": 70.0},
    "orbit": {"smooth": 0.25, "fov": 70.0, "distance": 320.0, "height": 120.0},
    "static": {"smooth": 0.30, "fov": 60.0, "distance": 0.0, "height": 0.0},
    "action": {"smooth": 0.60, "fov": 70.0, "distance": 420.0, "height": 200.0},
}


# --------------------------------------------------------------------- helpers

def _angles_to_vec(pitch, yaw):
    p, y = math.radians(pitch), math.radians(yaw)
    return (math.cos(p) * math.cos(y), math.cos(p) * math.sin(y), -math.sin(p))


def _vec_to_angles(x, y, z):
    dist = math.hypot(x, y)
    yaw = math.degrees(math.atan2(y, x))
    pitch = math.degrees(math.atan2(-z, dist)) if (dist or z) else 0.0
    return pitch, yaw


def _gaussian_kernel(window: int):
    if window <= 1:
        return [1.0]
    sigma = window / 3.0
    half = window // 2
    ker = [math.exp(-0.5 * (i / sigma) ** 2) for i in range(-half, half + 1)]
    total = sum(ker)
    return [k / total for k in ker]


def smooth_series(values, window: int):
    """Gaussian smoothing with edge clamping (no drift at the ends of a clip)."""
    if window <= 1 or len(values) < 3:
        return list(values)
    ker = _gaussian_kernel(window)
    half = len(ker) // 2
    n = len(values)
    out = []
    for i in range(n):
        acc = 0.0
        for k, weight in enumerate(ker):
            j = min(n - 1, max(0, i + k - half))
            acc += values[j] * weight
        out.append(acc)
    return out


def smooth_angles(pitches, yaws, window: int):
    """Smooth a direction, not two numbers - this is what stops the 180 degree flips."""
    if window <= 1 or len(pitches) < 3:
        return list(pitches), list(yaws)
    vx, vy, vz = [], [], []
    for p, y in zip(pitches, yaws):
        x, yy, z = _angles_to_vec(p, y)
        vx.append(x)
        vy.append(yy)
        vz.append(z)
    vx, vy, vz = (smooth_series(vx, window), smooth_series(vy, window),
                  smooth_series(vz, window))
    out_p, out_y = [], []
    for x, yy, z in zip(vx, vy, vz):
        length = math.sqrt(x * x + yy * yy + z * z) or 1.0
        p, y = _vec_to_angles(x / length, yy / length, z / length)
        out_p.append(p)
        out_y.append(y)
    return out_p, out_y


def _window_frames(seconds: float, fps: float) -> int:
    w = int(round(seconds * fps))
    return w + 1 if w % 2 == 0 else w      # odd window keeps the path centred


def _f(row, idx, default=0.0):
    try:
        return float(row[idx])
    except (TypeError, ValueError, IndexError):
        return default


# --------------------------------------------------------------------- rigs

def _eye(row):
    duck = _f(row, 12, 0.0)
    return _f(row, I_Z) + (DUCK_EYE_HEIGHT if duck > 0.5 else EYE_HEIGHT)


def _rig_pov(target_rows, _others, opt):
    path = []
    for row in target_rows:
        path.append((_f(row, I_TIME), _f(row, I_X), _f(row, I_Y), _eye(row),
                     _f(row, I_PITCH), _f(row, I_YAW)))
    return path


def _rig_follow(target_rows, _others, opt):
    dist, height = opt["distance"], opt["height"]
    path = []
    for row in target_rows:
        yaw = _f(row, I_YAW)
        pitch = _f(row, I_PITCH) * 0.35          # keep the chase cam mostly level
        fx, fy, fz = _angles_to_vec(pitch, yaw)
        tx, ty, tz = _f(row, I_X), _f(row, I_Y), _eye(row)
        cam = (tx - fx * dist, ty - fy * dist, tz - fz * dist + height)
        p, y = _vec_to_angles(tx - cam[0], ty - cam[1], tz - cam[2])
        path.append((_f(row, I_TIME), cam[0], cam[1], cam[2], p, y))
    return path


def _rig_orbit(target_rows, _others, opt):
    dist, height = opt["distance"], opt["height"]
    speed = opt.get("orbit_speed", 25.0)          # degrees per second
    start = opt.get("orbit_start", 0.0)
    path = []
    t0 = _f(target_rows[0], I_TIME) if target_rows else 0.0
    for row in target_rows:
        t = _f(row, I_TIME)
        angle = math.radians(start + speed * (t - t0))
        tx, ty, tz = _f(row, I_X), _f(row, I_Y), _eye(row)
        cam = (tx + dist * math.cos(angle), ty + dist * math.sin(angle), tz + height)
        p, y = _vec_to_angles(tx - cam[0], ty - cam[1], tz - cam[2])
        path.append((t, cam[0], cam[1], cam[2], p, y))
    return path


def _rig_static(target_rows, _others, opt):
    pos = opt.get("position")
    if not pos:
        first = target_rows[0]
        pos = (_f(first, I_X) + opt["distance"] or 300.0,
               _f(first, I_Y) + 300.0, _f(first, I_Z) + 200.0)
    path = []
    for row in target_rows:
        tx, ty, tz = _f(row, I_X), _f(row, I_Y), _eye(row)
        p, y = _vec_to_angles(tx - pos[0], ty - pos[1], tz - pos[2])
        path.append((_f(row, I_TIME), pos[0], pos[1], pos[2], p, y))
    return path


def _rig_action(target_rows, others, opt):
    """Look at the centre between the target and the nearest other player.

    Simple on purpose: an operator who wants exact framing tweaks the keys in Sequencer,
    but this already keeps the fight in frame instead of pointing at a wall.
    """
    dist, height = opt["distance"], opt["height"]
    by_time = {}
    for rows in others:
        for row in rows:
            by_time.setdefault(round(_f(row, I_TIME), 3), []).append(row)
    path = []
    for row in target_rows:
        t = round(_f(row, I_TIME), 3)
        tx, ty, tz = _f(row, I_X), _f(row, I_Y), _eye(row)
        near, best = None, 1e9
        for other in by_time.get(t, []):
            if _f(other, I_ALIVE, 1.0) < 0.5:
                continue
            ox, oy = _f(other, I_X), _f(other, I_Y)
            d = math.hypot(ox - tx, oy - ty)
            if 1.0 < d < best:
                near, best = other, d
        if near is not None:
            cx = (tx + _f(near, I_X)) / 2.0
            cy = (ty + _f(near, I_Y)) / 2.0
            cz = (tz + _eye(near)) / 2.0
        else:
            cx, cy, cz = tx, ty, tz
        # stand off perpendicular to the line between them, so both stay visible
        ang = math.atan2(cy - ty, cx - tx) + math.pi / 2.0
        radius = max(dist, best * 0.9 if near is not None else dist)
        cam = (cx + radius * math.cos(ang), cy + radius * math.sin(ang), cz + height)
        p, y = _vec_to_angles(cx - cam[0], cy - cam[1], cz - cam[2])
        path.append((_f(row, I_TIME), cam[0], cam[1], cam[2], p, y))
    return path


RIGS = {"pov": _rig_pov, "follow": _rig_follow, "orbit": _rig_orbit,
        "static": _rig_static, "action": _rig_action}


# --------------------------------------------------------------------- build

def build(rig: str, target_rows, other_rows=(), fps: float = 30.0, smooth: float = -1.0,
          fov: float = 0.0, distance: float = 0.0, height: float = 0.0, **extra):
    """Returns (rows in TRACK_COLUMNS order, list of camio.CamFrame)."""
    rig = rig.lower()
    if rig not in RIGS:
        raise ValueError(f"unknown camera rig {rig} (use: {', '.join(RIGS)})")
    if not target_rows:
        raise ValueError("camera target has no track data")

    opt = dict(DEFAULTS[rig])
    opt.update({k: v for k, v in extra.items() if v not in (None, "")})
    if distance:
        opt["distance"] = distance
    if height:
        opt["height"] = height
    if fov:
        opt["fov"] = fov
    if smooth >= 0.0:
        opt["smooth"] = smooth

    path = RIGS[rig](target_rows, other_rows, opt)
    window = _window_frames(opt["smooth"], fps)
    times = [p[0] for p in path]
    xs = smooth_series([p[1] for p in path], window)
    ys = smooth_series([p[2] for p in path], window)
    zs = smooth_series([p[3] for p in path], window)
    pitches, yaws = smooth_angles([p[4] for p in path], [p[5] for p in path], window)

    rows, frames = [], []
    for i, t in enumerate(times):
        tick = int(target_rows[i][I_TICK]) if i < len(target_rows) else i
        row = [tick, round(t, 6), round(xs[i], 4), round(ys[i], 4), round(zs[i], 4),
               round(pitches[i], 4), round(yaws[i], 4), 0.0, round(opt["fov"], 3),
               1, 0, "camera", 0.0, 0.0]
        assert len(row) == len(TRACK_COLUMNS)
        rows.append(row)
        frames.append(camio.CamFrame(t, xs[i], ys[i], zs[i],
                                     pitches[i], yaws[i], 0.0, opt["fov"]))
    return rows, frames


def load_track(sc, actor):
    """Scene track as plain lists in TRACK_COLUMNS order (what build() expects)."""
    rows = []
    for r in sc.read_track(actor):
        row = []
        for c in TRACK_COLUMNS:
            v = r.get(c, "")
            if c == "weapon":
                row.append(v)
            else:
                try:
                    row.append(float(v) if v not in ("", None) else 0.0)
                except ValueError:
                    row.append(0.0)
        rows.append(row)
    return rows


def pick_target(sc, wanted: str = "auto"):
    """Player to build the camera around: explicit, the clip owner, or the top fragger."""
    players = [a for a in sc.actors if a.kind == "player" and a.track]
    if not players:
        raise ValueError("this scene has no player tracks")
    want = (wanted or "auto").strip().lower()
    if want not in ("auto", ""):
        for a in players:
            if want in (a.steamid.lower(), a.name.lower(), a.id.lower()):
                return a
        raise ValueError("player not found: " + wanted + ". Available: "
                         + ", ".join(a.name for a in players))
    clip = (sc.meta.get("clip") or {})
    if clip.get("steamid"):
        for a in players:
            if a.steamid == str(clip["steamid"]):
                return a
    kills = {}
    for ev in sc.events:
        if ev["type"] == "player_death":
            sid = str(ev["data"].get("attacker_steamid", ""))
            if sid:
                kills[sid] = kills.get(sid, 0) + 1
    if kills:
        best = max(kills, key=kills.get)
        for a in players:
            if a.steamid == best:
                return a
    return players[0]


def add_to_scene(scene_dir, rig: str, target: str = "auto", name: str = "",
                 cam_path=None, **options):
    """Build a camera, store it in the scene and write the matching .cam file."""
    from .scene import Actor, Scene
    from pathlib import Path

    scene_dir = Path(scene_dir)
    sc = Scene.load(scene_dir)
    actor = pick_target(sc, target)
    target_rows = load_track(sc, actor)
    others = []
    if rig == "action":
        others = [load_track(sc, a) for a in sc.actors
                  if a.kind == "player" and a.track and a.id != actor.id]
    fps = float(sc.meta.get("sample_fps") or 30.0)
    rows, frames = build(rig, target_rows, others, fps=fps, **options)

    name = name or f"camera_{rig}"
    sc.actors = [a for a in sc.actors if a.id != name]
    sc.actors.append(Actor(id=name, kind="camera", name=f"{rig} {actor.name}",
                           meta={"rig": rig, "target": actor.id}))
    sc.save(scene_dir, {name: rows})
    cam_path = Path(cam_path) if cam_path else scene_dir / f"{name}.cam"
    write_cam(cam_path, frames)
    return {"name": name, "target": actor.name, "frames": len(rows), "cam": str(cam_path)}


def write_cam(path, frames):
    """HLAE .cam next to the scene - usable by mirv_camio import inside CS2."""
    return camio.write(path, frames)
