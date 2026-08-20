"""Small Tkinter front end - the whole pipeline in one window, no extra dependencies.

    Demo -> (auto) HLAE version -> export scene -> extract map -> Unreal command line
"""

from __future__ import annotations

import queue
import threading
import traceback
from pathlib import Path

import tkinter as tk
from tkinter import filedialog, messagebox, ttk

from . import assets, demoinfo, highlights, maplib, steam, ueproject
from .config import Config
from .hlae import index as hlae_index, manager as hlae_manager, resolver as hlae_resolver
from .ue import BUILD_SEQUENCE, IMPORT_MAP
from .util import human, slug

TITLE = "cs2toUE - CS2 demo -> Unreal Engine"


class App:
    def __init__(self, root: tk.Tk):
        self.root = root
        self.cfg = Config.load()
        self.cfg.ensure_dirs()
        self.demo = None
        self.res = None
        self.scene_dir = None
        self.clips = []
        self.maps = []
        self.cs2_dir = ""
        self.log_q = queue.Queue()

        root.title(TITLE)
        root.minsize(900, 560)

        self._build_ui()
        self._fit_window()
        self._pump_log()
        self.log(f"workspace: {self.cfg.ws}")
        if not self.cfg.cs2_exe:
            found = steam.find_cs2_exe()
            if found:
                self.cfg.cs2_exe = found
                self.cfg.steam_path = self.cfg.steam_path or steam.steam_path()
                self.cfg.save()
        self.log(f"cs2.exe: {self.cfg.cs2_exe or 'NOT FOUND - set it in Settings'}")
        self._show_ue_state()
        self.check_update()


    def _fit_window(self):
        """Size the window to what the layout needs, but never past the screen."""
        self.root.update_idletasks()
        screen_h = self.root.winfo_screenheight()
        # a canvas has no natural height, so tell it how tall the pipeline actually is
        self._pipeline_canvas.configure(
            height=min(self._pipeline_inner.winfo_reqheight(), int(screen_h * 0.62)))
        self.root.update_idletasks()
        width = min(max(self.root.winfo_reqwidth(), 980), self.root.winfo_screenwidth() - 60)
        height = min(self.root.winfo_reqheight(), int(screen_h * 0.9))
        x = max(0, (self.root.winfo_screenwidth() - width) // 2)
        y = max(0, (self.root.winfo_screenheight() - height) // 3)
        self.root.geometry(f"{width}x{height}+{x}+{y}")

    # ---------------------------------------------------------------- ui

    def _build_ui(self):
        pad = dict(padx=8, pady=4)

        top = ttk.Frame(self.root)
        top.pack(fill="x", **pad)
        ttk.Button(top, text="Open demo...", command=self.pick_demo).pack(side="left")
        self.demo_var = tk.StringVar(value="no demo selected")
        ttk.Label(top, textvariable=self.demo_var).pack(side="left", padx=10)
        ttk.Button(top, text="Settings", command=self.settings).pack(side="right")
        self.update_btn = ttk.Button(top, text="Обновить", command=self.run_update)
        self.update_label = ttk.Label(top, text="", foreground="#1a7f37")

        # packed before the body, so the log always keeps its place at the bottom
        logf = ttk.LabelFrame(self.root, text="Log")
        logf.pack(side="bottom", fill="x", padx=8, pady=(0, 6))
        self.log_text = tk.Text(logf, height=7, wrap="word")
        self.log_text.pack(fill="both", expand=True, padx=6, pady=6)

        body = ttk.Frame(self.root)
        body.pack(fill="both", expand=True, **pad)

        left = ttk.LabelFrame(body, text="Demo")
        left.pack(side="left", fill="both", expand=True)
        self.info_text = tk.Text(left, height=14, width=52, wrap="none")
        self.info_text.pack(fill="both", expand=True, padx=6, pady=6)

        # The pipeline column is tall. On a small screen it has to scroll instead of
        # pushing the log (and everything else) out of the window.
        right_outer = ttk.LabelFrame(body, text="Pipeline")
        right_outer.pack(side="left", fill="both", expand=True, padx=(8, 0))
        canvas = tk.Canvas(right_outer, highlightthickness=0, borderwidth=0)
        vsb = ttk.Scrollbar(right_outer, orient="vertical", command=canvas.yview)
        canvas.configure(yscrollcommand=vsb.set)
        vsb.pack(side="right", fill="y")
        canvas.pack(side="left", fill="both", expand=True)

        right = ttk.Frame(canvas)
        self._pipeline_canvas = canvas
        self._pipeline_inner = right
        window = canvas.create_window((0, 0), window=right, anchor="nw")
        right.bind("<Configure>",
                   lambda e: canvas.configure(scrollregion=canvas.bbox("all")))
        canvas.bind("<Configure>", lambda e: canvas.itemconfigure(window, width=e.width))

        def _wheel(event):
            canvas.yview_scroll(int(-event.delta / 120), "units")
        canvas.bind("<Enter>", lambda e: canvas.bind_all("<MouseWheel>", _wheel))
        canvas.bind("<Leave>", lambda e: canvas.unbind_all("<MouseWheel>"))

        hl = ttk.LabelFrame(right, text="1. HLAE build for this demo")
        hl.pack(fill="x", padx=6, pady=6)
        self.hlae_var = tk.StringVar(value="-")
        ttk.Label(hl, textvariable=self.hlae_var, wraplength=420, justify="left").pack(
            anchor="w", padx=6, pady=4)
        row = ttk.Frame(hl)
        row.pack(fill="x", padx=6, pady=4)
        ttk.Label(row, text="version").pack(side="left")
        self.hlae_pick = tk.StringVar()
        self.hlae_box = ttk.Combobox(row, textvariable=self.hlae_pick, width=44,
                                     state="readonly")
        self.hlae_box.pack(side="left", padx=6)
        row = ttk.Frame(hl)
        row.pack(fill="x", padx=6, pady=4)
        ttk.Button(row, text="Install", command=self.install_hlae).pack(side="left")
        ttk.Button(row, text="Play demo in CS2", command=self.play).pack(side="left", padx=6)
        ttk.Button(row, text="Refresh index", command=self.refresh_index).pack(side="left")

        ex = ttk.LabelFrame(right, text="2. Export scene")
        ex.pack(fill="x", padx=6, pady=6)
        row = ttk.Frame(ex)
        row.pack(fill="x", padx=6, pady=4)
        ttk.Label(row, text="round").pack(side="left")
        self.round_var = tk.StringVar(value="all")
        self.round_box = ttk.Combobox(row, textvariable=self.round_var, width=8, values=["all"])
        self.round_box.pack(side="left", padx=6)
        ttk.Label(row, text="fps").pack(side="left")
        self.fps_var = tk.StringVar(value="30")
        ttk.Entry(row, textvariable=self.fps_var, width=6).pack(side="left", padx=6)
        self.grenades_var = tk.BooleanVar(value=True)
        ttk.Checkbutton(row, text="grenades", variable=self.grenades_var).pack(side="left")
        row = ttk.Frame(ex)
        row.pack(fill="x", padx=6, pady=4)
        ttk.Button(row, text="Find highlights", command=self.find_clips).pack(side="left")
        self.clip_var = tk.StringVar(value="whole selection")
        self.clip_box = ttk.Combobox(row, textvariable=self.clip_var, width=44, state="readonly",
                                     values=["whole selection"])
        self.clip_box.pack(side="left", padx=6)
        row = ttk.Frame(ex)
        row.pack(fill="x", padx=6, pady=4)
        ttk.Label(row, text="camera").pack(side="left")
        self.rig_var = tk.StringVar(value="follow")
        ttk.Combobox(row, textvariable=self.rig_var, width=9, state="readonly",
                     values=["none", "pov", "follow", "orbit", "static", "action"]
                     ).pack(side="left", padx=6)
        ttk.Label(row, text="smooth").pack(side="left")
        self.smooth_var = tk.StringVar(value="auto")
        ttk.Entry(row, textvariable=self.smooth_var, width=6).pack(side="left", padx=6)
        ttk.Button(row, text="Rebuild camera", command=self.rebuild_camera).pack(side="left")

        row = ttk.Frame(ex)
        row.pack(fill="x", padx=6, pady=4)
        ttk.Button(row, text="Export scene", command=self.export).pack(side="left")
        ttk.Button(row, text="Preview scene", command=self.preview).pack(side="left", padx=6)

        mp = ttk.LabelFrame(right, text="3. Map library")
        mp.pack(fill="x", padx=6, pady=6)
        row = ttk.Frame(mp)
        row.pack(fill="x", padx=6, pady=4)
        ttk.Button(row, text="CS2 folder...", command=self.pick_cs2_dir).pack(side="left")
        ttk.Button(row, text="cs2.exe...", command=self.pick_cs2_exe).pack(side="left", padx=6)
        ttk.Button(row, text="Rescan", command=self.scan_maps).pack(side="left")
        row = ttk.Frame(mp)
        row.pack(fill="x", padx=6, pady=4)
        self.map_var = tk.StringVar()
        self.map_box = ttk.Combobox(row, textvariable=self.map_var, width=42, state="readonly")
        self.map_box.pack(side="left")
        ttk.Button(row, text="Convert", command=self.convert_map).pack(side="left", padx=6)
        self.map_state = tk.StringVar(value="pick your CS2 folder to see the maps")
        ttk.Label(mp, textvariable=self.map_state, wraplength=430, justify="left").pack(
            anchor="w", padx=6, pady=4)

        ue = ttk.LabelFrame(right, text="4. Unreal Engine")
        ue.pack(fill="both", expand=True, padx=6, pady=6)
        row = ttk.Frame(ue)
        row.pack(fill="x", padx=6, pady=4)
        ttk.Button(row, text="Engine folder...", command=self.pick_engine).pack(side="left")
        ttk.Button(row, text="Project...", command=self.pick_project).pack(side="left", padx=6)
        ttk.Button(row, text="Detect", command=self.detect_ue).pack(side="left")
        self.ue_var = tk.StringVar(value="engine and project are not set")
        ttk.Label(ue, textvariable=self.ue_var, wraplength=430, justify="left").pack(
            anchor="w", padx=6, pady=2)
        row = ttk.Frame(ue)
        row.pack(fill="x", padx=6, pady=4)
        ttk.Button(row, text="Build sequence in UE", command=self.ue_build).pack(side="left")
        ttk.Button(row, text="Import map to UE", command=self.ue_import_map).pack(side="left", padx=6)
        self.ue_text = tk.Text(ue, height=5, wrap="word")
        self.ue_text.pack(fill="both", expand=True, padx=6, pady=6)
        ttk.Button(ue, text="Copy command", command=self.copy_ue).pack(anchor="w", padx=6, pady=4)


    # ---------------------------------------------------------------- infra

    def log(self, msg):
        self.log_q.put(str(msg))

    def _pump_log(self):
        try:
            while True:
                msg = self.log_q.get_nowait()
                self.log_text.insert("end", msg + "\n")
                self.log_text.see("end")
        except queue.Empty:
            pass
        self.root.after(120, self._pump_log)

    def task(self, fn, *a, **kw):
        def runner():
            try:
                fn(*a, **kw)
            except Exception as exc:
                self.log(f"ERROR: {exc}")
                self.log(traceback.format_exc(limit=3))
        threading.Thread(target=runner, daemon=True).start()

    # ---------------------------------------------------------------- actions

    def pick_demo(self):
        path = filedialog.askopenfilename(
            title="Select a CS2 / CS:GO demo", filetypes=[("Demo files", "*.dem"), ("All", "*.*")])
        if not path:
            return
        self.demo_var.set(Path(path).name)
        self.task(self._load_demo, path)

    def _load_demo(self, path):
        d = demoinfo.read(path)
        self.demo = d
        lines = [
            f"file          {Path(d.path).name}",
            f"size          {human(d.size)}",
            f"format        {d.fmt} ({d.engine})",
            f"game          {d.game}",
            f"map           {d.map_name}",
            f"build_num     {d.build_num}",
            f"demo version  {d.demo_version_name}",
            f"net protocol  {d.network_protocol}",
            f"length        {int(d.playback_time)//60}:{int(d.playback_time)%60:02d} "
            f"({d.playback_ticks} ticks @ {d.tickrate})",
            f"rounds        {len(getattr(self, 'rounds', []) or d.round_start_ticks)}",
            f"server        {d.server_name}",
        ]
        self.info_text.delete("1.0", "end")
        self.info_text.insert("end", "\n".join(lines))
        # Valve demos keep no round table in the header, so it comes from the events
        self.rounds = highlights.round_starts(self.cfg, d.path, d)
        self.round_box["values"] = ["all"] + [str(i + 1) for i in range(len(self.rounds))]
        self.round_var.set("all")
        try:
            entries = hlae_index.ensure_fresh()
            self.res = hlae_resolver.resolve(d, self.cfg, entries)
            state = "installed" if hlae_manager.is_installed(self.cfg, self.res.version) else "not installed"
            self.hlae_var.set(f"HLAE {self.res.version} ({state})\n{self.res.reason}")
            self._fill_hlae_versions(entries)
            for w in self.res.warnings:
                self.log(f"warning: {w}")
        except Exception as exc:
            self.hlae_var.set(f"could not resolve: {exc}")
        self.log(f"loaded {Path(path).name}: {d.label}, map {d.map_name}")


    def _fill_hlae_versions(self, entries=None):
        """Any released build can be picked by hand; the resolved one comes first."""
        entries = entries if entries is not None else hlae_index.load()
        installed = set(hlae_manager.installed(self.cfg))
        recommended = self.res.version if self.res else ""
        wanted_hook = "hook_source" if (self.demo and self.demo.engine == "source1")             else "hook_source2"

        def label(e):
            marks = []
            if e["version"] == recommended:
                marks.append("рекомендуется")
            if e["version"] in installed:
                marks.append("скачана")
            if e.get("cs2_updates"):
                marks.append("CS2 " + e["cs2_updates"][-1])
            if e.get("prerelease"):
                marks.append("pre")
            return f"{e['version']:<10} {e['published']}  " + ", ".join(marks)

        usable = [e for e in entries if e.get(wanted_hook)]
        head = [e for e in usable if e["version"] == recommended]
        rest = [e for e in usable if e["version"] != recommended]
        keep = head + [e for e in rest if e["version"] in installed] + rest[:60]
        seen, ordered = set(), []
        for e in keep:
            if e["version"] not in seen:
                seen.add(e["version"])
                ordered.append(e)

        self.hlae_entries = ordered
        self.hlae_box["values"] = [label(e) for e in ordered]
        if ordered:
            self.hlae_box.current(0)

    def _selected_hlae(self) -> str:
        idx = self.hlae_box.current()
        entries = getattr(self, "hlae_entries", [])
        if 0 <= idx < len(entries):
            return entries[idx]["version"]
        return self.res.version if self.res else ""

    def refresh_index(self):
        def run():
            entries = hlae_index.refresh()
            self.log(f"индекс HLAE обновлён: {len(entries)} релизов, "
                     f"новейший {hlae_index.latest(entries)['version']}")
            if self.demo:
                self._fill_hlae_versions(entries)
        self.task(run)

    def install_hlae(self):
        if not self.res:
            return messagebox.showinfo(TITLE, "Open a demo first")
        self.task(self._install_hlae)

    def _install_hlae(self):
        version = self._selected_hlae()
        self.log(f"установка HLAE {version} ...")
        hlae_manager.install(self.cfg, version)
        self._fill_hlae_versions()
        self.log(f"HLAE {version} готов")

    def play(self):
        if not (self.demo and self.res):
            return messagebox.showinfo(TITLE, "Open a demo first")
        self.task(self._play)

    def _play(self):
        from .util import popen
        version = self._selected_hlae()
        hlae_manager.install(self.cfg, version)
        cfg_file = hlae_manager.write_session_cfg(self.cfg, self.demo.path)
        cmd = hlae_manager.build_launch_args(
            self.cfg, version, self.demo.path, hook=self.res.hook_dll,
            exec_cfg=cfg_file.stem)
        popen(cmd)
        self.log("HLAE launched")

    def export(self):
        if not self.demo:
            return messagebox.showinfo(TITLE, "Open a demo first")
        self.task(self._export)

    def _export(self):
        from . import backends
        backend = backends.pick(self.demo)
        if not backend.available():
            self.log("demoparser2 is missing - run install.cmd")
            return
        d = self.demo
        start, end = 0, d.playback_ticks
        name = slug(Path(d.path).stem)
        clip_idx = self.clip_box.current()
        clip = getattr(self, "clips", [])[clip_idx - 1] if clip_idx > 0 else None
        if clip:
            start, end = clip.tick_start, clip.tick_end
            name += f"_clip{clip.id}_{slug(clip.kind)}"
            self.log(f"clip {clip.id}: {clip.title}")
        elif self.round_var.get() != "all":
            i = int(self.round_var.get()) - 1
            starts = getattr(self, "rounds", []) or d.round_start_ticks
            start = starts[i]
            end = starts[i + 1] - 1 if i + 1 < len(starts) else d.playback_ticks
            name += f"_round{i + 1}"
        fps = float(self.fps_var.get() or 30)
        step = max(1, int(round(d.tickrate / fps)))
        out = self.cfg.exports_dir / name
        out.mkdir(parents=True, exist_ok=True)
        self.log(f"exporting ticks {start}..{end} step {step} -> {out}")
        sc = backend.parse(d.path, d, out, tick_start=start, tick_end=end, step=step,
                           with_grenades=self.grenades_var.get(), with_events=True)
        self.scene_dir = out
        self.log(f"scene ready: {len(sc.actors)} actors, {len(sc.events)} events, "
                 f"{len(sc.effects)} effects")
        try:
            self._add_camera(out)
        except Exception as exc:
            self.log(f"camera skipped: {exc}")
        self._show_ue_cmd()

    # ------------------------------------------------------------ updates

    def check_update(self):
        """Runs once on start; the result is cached for a day, so it costs nothing."""
        self.task(self._check_update)

    def _check_update(self):
        from . import updater
        # HLAE publishes builds constantly, so refresh the release index too - once a
        # day at most, and never fatal when there is no network
        try:
            stale = hlae_index.age_days() > 1
            entries = hlae_index.ensure_fresh()
            if stale:
                self.log(f"индекс HLAE обновлён: {len(entries)} релизов, "
                         f"новейший {hlae_index.latest(entries)['version']}")
        except Exception as exc:
            self.log(f"индекс HLAE: {exc}")

        upd = updater.check(self.cfg)
        self.pending_update = upd
        if upd.error:
            self.log(f"проверка обновлений: {upd.error}")
            return
        if not upd.available:
            self.log(f"версия {upd.current} - последняя")
            return
        self.log(f"доступна версия {upd.version} ({human(upd.size)})")
        self.update_label.config(text=f"Доступна версия {upd.version}")
        self.update_label.pack(side="right", padx=8)
        self.update_btn.pack(side="right")

    def run_update(self):
        upd = getattr(self, "pending_update", None)
        if not upd or not upd.available:
            return
        notes = "\n".join(upd.notes.splitlines()[:10])
        if not messagebox.askyesno(
                TITLE,
                f"Обновить {upd.current} → {upd.version}?\n\n"
                f"Будет скачано {human(upd.size)} — только файлы программы.\n"
                f"Скачанные HLAE, карты, сцены и настройки останутся на месте.\n\n{notes}"):
            return
        self.task(self._run_update)

    def _run_update(self):
        from . import updater
        try:
            updater.update(self.cfg, force=True)
        except Exception as exc:
            self.log(f"обновление не удалось: {exc}")
            messagebox.showerror(TITLE, str(exc))
            return
        messagebox.showinfo(TITLE, "Обновление скачано.\n\n"
                                   "Программа сейчас закроется, файлы заменятся "
                                   "и она запустится снова.")
        self.root.destroy()

    # ------------------------------------------------------------ camera / preview

    def _smooth_value(self):
        raw = (self.smooth_var.get() or "auto").strip().lower()
        if raw in ("", "auto"):
            return -1.0
        try:
            return float(raw)
        except ValueError:
            return -1.0

    def _add_camera(self, scene_dir):
        rig = self.rig_var.get()
        if rig == "none":
            return
        from . import cameras
        res = cameras.add_to_scene(scene_dir, rig, "auto", smooth=self._smooth_value())
        self.log(f"camera '{rig}' on {res['target']}: {res['frames']} frames, "
                 f"{res['cam']}")

    def rebuild_camera(self):
        if not self.scene_dir:
            return messagebox.showinfo(TITLE, "Export a scene first")
        self.task(self._rebuild_camera)

    def _rebuild_camera(self):
        self._add_camera(self.scene_dir)
        self.log("camera rebuilt - run 'Build sequence in UE' again to see it")

    def preview(self):
        if not self.scene_dir:
            return messagebox.showinfo(TITLE, "Export a scene first")
        from .preview import show
        show(self.scene_dir, master=self.root)

    # ------------------------------------------------------------ highlights

    def find_clips(self):
        if not self.demo:
            return messagebox.showinfo(TITLE, "Open a demo first")
        self.task(self._find_clips)

    def _find_clips(self):
        self.log("looking for interesting moments...")
        clips = highlights.detect(self.cfg, self.demo.path, self.demo)
        self.clips = clips
        values = ["whole selection"] + [
            f"[{c.id}] {c.timecode} {c.kind} - {c.title} ({c.duration:.0f}s)" for c in clips]
        self.clip_box["values"] = values
        self.clip_box.current(1 if clips else 0)
        self.log(f"found {len(clips)} moments" if clips else "nothing found (try a longer demo)")

    # ------------------------------------------------------------ unreal

    def pick_engine(self):
        path = filedialog.askdirectory(title="Select the Unreal Engine folder (e.g. UE_5.5)")
        if not path:
            return
        try:
            ueproject.set_engine(self.cfg, path)
            self.log(f"Unreal engine: {self.cfg.ue_engine}")
        except Exception as exc:
            messagebox.showerror(TITLE, str(exc))
        self._show_ue_state()

    def pick_project(self):
        path = filedialog.askopenfilename(title="Select the Unreal project",
                                          filetypes=[("Unreal project", "*.uproject")])
        if not path:
            return
        try:
            ueproject.set_project(self.cfg, path)
            self.log(f"Unreal project: {self.cfg.ue_project}")
        except Exception as exc:
            messagebox.showerror(TITLE, str(exc))
        self._show_ue_state()

    def detect_ue(self):
        self.task(self._detect_ue)

    def _detect_ue(self):
        engines = ueproject.detect_engines()
        for e in engines:
            self.log(f"engine found: {e['version']}  {e['dir']}")
        for p in ueproject.detect_projects()[:10]:
            self.log(f"project found: {p}")
        if len(engines) == 1 and not self.cfg.ue_engine:
            ueproject.set_engine(self.cfg, engines[0]["dir"])
        self._show_ue_state()

    def _show_ue_state(self):
        engine = self.cfg.ue_engine or "engine not set"
        project = Path(self.cfg.ue_project).name if self.cfg.ue_project else "project not set"
        self.ue_var.set(f"{engine}\n{project}")

    def ue_build(self):
        if not self.scene_dir:
            return messagebox.showinfo(TITLE, "Export a scene first")
        self.task(self._ue_run, "build", self.scene_dir)

    def ue_import_map(self):
        name = None
        if getattr(self, "maps", None) and self.map_box.current() >= 0:
            name = self.maps[self.map_box.current()][0].name
        elif self.demo:
            name = self.demo.map_name
        if not name:
            return messagebox.showinfo(TITLE, "Pick a map first")
        build = maplib.load_library(self.cfg).get(name)
        if not build:
            return messagebox.showinfo(TITLE, f"{name} is not converted yet")
        self.task(self._ue_run, "map", build.out)

    def _ue_run(self, what, target):
        try:
            if what == "build":
                ueproject.build_sequence(self.cfg, target)
            else:
                ueproject.import_map(self.cfg, target)
            self.log("Unreal finished - check the editor log above")
        except Exception as exc:
            self.log(f"Unreal: {exc}")

    # ------------------------------------------------------------ map library

    def pick_cs2_dir(self):
        path = filedialog.askdirectory(title="Папка Counter-Strike 2 (подойдёт любая внутри неё)")
        if path:
            self._use_cs2_path(path)

    def pick_cs2_exe(self):
        path = filedialog.askopenfilename(
            title="Выберите cs2.exe",
            filetypes=[("cs2.exe", "cs2.exe"), ("Программы", "*.exe"), ("Все файлы", "*.*")])
        if path:
            self._use_cs2_path(path)

    def _use_cs2_path(self, path):
        """The exe, the install root or the folder that holds the exe - all accepted."""
        exe = steam.resolve_cs2(path)
        if exe:
            self.cfg.cs2_exe = exe
            self.cfg.save()
            self.log(f"cs2.exe: {exe}")
            version = steam.installed_version(exe).get("PatchVersion", "")
            if version:
                self.log(f"установленная версия CS2: {version}")
        else:
            self.log(f"cs2.exe не найден по пути {path} - карты всё равно поищу там")
        self.cs2_dir = path
        self.scan_maps()

    def scan_maps(self):
        self.task(self._scan_maps)

    def _scan_maps(self):
        rows = []
        cs2_dir = getattr(self, "cs2_dir", "")
        lib = maplib.load_library(self.cfg)
        for m in maplib.scan(self.cfg, cs2_dir):
            build = lib.get(m.name)
            ready = bool(maplib.cached(self.cfg, m))
            rows.append((m, "converted" if ready else
                         ("needs rebuild" if build else "not converted")))
        self.maps = rows
        values = [f"{m.name}  [{state}]" for m, state in rows]
        self.map_box["values"] = values
        if values:
            done = sum(1 for _m, s in rows if s == "converted")
            self.map_state.set(f"{len(values)} maps found, {done} already converted "
                               f"(stored in {self.cfg.ws / 'maps'})")
            if self.demo:
                for i, (m, _s) in enumerate(rows):
                    if m.name == self.demo.map_name:
                        self.map_box.current(i)
                        break
                else:
                    self.map_box.current(0)
            else:
                self.map_box.current(0)
        else:
            self.map_state.set("no maps found - pick the CS2 folder")
        self.log(f"maps found: {len(values)}")

    def convert_map(self):
        if not getattr(self, "maps", None):
            return messagebox.showinfo(TITLE, "Scan the CS2 folder first")
        idx = self.map_box.current()
        if idx < 0:
            return
        self.task(self._convert_map, self.maps[idx][0])

    def _convert_map(self, map_file):
        build = maplib.convert(self.cfg, map_file)
        self.map_state.set(f"{build.name}: ready, {build.files} mesh files in {build.out}")
        self.log(f"map ready: {build.out}")
        self._scan_maps()
        self._show_ue_cmd(map_dir=build.out)

    def export_map(self):
        """Convert the map used by the currently opened demo."""
        if not self.demo:
            return messagebox.showinfo(TITLE, "Open a demo first")
        self.task(self._export_map)

    def _export_map(self):
        build = maplib.ensure(self.cfg, self.demo.map_name)
        if not build:
            self.log(f"map {self.demo.map_name} is not in the CS2 install")
            return
        self.log(f"map ready: {build.out}")
        self._show_ue_cmd(map_dir=build.out)

    def _show_ue_cmd(self, map_dir=None):
        lines = []
        if map_dir:
            lines.append(f'UnrealEditor-Cmd.exe "<Project>.uproject" -run=pythonscript '
                         f'-script="{IMPORT_MAP} {map_dir}"')
        if self.scene_dir:
            lines.append(f'UnrealEditor-Cmd.exe "<Project>.uproject" -run=pythonscript '
                         f'-script="{BUILD_SEQUENCE} {self.scene_dir} --scale={self.cfg.ue_scale}"')
        self.ue_text.delete("1.0", "end")
        self.ue_text.insert("end", "\n\n".join(lines))

    def copy_ue(self):
        text = self.ue_text.get("1.0", "end").strip()
        if text:
            self.root.clipboard_clear()
            self.root.clipboard_append(text)
            self.log("command copied to clipboard")

    def settings(self):
        win = tk.Toplevel(self.root)
        win.title("Settings")
        win.geometry("720x260")
        fields = [("cs2.exe", "cs2_exe"), ("workspace", "workspace"),
                  ("moviemaking cfg", "mmcfg"), ("Source2Viewer-CLI.exe", "source2viewer_cli")]
        vars_ = {}
        for i, (label, attr) in enumerate(fields):
            ttk.Label(win, text=label).grid(row=i, column=0, sticky="w", padx=8, pady=6)
            v = tk.StringVar(value=str(getattr(self.cfg, attr)))
            vars_[attr] = v
            ttk.Entry(win, textvariable=v, width=70).grid(row=i, column=1, padx=8)
            ttk.Button(win, text="...",
                       command=lambda vv=v, a=attr: vv.set(
                           filedialog.askopenfilename() if "exe" in a
                           else filedialog.askdirectory())).grid(row=i, column=2, padx=4)
        scale_var = tk.StringVar(value=str(self.cfg.ue_scale))
        ttk.Label(win, text="unreal scale").grid(row=len(fields), column=0, sticky="w", padx=8)
        ttk.Entry(win, textvariable=scale_var, width=10).grid(row=len(fields), column=1,
                                                              sticky="w", padx=8)

        def save():
            for attr, v in vars_.items():
                setattr(self.cfg, attr, v.get())
            try:
                self.cfg.ue_scale = float(scale_var.get())
            except ValueError:
                pass
            self.cfg.save()
            self.cfg.ensure_dirs()
            self.log("settings saved")
            win.destroy()

        ttk.Button(win, text="Save", command=save).grid(row=len(fields) + 1, column=1,
                                                        sticky="e", pady=10)


def main():
    root = tk.Tk()
    try:
        ttk.Style().theme_use("vista")
    except Exception:
        pass
    App(root)
    root.mainloop()
