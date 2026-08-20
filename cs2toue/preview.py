"""Top-down preview of an exported scene: play it back, pick the moment, then export.

Pure tkinter, so it works in the packaged app without extra dependencies. The point is
not to be pretty - it is to answer "is this the moment I want?" before spending minutes
on a Level Sequence.

    cs2toue preview <scene folder>

Controls: space = play/pause, arrows = step, I / O = mark in and out, click a player to
follow them with the camera rigs later.
"""

from __future__ import annotations

import math

import tkinter as tk
from pathlib import Path
from tkinter import ttk

from .scene import Scene

TEAM_COLORS = {"CT": "#4da3ff", "TERRORIST": "#ffa04d", "": "#bbbbbb"}
EFFECT_COLORS = {"smoke": "#dddddd", "molotov": "#ff6a3d", "he": "#ffd24d",
                 "flash": "#ffffff", "decoy": "#c0a0ff", "bomb": "#ff4d4d",
                 "tracer": "#ffe680"}
BG = "#12161c"
GRID = "#1d242e"


class Preview:
    def __init__(self, root, scene_dir):
        self.root = root
        self.scene_dir = Path(scene_dir)
        self.sc = Scene.load(self.scene_dir)
        self.tickrate = float(self.sc.meta.get("tickrate") or 64.0)
        self.fps = float(self.sc.meta.get("sample_fps") or 30.0)

        self.players, self.grenades = [], []
        self._load_tracks()
        self.duration = max((p["rows"][-1][0] for p in self.players + self.grenades
                             if p["rows"]), default=1.0)
        self.bounds = self._bounds()

        self.playing = False
        self.speed = 1.0
        self.time = 0.0
        self.mark_in = None
        self.mark_out = None
        self.follow = None

        self._build_ui()
        self._tick_loop()

    # ---------------------------------------------------------------- data

    def _load_tracks(self):
        for actor in self.sc.actors:
            if not actor.track:
                continue
            rows = []
            for r in self.sc.read_track(actor):
                try:
                    rows.append((float(r["time"]), float(r["x"]), float(r["y"]),
                                 float(r["z"]), float(r.get("yaw") or 0.0),
                                 str(r.get("alive", "1")) not in ("0", "False", "false"),
                                 int(float(r.get("tick") or 0))))
                except (ValueError, KeyError):
                    continue
            if not rows:
                continue
            item = {"actor": actor, "rows": rows,
                    "color": TEAM_COLORS.get(actor.team, TEAM_COLORS[""])}
            if actor.kind == "player":
                self.players.append(item)
            elif actor.kind == "grenade":
                self.grenades.append(item)

    def _bounds(self):
        xs, ys = [], []
        for item in self.players + self.grenades:
            for row in item["rows"]:
                xs.append(row[1])
                ys.append(row[2])
        if not xs:
            return (-1000, -1000, 1000, 1000)
        pad = 200
        return (min(xs) - pad, min(ys) - pad, max(xs) + pad, max(ys) + pad)

    def _row_at(self, rows, t):
        lo, hi = 0, len(rows) - 1
        while lo < hi:
            mid = (lo + hi) // 2
            if rows[mid][0] < t:
                lo = mid + 1
            else:
                hi = mid
        return rows[lo]

    # ---------------------------------------------------------------- ui

    def _build_ui(self):
        self.root.title(f"cs2toUE preview - {self.sc.meta.get('demo', '')} "
                        f"({self.sc.meta.get('map', '')})")
        self.root.geometry("1000x760")

        self.canvas = tk.Canvas(self.root, bg=BG, highlightthickness=0)
        self.canvas.pack(fill="both", expand=True, padx=6, pady=6)
        self.canvas.bind("<Configure>", lambda e: self._draw())
        self.canvas.bind("<Button-1>", self._on_click)

        bar = ttk.Frame(self.root)
        bar.pack(fill="x", padx=6)
        self.play_btn = ttk.Button(bar, text="Play", width=8, command=self.toggle)
        self.play_btn.pack(side="left")
        ttk.Button(bar, text="<<", width=4,
                   command=lambda: self.step(-1.0)).pack(side="left", padx=2)
        ttk.Button(bar, text=">>", width=4,
                   command=lambda: self.step(1.0)).pack(side="left")
        ttk.Label(bar, text="speed").pack(side="left", padx=(10, 2))
        self.speed_var = tk.StringVar(value="1x")
        speed = ttk.Combobox(bar, textvariable=self.speed_var, width=5, state="readonly",
                             values=["0.25x", "0.5x", "1x", "2x", "4x"])
        speed.pack(side="left")
        speed.bind("<<ComboboxSelected>>",
                   lambda e: setattr(self, "speed", float(self.speed_var.get()[:-1])))

        ttk.Button(bar, text="Mark in (I)", command=self.set_in).pack(side="left", padx=(14, 2))
        ttk.Button(bar, text="Mark out (O)", command=self.set_out).pack(side="left")
        ttk.Button(bar, text="Copy range", command=self.copy_range).pack(side="left", padx=6)

        self.time_var = tk.DoubleVar(value=0.0)
        self.slider = ttk.Scale(self.root, from_=0.0, to=self.duration, variable=self.time_var,
                                command=self._on_slider)
        self.slider.pack(fill="x", padx=6, pady=4)

        self.status = tk.StringVar(value="")
        ttk.Label(self.root, textvariable=self.status).pack(anchor="w", padx=8, pady=(0, 6))

        self.root.bind("<space>", lambda e: self.toggle())
        self.root.bind("<Left>", lambda e: self.step(-1.0 / self.fps))
        self.root.bind("<Right>", lambda e: self.step(1.0 / self.fps))
        self.root.bind("i", lambda e: self.set_in())
        self.root.bind("o", lambda e: self.set_out())

    # ---------------------------------------------------------------- transform

    def _to_screen(self, x, y):
        minx, miny, maxx, maxy = self.bounds
        w = max(1, self.canvas.winfo_width())
        h = max(1, self.canvas.winfo_height())
        k = min(w / (maxx - minx), h / (maxy - miny))
        ox = (w - (maxx - minx) * k) / 2
        oy = (h - (maxy - miny) * k) / 2
        return (ox + (x - minx) * k, h - oy - (y - miny) * k, k)

    # ---------------------------------------------------------------- drawing

    def _draw(self):
        c = self.canvas
        c.delete("all")
        t = self.time

        # faint trails so the whole movement of the clip is visible at once
        for item in self.players:
            pts = []
            for row in item["rows"][::3]:
                sx, sy, _ = self._to_screen(row[1], row[2])
                pts += [sx, sy]
            if len(pts) >= 4:
                c.create_line(*pts, fill=GRID, width=1)

        for fx in self.sc.effects:
            start = float(fx["time"])
            end = start + float(fx.get("duration") or 0.0)
            if not (start <= t <= end):
                continue
            pos = fx["pos"]
            sx, sy, k = self._to_screen(pos[0], pos[1])
            radius = {"smoke": 144, "molotov": 150, "he": 90, "flash": 60,
                      "decoy": 60, "bomb": 250}.get(fx["type"], 30) * k
            color = EFFECT_COLORS.get(fx["type"], "#888888")
            if fx["type"] == "tracer" and fx.get("end"):
                ex, ey, _ = self._to_screen(fx["end"][0], fx["end"][1])
                c.create_line(sx, sy, ex, ey, fill=color, width=1)
            else:
                c.create_oval(sx - radius, sy - radius, sx + radius, sy + radius,
                              outline=color, width=1)

        for item in self.grenades:
            rows = item["rows"]
            if not (rows[0][0] <= t <= rows[-1][0]):
                continue
            row = self._row_at(rows, t)
            sx, sy, _ = self._to_screen(row[1], row[2])
            c.create_oval(sx - 3, sy - 3, sx + 3, sy + 3, fill="#8fe08f", outline="")

        for item in self.players:
            row = self._row_at(item["rows"], t)
            sx, sy, _ = self._to_screen(row[1], row[2])
            alive = row[5]
            color = item["color"]
            if not alive:
                c.create_line(sx - 5, sy - 5, sx + 5, sy + 5, fill="#666666", width=2)
                c.create_line(sx - 5, sy + 5, sx + 5, sy - 5, fill="#666666", width=2)
                continue
            yaw = math.radians(row[4])
            c.create_line(sx, sy, sx + 18 * math.cos(yaw), sy - 18 * math.sin(yaw),
                          fill=color, width=2)
            r = 7 if item["actor"].id == self.follow else 5
            c.create_oval(sx - r, sy - r, sx + r, sy + r, fill=color, outline="")
            c.create_text(sx + 10, sy - 10, text=item["actor"].name, anchor="w",
                          fill="#cfd6e0", font=("Segoe UI", 8))

        self._draw_status(t)

    def _draw_status(self, t):
        near = [ev for ev in self.sc.events
                if abs(ev["time"] - t) < 0.5 and ev["type"] in
                ("player_death", "bomb_planted", "bomb_defused", "round_start")]
        text = f"t {t:6.2f}s / {self.duration:.2f}s   tick {self._tick_at(t)}"
        if self.mark_in is not None or self.mark_out is not None:
            text += (f"   in {self.mark_in if self.mark_in is not None else '-'}"
                     f"  out {self.mark_out if self.mark_out is not None else '-'}")
        for ev in near[:2]:
            data = ev["data"]
            if ev["type"] == "player_death":
                text += (f"   kill: {data.get('attacker_name', '?')} -> "
                         f"{data.get('user_name', '?')} ({data.get('weapon', '')})")
            else:
                text += f"   {ev['type']}"
        self.status.set(text)

    def _tick_at(self, t):
        start = int(self.sc.meta.get("tick_start") or 0)
        return start + int(round(t * self.tickrate))

    # ---------------------------------------------------------------- actions

    def toggle(self):
        self.playing = not self.playing
        self.play_btn.config(text="Pause" if self.playing else "Play")

    def step(self, seconds):
        self.time = max(0.0, min(self.duration, self.time + seconds))
        self.time_var.set(self.time)
        self._draw()

    def _on_slider(self, _value):
        self.time = float(self.time_var.get())
        self._draw()

    def _on_click(self, event):
        best, dist = None, 1e9
        for item in self.players:
            row = self._row_at(item["rows"], self.time)
            sx, sy, _ = self._to_screen(row[1], row[2])
            d = (sx - event.x) ** 2 + (sy - event.y) ** 2
            if d < dist:
                best, dist = item, d
        if best and dist < 400:
            self.follow = best["actor"].id
            self.status.set(f"selected {best['actor'].name} "
                            f"(steamid {best['actor'].steamid})")
            self._draw()

    def set_in(self):
        self.mark_in = self._tick_at(self.time)
        self._draw()

    def set_out(self):
        self.mark_out = self._tick_at(self.time)
        self._draw()

    def copy_range(self):
        if self.mark_in is None or self.mark_out is None:
            self.status.set("mark both ends first (I and O)")
            return
        lo, hi = sorted((self.mark_in, self.mark_out))
        demo = self.sc.meta.get("demo_path") or self.sc.meta.get("demo") or "<demo>"
        cmd = f'cs2toue export "{demo}" --from {lo} --to {hi}'
        self.root.clipboard_clear()
        self.root.clipboard_append(cmd)
        self.status.set(f"copied: {cmd}")

    # ---------------------------------------------------------------- loop

    def _tick_loop(self):
        if self.playing:
            self.time += (1.0 / 30.0) * self.speed
            if self.time >= self.duration:
                self.time = 0.0
            self.time_var.set(self.time)
            self._draw()
        self.root.after(33, self._tick_loop)


def show(scene_dir, master=None):
    root = tk.Toplevel(master) if master is not None else tk.Tk()
    Preview(root, scene_dir)
    if master is None:
        root.mainloop()
    return root
