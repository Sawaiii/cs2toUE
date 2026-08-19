"""Check the effect placement logic without a real demo.

    python tools/test_effects.py

Builds a small fake scene (two players, a smoke, a molotov, two shots - one of them a
kill) and verifies positions, lifetimes and tracer geometry.
"""

import math
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from cs2toue import effects
from cs2toue.scene import Actor, Scene, TRACK_COLUMNS


def row(tick, t, x, y, z, pitch=0.0, yaw=0.0, alive=1, speed=0.0):
    return [tick, t, x, y, z, pitch, yaw, 0.0, "", alive, 100, "ak47", 0.0, speed]


def main():
    assert len(TRACK_COLUMNS) == 14, TRACK_COLUMNS

    sc = Scene()
    sc.actors = [
        Actor(id="player_1", kind="player", name="Shooter", steamid="1", team="CT"),
        Actor(id="player_2", kind="player", name="Victim", steamid="2", team="TERRORIST"),
        Actor(id="grenade_smoke_77", kind="grenade", name="smoke"),
    ]
    tracks = {
        "player_1": [row(0, 0.0, 0, 0, 0, 0.0, 0.0), row(64, 1.0, 0, 0, 0, 0.0, 90.0),
                     row(128, 2.0, 0, 0, 0, 0.0, 90.0)],
        "player_2": [row(0, 0.0, 500, 500, 0), row(64, 1.0, 500, 500, 0),
                     row(128, 2.0, 500, 500, 0, alive=0)],
        "grenade_smoke_77": [row(0, 0.0, 100, 100, 10), row(32, 0.5, 300, 250, 40)],
    }
    sc.events = [
        {"tick": 32, "time": 0.5, "type": "smokegrenade_detonate",
         "data": {"entityid": 77, "x": 300.0, "y": 250.0, "z": 40.0}},
        {"tick": 96, "time": 1.5, "type": "smokegrenade_expired", "data": {"entityid": 77}},
        {"tick": 40, "time": 0.625, "type": "inferno_startburn",
         "data": {"entityid": 88, "x": -50.0, "y": 20.0, "z": 0.0}},
        {"tick": 64, "time": 1.0, "type": "weapon_fire",
         "data": {"user_steamid": "1", "weapon": "weapon_ak47"}},
        {"tick": 128, "time": 2.0, "type": "weapon_fire",
         "data": {"user_steamid": "1", "weapon": "weapon_ak47"}},
        {"tick": 128, "time": 2.0, "type": "player_death",
         "data": {"attacker_steamid": "1", "user_steamid": "2", "weapon": "ak47"}},
    ]

    fx = effects.build(sc, tracks)
    kinds = [e["type"] for e in fx]
    print("effects:", kinds)

    smoke = next(e for e in fx if e["type"] == "smoke")
    assert smoke["pos"] == [300.0, 250.0, 40.0], smoke
    # detonate 0.5 -> expired 1.5 must win over the 18 second default
    assert abs(smoke["duration"] - 1.0) < 1e-6, smoke
    print("smoke   ok   pos", smoke["pos"], "duration", smoke["duration"])

    molotov = next(e for e in fx if e["type"] == "molotov")
    assert molotov["pos"] == [-50.0, 20.0, 0.0]
    assert abs(molotov["duration"] - effects.DEFAULT_LIFETIME["molotov"]) < 1e-6
    print("molotov ok   default lifetime", molotov["duration"])

    tracers = [e for e in fx if e["type"] == "tracer"]
    assert len(tracers) == 2, tracers
    free, kill = tracers[0], tracers[1]

    # the free shot goes 3000 units along yaw 90 (+Y in source space), from eye height
    assert free["pos"] == [0.0, 0.0, effects.EYE_HEIGHT], free
    assert abs(free["end"][1] - effects.TRACER_LENGTH) < 1.0, free
    assert abs(free["end"][0]) < 1.0, free
    print("tracer  ok   free shot ends at", free["end"])

    # the killing shot must end on the victim, not in the air
    assert abs(kill["end"][0] - 500.0) < 1e-6 and abs(kill["end"][1] - 500.0) < 1e-6, kill
    length = math.dist(kill["pos"], kill["end"])
    assert length < effects.TRACER_LENGTH, kill
    print("tracer  ok   killing shot ends on the victim", kill["end"])

    assert fx == sorted(fx, key=lambda e: e["time"]), "effects must be sorted by time"
    print("\nall effect checks passed")


if __name__ == "__main__":
    main()
