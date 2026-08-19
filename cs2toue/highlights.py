"""Find the interesting moments in a demo, so nobody has to scrub through 40 minutes.

Only game events are parsed here (no per-tick data), so this runs in a couple of
seconds even on a long match.  Team sides are read once per round - that is what makes
real clutch detection possible instead of guessing.

Result: a ranked list of clips with ready tick ranges, which `cs2toue export --clip-id`
feeds straight into the scene exporter.
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass, asdict, field
from pathlib import Path

from .util import Fail, info, warn

EVENTS = ["player_death", "bomb_planted", "bomb_defused", "bomb_exploded",
          "round_start", "round_end", "round_officially_ended", "round_freeze_end"]

# kills with these weapons are worth a clip on their own
SPECIAL_WEAPONS = {
    "knife": "knife kill",
    "bayonet": "knife kill",
    "taser": "zeus kill",
    "hegrenade": "grenade kill",
    "inferno": "molotov kill",
    "decoy": "decoy kill",
}

KIND_SCORE = {
    "ace": 100, "clutch": 90, "4k": 80, "3k": 60, "defuse": 55,
    "special": 45, "2k": 30, "1vX": 85,
}


@dataclass
class Clip:
    id: int = 0
    kind: str = ""
    title: str = ""
    player: str = ""
    steamid: str = ""
    round: int = 0
    tick_start: int = 0
    tick_end: int = 0
    time_start: float = 0.0
    duration: float = 0.0
    score: int = 0
    kills: int = 0
    details: list = field(default_factory=list)

    @property
    def timecode(self) -> str:
        m, s = divmod(int(self.time_start), 60)
        return f"{m}:{s:02d}"


# ------------------------------------------------------------------ helpers

def _num(v, default=0):
    try:
        f = float(v)
        return default if math.isnan(f) else f
    except (TypeError, ValueError):
        return default


def _txt(v) -> str:
    if v is None:
        return ""
    if isinstance(v, float) and math.isnan(v):
        return ""
    s = str(v).strip()
    return "" if s in ("nan", "None", "<NA>") else s


def _truthy(v) -> bool:
    if v is None:
        return False
    if isinstance(v, str):
        return v.lower() in ("1", "true", "yes")
    if isinstance(v, float) and math.isnan(v):
        return False
    return bool(v)


def _weapon_kind(weapon: str) -> str:
    w = weapon.lower().replace("weapon_", "")
    for key, label in SPECIAL_WEAPONS.items():
        if key in w:
            return label
    return ""


def cache_path(cfg, demo_path) -> Path:
    p = Path(demo_path)
    size = p.stat().st_size if p.is_file() else 0
    return cfg.cache_dir / f"clips_{p.stem}_{size}.json"


# ------------------------------------------------------------------ detection

def detect(cfg, demo_path, demo_info, min_kills: int = 3, pre: float = 6.0,
           post: float = 3.0, use_cache: bool = True) -> list:
    cache = cache_path(cfg, demo_path)
    if use_cache and cache.is_file():
        try:
            raw = json.loads(cache.read_text(encoding="utf-8"))
            if raw.get("min_kills") == min_kills and raw.get("pre") == pre and raw.get("post") == post:
                return [Clip(**c) for c in raw["clips"]]
        except Exception:
            pass

    try:
        from demoparser2 import DemoParser
    except Exception as exc:
        raise Fail("demoparser2 is not installed - run install.cmd") from exc

    parser = DemoParser(str(demo_path))
    tickrate = demo_info.tickrate or 64.0
    last_tick = demo_info.playback_ticks or 0

    events = {}
    try:
        for name, df in parser.parse_events(EVENTS) or []:
            if df is not None and len(df):
                events[name] = df.to_dict("records")
    except Exception as exc:
        raise Fail(f"could not read events from the demo: {exc}")

    deaths = sorted(events.get("player_death", []), key=lambda e: _num(e.get("tick")))
    if not deaths:
        warn("no player_death events in this demo - nothing to detect")
        return []

    rounds = _round_table(demo_info, events, last_tick)
    teams = _team_sides(parser, rounds)

    clips = []
    for rnd in rounds:
        in_round = [d for d in deaths
                    if rnd["start"] <= _num(d.get("tick")) <= rnd["end"]]
        if not in_round:
            continue
        clips += _multikills(in_round, rnd, tickrate, min_kills)
        clips += _clutches(in_round, rnd, teams.get(rnd["number"], {}), tickrate)
        clips += _special_kills(in_round, rnd, tickrate)
    clips += _defuses(events, rounds, tickrate)

    # pad, clamp, rank, number
    out = []
    for c in clips:
        c.tick_start = max(0, int(c.tick_start - pre * tickrate))
        c.tick_end = int(c.tick_end + post * tickrate)
        if last_tick:
            c.tick_end = min(c.tick_end, last_tick)
        c.time_start = round(c.tick_start / tickrate, 2)
        c.duration = round((c.tick_end - c.tick_start) / tickrate, 2)
        c.score = KIND_SCORE.get(c.kind, 10) + c.kills
        out.append(c)
    out = _dedupe(out)
    out.sort(key=lambda c: (-c.score, c.tick_start))
    for i, c in enumerate(out, 1):
        c.id = i

    cache.parent.mkdir(parents=True, exist_ok=True)
    cache.write_text(json.dumps({"min_kills": min_kills, "pre": pre, "post": post,
                                 "clips": [asdict(c) for c in out]}, indent=1,
                                ensure_ascii=False), encoding="utf-8")
    return out


def _round_table(demo_info, events, last_tick) -> list:
    starts = list(demo_info.round_start_ticks or [])
    if not starts:
        starts = sorted({int(_num(e.get("tick"))) for e in events.get("round_start", [])})
    if not starts:
        starts = [0]
    rounds = []
    for i, start in enumerate(starts):
        end = starts[i + 1] - 1 if i + 1 < len(starts) else (last_tick or start + 10 ** 7)
        rounds.append({"number": i + 1, "start": int(start), "end": int(end)})
    return rounds


def _team_sides(parser, rounds) -> dict:
    """{round_number: {steamid: team}} sampled a moment after each round start."""
    if not rounds:
        return {}
    sample = [r["start"] + 64 for r in rounds]
    try:
        df = parser.parse_ticks(["team_name"], ticks=sample)
    except Exception as exc:
        warn(f"team sides unavailable, clutch detection disabled ({exc})")
        return {}
    if df is None or not len(df):
        return {}
    by_tick = {}
    for rec in df.to_dict("records"):
        tick = int(_num(rec.get("tick")))
        sid = _txt(rec.get("steamid")) or _txt(rec.get("name"))
        team = _txt(rec.get("team_name"))
        if sid and team:
            by_tick.setdefault(tick, {})[sid] = team
    out = {}
    for r, tick in zip(rounds, sample):
        if by_tick.get(tick):
            out[r["number"]] = by_tick[tick]
    return out


def _attacker(d):
    name = _txt(d.get("attacker_name"))
    sid = _txt(d.get("attacker_steamid"))
    victim = _txt(d.get("user_name"))
    if not name or name == victim:      # suicide / world damage
        return "", ""
    return name, sid


def _multikills(deaths, rnd, tickrate, min_kills) -> list:
    by_player = {}
    for d in deaths:
        name, sid = _attacker(d)
        if not name:
            continue
        by_player.setdefault((name, sid), []).append(d)
    out = []
    for (name, sid), kills in by_player.items():
        n = len(kills)
        if n < max(2, min_kills):
            continue
        kind = {5: "ace", 4: "4k", 3: "3k"}.get(min(n, 5), "2k")
        if n > 5:
            kind = "ace"
        details = []
        for k in kills:
            w = _txt(k.get("weapon")).replace("weapon_", "")
            flags = []
            if _truthy(k.get("headshot")):
                flags.append("hs")
            if _num(k.get("penetrated")) > 0:
                flags.append("wallbang")
            if _truthy(k.get("noscope")):
                flags.append("noscope")
            if _truthy(k.get("thrusmoke")):
                flags.append("smoke")
            if _truthy(k.get("attackerblind")):
                flags.append("blind")
            details.append(f"{_txt(k.get('user_name'))} ({w}{', ' + '/'.join(flags) if flags else ''})")
        out.append(Clip(
            kind=kind, player=name, steamid=sid, round=rnd["number"], kills=n,
            title=f"{n}k {name} (round {rnd['number']})",
            tick_start=int(_num(kills[0].get("tick"))),
            tick_end=int(_num(kills[-1].get("tick"))),
            details=details,
        ))
    return out


def _clutches(deaths, rnd, sides, tickrate) -> list:
    """1vX: a player left alone against N enemies who then kills all of them."""
    if not sides:
        return []
    alive = {}
    for sid, team in sides.items():
        alive.setdefault(team, set()).add(sid)
    if len(alive) != 2:
        return []
    out = []
    clutcher = None
    clutch_from = 0
    kills_after = 0
    for d in deaths:
        victim_sid = _txt(d.get("user_steamid"))
        att_name, att_sid = _attacker(d)
        if clutcher and att_sid == clutcher[0]:
            kills_after += 1
        for team, members in alive.items():
            members.discard(victim_sid)
        if clutcher is None:
            for team, members in alive.items():
                if len(members) == 1:
                    other = next(t for t in alive if t != team)
                    if len(alive[other]) >= 2:
                        sid = next(iter(members))
                        clutcher = (sid, team, len(alive[other]))
                        clutch_from = int(_num(d.get("tick")))
                        kills_after = 0
                    break
    if clutcher and kills_after >= 2:
        sid, team, enemies = clutcher
        # did they survive to the end of the round?
        won = len(alive[next(t for t in alive if t != team)]) == 0
        if won:
            name = ""
            for d in deaths:
                if _txt(d.get("attacker_steamid")) == sid:
                    name = _txt(d.get("attacker_name"))
                    break
            out.append(Clip(
                kind="clutch", player=name or sid, steamid=sid, round=rnd["number"],
                kills=kills_after,
                title=f"clutch 1v{enemies} {name or sid} (round {rnd['number']})",
                tick_start=clutch_from, tick_end=int(_num(deaths[-1].get("tick"))),
                details=[f"alone against {enemies}, {kills_after} kills to win the round"],
            ))
    return out


def _special_kills(deaths, rnd, tickrate) -> list:
    out = []
    for d in deaths:
        name, sid = _attacker(d)
        if not name:
            continue
        label = _weapon_kind(_txt(d.get("weapon")))
        if not label:
            continue
        tick = int(_num(d.get("tick")))
        out.append(Clip(
            kind="special", player=name, steamid=sid, round=rnd["number"], kills=1,
            title=f"{label} {name} -> {_txt(d.get('user_name'))} (round {rnd['number']})",
            tick_start=tick, tick_end=tick,
            details=[label],
        ))
    return out


def _defuses(events, rounds, tickrate) -> list:
    out = []
    planted = {int(_num(e.get("tick"))): e for e in events.get("bomb_planted", [])}
    for e in events.get("bomb_defused", []):
        tick = int(_num(e.get("tick")))
        rnd = next((r["number"] for r in rounds if r["start"] <= tick <= r["end"]), 0)
        plant_tick = max([t for t in planted if t < tick], default=0)
        left = ""
        if plant_tick:
            elapsed = (tick - plant_tick) / tickrate
            left = f"{max(0.0, 40.0 - elapsed):.1f}s left on the bomb"
        out.append(Clip(
            kind="defuse", player=_txt(e.get("user_name")), round=rnd, kills=0,
            title=f"defuse {_txt(e.get('user_name'))} (round {rnd})",
            tick_start=tick - int(6 * tickrate), tick_end=tick,
            details=[left] if left else [],
        ))
    return out


def _dedupe(clips) -> list:
    """Drop a clip fully covered by a better one of the same player."""
    keep = []
    for c in sorted(clips, key=lambda c: (-c.score, c.tick_start)):
        covered = any(
            k.player == c.player and k.tick_start <= c.tick_start and c.tick_end <= k.tick_end
            for k in keep
        )
        if not covered:
            keep.append(c)
    return keep


def describe(clips) -> str:
    lines = []
    for c in clips:
        head = f"  [{c.id:>2}] {c.timecode:>6}  {c.kind:<8} {c.duration:>5.1f}s  {c.title}"
        lines.append(head)
        for d in c.details[:6]:
            lines.append(f"          {d}")
    return "\n".join(lines)
