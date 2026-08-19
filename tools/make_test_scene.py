"""Build a synthetic exported scene, then exercise the camera rigs on it.

    python tools/make_test_scene.py [out_dir]

No demo needed: two players walk a circle, one shoots and kills the other, a smoke goes
off. Used to check the camera rigs, the .cam writer and the preview window.
"""

import math
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from cs2toue import cameras
from cs2toue.hlae import camio
from cs2toue.scene import Actor, Scene

FPS = 30.0
SECONDS = 8.0
TICKRATE = 64.0


def build_scene(out_dir: Path) -> Scene:
    sc = Scene()
    sc.meta = {
        "demo": "synthetic.dem", "demo_path": str(out_dir / "synthetic.dem"),
        "map": "de_test", "tickrate": TICKRATE, "tick_start": 0,
        "tick_end": int(SECONDS * TICKRATE), "tick_step": 2, "sample_fps": FPS,
        "space": "source", "backend": "synthetic",
    }
    sc.actors = [
        Actor(id="player_1", kind="player", name="Shooter", steamid="1", team="CT"),
        Actor(id="player_2", kind="player", name="Victim", steamid="2", team="TERRORIST"),
    ]

    tracks = {"player_1": [], "player_2": []}
    n = int(SECONDS * FPS)
    for i in range(n):
        t = i / FPS
        tick = int(t * TICKRATE)
        # shooter walks a circle and looks at the middle
        ang = t * 0.6
        x, y = 400 * math.cos(ang), 400 * math.sin(ang)
        yaw = math.degrees(math.atan2(-y, -x))
        speed = 400 * 0.6
        tracks["player_1"].append([tick, round(t, 6), round(x, 3), round(y, 3), 0.0,
                                   -3.0, round(yaw, 3), 0.0, "", 1, 100, "ak47", 0.0,
                                   round(speed, 2)])
        # victim stands still, dies at 5s
        alive = 1 if t < 5.0 else 0
        tracks["player_2"].append([tick, round(t, 6), 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, "",
                                   alive, 100 if alive else 0, "awp", 0.0, 0.0])

    sc.events = [
        {"tick": int(5.0 * TICKRATE), "time": 5.0, "type": "weapon_fire",
         "data": {"user_steamid": "1", "weapon": "weapon_ak47"}},
        {"tick": int(5.0 * TICKRATE), "time": 5.0, "type": "player_death",
         "data": {"attacker_steamid": "1", "attacker_name": "Shooter",
                  "user_steamid": "2", "user_name": "Victim", "weapon": "ak47"}},
    ]
    sc.effects = [
        {"type": "smoke", "time": 2.0, "duration": 4.0, "pos": [200.0, -150.0, 0.0]},
        {"type": "tracer", "time": 5.0, "duration": 0.06,
         "pos": [400.0, 0.0, 64.0], "end": [0.0, 0.0, 40.0]},
    ]
    sc.rounds = [{"number": 1, "start_tick": 0, "end_tick": int(SECONDS * TICKRATE)}]
    sc.save(out_dir, tracks)
    return sc


def check_cameras(out_dir: Path):
    sc = Scene.load(out_dir)
    target = next(a for a in sc.actors if a.id == "player_1")
    other = next(a for a in sc.actors if a.id == "player_2")

    def rows_of(actor):
        out = []
        from cs2toue.scene import TRACK_COLUMNS
        for r in sc.read_track(actor):
            row = []
            for c in TRACK_COLUMNS:
                v = r.get(c, "")
                row.append(v if c == "weapon" else (float(v) if v not in ("", None) else 0.0))
            out.append(row)
        return out

    target_rows = rows_of(target)
    other_rows = [rows_of(other)]

    for rig in ("pov", "follow", "orbit", "static", "action"):
        rows, frames = cameras.build(rig, target_rows, other_rows, fps=FPS)
        assert len(rows) == len(target_rows), rig
        path = out_dir / f"camera_{rig}.cam"
        cameras.write_cam(path, frames)
        back = camio.read(path)
        assert len(back) == len(frames), rig

        # the camera must never jump: check the biggest step between frames
        jumps = [math.dist(rows[i][2:5], rows[i - 1][2:5]) for i in range(1, len(rows))]
        yaw_steps = []
        for i in range(1, len(rows)):
            d = abs(rows[i][6] - rows[i - 1][6]) % 360.0
            yaw_steps.append(min(d, 360.0 - d))
        print(f"  {rig:<7} frames {len(rows):>4}  max move {max(jumps):7.1f} u/frame  "
              f"max yaw step {max(yaw_steps):6.1f} deg  fov {rows[0][8]}")
        assert max(yaw_steps) < 90.0, f"{rig}: camera spins ({max(yaw_steps)} deg in one frame)"

    # follow must sit behind the player, not on top of them
    rows, _ = cameras.build("follow", target_rows, fps=FPS)
    d = math.dist(rows[len(rows) // 2][2:5], target_rows[len(rows) // 2][2:5])
    assert 80.0 < d < 400.0, f"follow distance looks wrong: {d}"
    print(f"  follow distance from the player: {d:.0f} units")


if __name__ == "__main__":
    out = Path(sys.argv[1] if len(sys.argv) > 1 else "workspace/test/scene_demo")
    out.mkdir(parents=True, exist_ok=True)
    build_scene(out)
    print(f"scene written: {out}")
    check_cameras(out)
    print("\nall camera checks passed")
    print(f"preview it with:  cs2toue preview {out}")
