"""cs2toUE command line."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from . import __version__, assets, demoinfo, highlights, maplib, models, steam, ueproject
from .config import Config, PROJECT_DIR
from .hlae import camio, index as hlae_index, manager as hlae_manager, resolver as hlae_resolver
from .ue import BUILD_SEQUENCE, IMPORT_MAP, IMPORT_MODELS
from .util import Fail, human, info, ok, parse_timecode, popen, slug, warn


# --------------------------------------------------------------------- helpers

def _cfg(args) -> Config:
    cfg = Config.load()
    if getattr(args, "workspace", None):
        cfg.workspace = args.workspace
    cfg.ensure_dirs()
    return cfg


def _demo(path) -> demoinfo.DemoInfo:
    try:
        return demoinfo.read(path)
    except Exception as exc:
        raise Fail(f"cannot read demo: {exc}")


def _print_demo(d: demoinfo.DemoInfo) -> None:
    print(f"  file          {Path(d.path).name}  ({human(d.size)})")
    print(f"  format        {d.fmt}  ({d.engine})")
    print(f"  game          {d.game}")
    print(f"  map           {d.map_name}")
    if d.build_num:
        print(f"  build_num     {d.build_num}")
    if d.demo_version_name:
        print(f"  demo version  {d.demo_version_name}  {d.demo_version_guid}")
    if d.network_protocol:
        print(f"  net protocol  {d.network_protocol} (demo protocol {d.demo_protocol})")
    if d.playback_ticks:
        mins, secs = divmod(int(d.playback_time), 60)
        print(f"  length        {mins}:{secs:02d}  ({d.playback_ticks} ticks @ {d.tickrate} tick)")
    if d.round_start_ticks:
        print(f"  rounds        {len(d.round_start_ticks)}  (first tick {d.round_start_ticks[0]})")
    if d.server_name:
        print(f"  server        {d.server_name}")
    for n in d.notes:
        warn(n)


def _resolve(cfg, d, forced="") -> hlae_resolver.Resolution:
    entries = hlae_index.load(auto_refresh=True)
    res = hlae_resolver.resolve(d, cfg, entries, forced or cfg.hlae_channel)
    return res


def _tick_range(args, d):
    start, end = 0, d.playback_ticks or 0
    if getattr(args, "round", 0):
        rounds = d.round_start_ticks
        if not rounds:
            raise Fail("this demo carries no round table; use --from/--to instead")
        i = int(args.round) - 1
        if not 0 <= i < len(rounds):
            raise Fail(f"round {args.round} out of range (demo has {len(rounds)} rounds)")
        start = rounds[i]
        end = rounds[i + 1] - 1 if i + 1 < len(rounds) else (d.playback_ticks or 0)
    if getattr(args, "clip", ""):
        a, _, b = str(args.clip).partition("-")
        start = parse_timecode(a, d.tickrate)
        if b:
            end = parse_timecode(b, d.tickrate)
    if getattr(args, "from_tick", None) is not None:
        start = parse_timecode(args.from_tick, d.tickrate)
    if getattr(args, "to_tick", None) is not None:
        end = parse_timecode(args.to_tick, d.tickrate)
    if end <= start:
        raise Fail(f"empty tick range {start}..{end}")
    return int(start), int(end)


# --------------------------------------------------------------------- commands

def cmd_setup(args):
    cfg = _cfg(args)
    if args.cs2_exe:
        cfg.cs2_exe = args.cs2_exe
    if not cfg.cs2_exe:
        cfg.cs2_exe = steam.find_cs2_exe()
    if not cfg.steam_path:
        cfg.steam_path = steam.steam_path()
    if args.mmcfg:
        cfg.mmcfg = args.mmcfg
    if not cfg.mmcfg:
        cfg.mmcfg = str(cfg.ws / "mmcfg")
        (Path(cfg.mmcfg) / "csgo" / "cfg").mkdir(parents=True, exist_ok=True)
    if args.scale:
        cfg.ue_scale = args.scale
    path = cfg.save()
    ok(f"config written: {path}")
    print(json.dumps(json.loads(path.read_text(encoding='utf-8')), indent=2, ensure_ascii=False))
    if cfg.cs2_exe:
        v = steam.installed_version(cfg.cs2_exe)
        if v:
            print(f"  installed CS2: PatchVersion {v.get('PatchVersion')} "
                  f"(ClientVersion {v.get('ClientVersion')})")
    else:
        warn("cs2.exe not found - pass --cs2-exe <path> (needed for HLAE playback and map export)")


def cmd_doctor(args):
    cfg = _cfg(args)
    print(f"cs2toUE {__version__}")
    print(f"  python        {sys.version.split()[0]}")
    print(f"  workspace     {cfg.ws}   {'OK' if cfg.ws.is_dir() else 'MISSING'}")
    print(f"  cs2.exe       {cfg.cs2_exe or '- not configured -'}")
    if cfg.cs2_exe:
        v = steam.installed_version(cfg.cs2_exe)
        print(f"  CS2 patch     {v.get('PatchVersion', '?')}")
    try:
        entries = hlae_index.load()
        print(f"  HLAE index    {len(entries)} releases, newest {hlae_index.latest(entries)['version']}")
    except Exception as exc:
        warn(f"HLAE index    {exc}")
    inst = hlae_manager.installed(cfg)
    print(f"  HLAE local    {', '.join(inst) if inst else '- none installed -'}")
    s2v = assets.cli_path(cfg)
    print(f"  Source2Viewer {s2v or '- not installed (auto-downloaded on first use) -'}")
    try:
        import demoparser2
        print(f"  demoparser2   {getattr(demoparser2, '__version__', 'installed')}")
    except Exception:
        warn("demoparser2   not installed -> run install.cmd (pip install demoparser2 pandas)")
    try:
        import pandas
        print(f"  pandas        {pandas.__version__}")
    except Exception:
        warn("pandas        not installed")
    print(f"  UE scripts    {BUILD_SEQUENCE}")


def cmd_prefetch(args):
    """Download the external tools now, so later runs work without waiting (or offline)."""
    cfg = _cfg(args)
    entries = hlae_index.load(auto_refresh=True)
    version = args.hlae or hlae_index.latest(entries)["version"]
    hlae_manager.install(cfg, version)
    if args.csgo:
        csgo_entries = hlae_index.with_csgo_support(entries)
        if csgo_entries:
            legacy = max(csgo_entries, key=lambda e: [int(x) for x in e["version"].split(".")])
            hlae_manager.install(cfg, legacy["version"])
    try:
        assets.ensure_cli(cfg)
    except Exception as exc:
        warn(f"Source 2 Viewer could not be fetched: {exc}")
    ok("tools ready")


def cmd_inspect(args):
    cfg = _cfg(args)
    d = _demo(args.demo)
    if args.json:
        payload = d.as_dict()
        try:
            res = _resolve(cfg, d, args.hlae)
            payload["hlae"] = {"version": res.version, "reason": res.reason,
                               "warnings": res.warnings, "hook": res.hook_dll}
        except Exception as exc:
            payload["hlae_error"] = str(exc)
        print(json.dumps(payload, indent=2, ensure_ascii=False))
        return
    print("demo")
    _print_demo(d)
    print("hlae")
    try:
        res = _resolve(cfg, d, args.hlae)
        installed = "installed" if hlae_manager.is_installed(cfg, res.version) else "not installed"
        print(f"  version       {res.version}   ({installed})")
        print(f"  hook          {res.hook_dll}")
        print(f"  released      {res.entry.get('published')}   {res.entry.get('zip_name')}")
        print(f"  why           {res.reason}")
        for w in res.warnings:
            warn(w)
    except Exception as exc:
        warn(str(exc))


def cmd_hlae(args):
    cfg = _cfg(args)
    sub = args.hlae_cmd
    if sub == "refresh":
        entries = hlae_index.refresh()
        ok(f"index rebuilt: {len(entries)} releases, newest {entries[0]['version']}")
        return
    if sub == "list":
        entries = hlae_index.load(auto_refresh=True)
        if args.csgo:
            entries = hlae_index.with_csgo_support(entries)
        for e in entries[: args.limit]:
            marks = []
            if e["hook_source2"]:
                marks.append(f"cs2 hook {e['hook_source2']}")
            if e["hook_source"]:
                marks.append(f"csgo hook {e['hook_source']}")
            if e["cs2_updates"]:
                marks.append("adjusted to CS2 " + ", ".join(e["cs2_updates"]))
            if e.get("prerelease"):
                marks.append("PRE-RELEASE")
            local = "*" if hlae_manager.is_installed(cfg, e["version"]) else " "
            print(f" {local} {e['version']:<12} {e['published']}  {'; '.join(marks)}")
        print("\n  * = installed locally; pre-releases are skipped by auto-resolve")
        return
    if sub == "installed":
        for v in hlae_manager.installed(cfg):
            print(f"  {v}   {hlae_manager.version_dir(cfg, v)}")
        return
    if sub == "install":
        version = args.version
        if version in ("latest", "", None):
            version = hlae_index.latest()["version"]
        hlae_manager.install(cfg, version, force=args.force)
        return
    if sub == "remove":
        hlae_manager.remove(cfg, args.version)
        return
    if sub == "resolve":
        d = _demo(args.demo)
        res = _resolve(cfg, d, args.hlae)
        print(f"{res.version}  ({res.reason})")
        for w in res.warnings:
            warn(w)
        return
    if sub == "pin":
        match = {}
        if args.engine:
            match["engine"] = args.engine
        if args.build:
            lo, _, hi = str(args.build).partition("-")
            match["build_min"] = int(lo)
            match["build_max"] = int(hi or lo)
        if args.netproto:
            lo, _, hi = str(args.netproto).partition("-")
            match["netproto_min"] = int(lo)
            match["netproto_max"] = int(hi or lo)
        if not match:
            raise Fail("pin needs at least one of --engine / --build / --netproto")
        hlae_resolver.add_pin(match, args.version, args.note or "")
        ok(f"pinned {match} -> HLAE {args.version}  ({hlae_resolver.RULES_PATH})")
        return
    raise Fail(f"unknown hlae subcommand {sub}")


def cmd_play(args):
    cfg = _cfg(args)
    d = _demo(args.demo)
    res = _resolve(cfg, d, args.hlae)
    for w in res.warnings:
        warn(w)
    info(f"demo needs HLAE {res.version} ({res.reason})")
    hlae_manager.install(cfg, res.version)
    extra = []
    if args.camio:
        cam_path = Path(args.camio).resolve()
        cam_path.parent.mkdir(parents=True, exist_ok=True)
        extra.append(f'mirv_camio export start "{cam_path.as_posix()}"')
    if args.exec_cmds:
        extra += args.exec_cmds
    cfg_file = hlae_manager.write_session_cfg(cfg, args.demo, extra)
    info(f"session cfg: {cfg_file}")
    cmd = hlae_manager.build_launch_args(
        cfg, res.version, args.demo, hook=res.hook_dll,
        width=args.width, height=args.height, fullscreen=args.fullscreen,
        exec_cfg=cfg_file.stem, extra_game_args=args.game_args,
    )
    if args.dry_run:
        print(" ".join(f'"{c}"' if " " in str(c) else str(c) for c in cmd))
        return
    popen(cmd)
    ok("HLAE launched - CS2 should start and play the demo")


def cmd_export(args):
    cfg = _cfg(args)
    d = _demo(args.demo)
    print("demo")
    _print_demo(d)
    from . import backends
    backend = backends.pick(d)
    if not backend.available():
        raise Fail("demoparser2 is not installed - run install.cmd")

    clip = None
    if getattr(args, "clip_id", 0):
        found = highlights.detect(cfg, args.demo, d)
        clip = next((c for c in found if c.id == int(args.clip_id)), None)
        if not clip:
            raise Fail(f"moment {args.clip_id} not found - run: cs2toue clips {args.demo}")
        info(f"clip {clip.id}: {clip.title} at {clip.timecode} ({clip.duration}s)")

    start, end = (clip.tick_start, clip.tick_end) if clip else _tick_range(args, d)
    step = max(1, int(round(d.tickrate / float(args.fps or cfg.export_fps))))
    out = Path(args.out) if args.out else cfg.exports_dir / slug(Path(args.demo).stem)
    if clip:
        out = out.parent / f"{out.name}_clip{clip.id}_{slug(clip.kind)}"
    elif getattr(args, "round", 0):
        out = out.parent / f"{out.name}_round{int(args.round)}"
    out.mkdir(parents=True, exist_ok=True)

    sc = backend.parse(
        args.demo, d, out,
        tick_start=start, tick_end=end, step=step,
        with_grenades=not args.no_grenades, with_events=not args.no_events,
        camera=args.camera, max_players=args.max_players,
    )
    ok(f"scene written: {out / 'scene.json'}")
    print(f"  actors        {len(sc.actors)}  ({sum(a.frames for a in sc.actors)} keyframes)")
    print(f"  events        {len(sc.events)}")
    print(f"  rounds        {len(sc.rounds)}")
    print(f"  sample fps    {sc.meta['sample_fps']}")
    if args.map:
        _export_map(cfg, d)
    print()
    _print_ue_cmd(cfg, out)


def _export_map(cfg, d):
    """Convert the map of a demo (cached: converted once, reused afterwards)."""
    build = maplib.ensure(cfg, d.map_name)
    return Path(build.out) if build else None


def _print_map_import(out_dir):
    print()
    print("import the map into Unreal with:")
    print(f'  UnrealEditor-Cmd.exe "<Project>.uproject" -run=pythonscript '
          f'-script="{IMPORT_MAP} {out_dir}"')


def cmd_maps(args):
    cfg = _cfg(args)
    sub = args.maps_cmd
    cs2_dir = getattr(args, "cs2_dir", "")

    if cs2_dir:
        exe = Path(cs2_dir) / "game" / "bin" / "win64" / "cs2.exe"
        if exe.is_file():
            cfg.cs2_exe = str(exe)
            cfg.save()
            ok(f"cs2 folder remembered: {cs2_dir}")
        else:
            warn(f"cs2.exe not found under {cs2_dir} - maps are still scanned from there")

    if sub == "list":
        rows = maplib.status(cfg) if not cs2_dir else [(m, maplib.load_library(cfg).get(m.name), "ready" if maplib.cached(cfg, m) else "not converted") for m in maplib.scan(cfg, cs2_dir)]
        if not rows:
            raise Fail("no maps found - point the tool at your CS2 folder: "
                       "cs2toue maps list --cs2-dir \"D:\\Steam\\...\\Counter-Strike Global Offensive\"")
        print(f"{'map':<28} {'size':>9}  {'source':<9} state")
        for m, build, state in rows:
            mark = "*" if state == "ready" else " "
            print(f"{mark}{m.name:<27} {human(m.size):>9}  {m.source:<9} {state}"
                  + (f"  -> {build.out}" if build else ""))
        print(f"\n  * = already converted, stored in {cfg.ws / 'maps'}")
        return

    if sub == "convert":
        names = list(args.names or [])
        if args.all:
            names = [m.name for m in maplib.scan(cfg, cs2_dir)]
        if args.demo:
            names.append(_demo(args.demo).map_name)
        if not names:
            raise Fail("give one or more map names, --all, or --demo <file>")
        last = None
        for name in names:
            build = maplib.convert(cfg, name, force=args.force, cs2_dir=cs2_dir)
            last = build
        if last:
            _print_map_import(last.out)
        return

    if sub == "remove":
        maplib.remove(cfg, args.name)
        return

    if sub == "path":
        lib = maplib.load_library(cfg)
        build = lib.get(args.name)
        if not build:
            raise Fail(f"{args.name} is not in the library yet - cs2toue maps convert {args.name}")
        print(build.out)
        _print_map_import(build.out)
        return

    raise Fail(f"unknown maps subcommand {sub}")


def _print_ue_cmd(cfg, scene_dir):
    if cfg.ue_editor and cfg.ue_project:
        print("next step:")
        print(f"  cs2toue ue build {scene_dir}")
        return
    print("next step - in Unreal Engine:")
    print(f'  UnrealEditor-Cmd.exe "<Project>.uproject" -run=pythonscript '
          f'-script="{BUILD_SEQUENCE} {scene_dir} --scale={cfg.ue_scale}"')
    print("or let cs2toUE start it for you once the engine folder is set:")
    print('  cs2toue ue set --engine "<UE folder>" --project "<file.uproject>"')
    print(f"  cs2toue ue build {scene_dir}")


def cmd_ue(args):
    cfg = _cfg(args)
    sub = args.ue_cmd

    if sub == "detect":
        engines = ueproject.detect_engines()
        if engines:
            print("Unreal installations found:")
            for e in engines:
                mark = "*" if cfg.ue_editor.lower().startswith(e["dir"].lower()) else " "
                print(f" {mark} {e['version']:<10} {e['dir']}")
        else:
            warn("no Unreal installation found - set it manually with: ue set --engine <folder>")
        projects = ueproject.detect_projects([args.search] if args.search else None)
        if projects:
            print("projects found:")
            for p in projects[:20]:
                mark = "*" if cfg.ue_project == p else " "
                print(f" {mark} {p}")
        print()
        print(f"  current engine  {cfg.ue_engine or '- not set -'}")
        print(f"  current project {cfg.ue_project or '- not set -'}")
        return

    if sub == "set":
        if not args.engine and not args.project:
            raise Fail("give --engine <folder with Engine\\Binaries> and/or --project <.uproject>")
        if args.engine:
            ueproject.set_engine(cfg, args.engine)
        if args.project:
            ueproject.set_project(cfg, args.project)
        return

    if sub == "build":
        extra = []
        if args.package:
            extra.append(f"--package={args.package}")
        if args.name:
            extra.append(f"--name={args.name}")
        if args.scale:
            extra.append(f"--scale={args.scale}")
        if args.no_effects:
            extra.append("--effects=0")
        if args.no_tracers:
            extra.append("--tracers=0")
        if args.no_animations:
            extra.append("--animations=0")
        if args.max_effects:
            extra.append(f"--max-effects={args.max_effects}")
        ueproject.build_sequence(cfg, args.scene, extra, dry_run=args.dry_run)
        return

    if sub == "models":
        src = Path(args.path) if args.path else (cfg.ws / "models")
        if not src.is_dir():
            raise Fail(f"no exported models in {src} - cs2toue models export <name>")
        extra = [f"--package={args.package}"] if args.package else []
        ueproject.run_script(cfg, IMPORT_MODELS, [src] + extra, dry_run=args.dry_run)
        if args.scene and not args.dry_run:
            models.write_ue_mapping(cfg, args.scene, args.package or "/Game/cs2toUE/Models")
        return

    if sub == "map":
        target = args.map
        p = Path(target)
        if not p.is_dir():
            lib = maplib.load_library(cfg)
            build = lib.get(target)
            if not build:
                raise Fail(f"{target} is not in the map library - cs2toue maps convert {target}")
            p = Path(build.out)
        extra = [f"--package={args.package}"] if args.package else []
        ueproject.import_map(cfg, p, extra, dry_run=args.dry_run)
        return

    raise Fail(f"unknown ue subcommand {sub}")


def cmd_models(args):
    cfg = _cfg(args)
    sub = args.models_cmd
    cs2_dir = getattr(args, "cs2_dir", "")

    if sub == "list":
        found = models.find_models(cfg, args.kind, args.filter, cs2_dir)
        if not found:
            raise Fail("nothing found - check --filter, or set the CS2 folder with setup")
        lib = models.load_library(cfg)
        for m in found[: args.limit]:
            mark = "*" if m["name"] in lib else " "
            print(f" {mark} {m['name']:<28} {m['team'] or '--':<4} {m['path']}")
        print(f"\n  {len(found)} models, * = already exported")
        return

    if sub == "export":
        for name in args.names:
            models.export(cfg, name, force=args.force, cs2_dir=cs2_dir)
        return

    if sub == "library":
        lib = models.load_library(cfg)
        if not lib:
            print("  no models exported yet")
            return
        for name, b in lib.items():
            print(f"  {name:<28} {b.team or '--':<4} {human(b.bytes):>9}  "
                  f"{len(b.animations)} anims  {b.out}")
        return

    if sub == "mapping":
        models.write_ue_mapping(cfg, args.scene, args.package or "/Game/cs2toUE/Models")
        return

    if sub == "remove":
        models.remove(cfg, args.name)
        return

    raise Fail(f"unknown models subcommand {sub}")


def cmd_camera(args):
    from . import cameras

    cfg = _cfg(args)
    scene_dir = Path(args.scene)
    try:
        res = cameras.add_to_scene(
            scene_dir, args.rig, args.target, args.name, args.cam,
            smooth=args.smooth, fov=args.fov, distance=args.distance, height=args.height,
            orbit_speed=args.orbit_speed,
        )
    except ValueError as exc:
        raise Fail(str(exc))
    info(f"camera target: {res['target']}")
    ok(f"camera track written: {scene_dir / 'tracks' / (res['name'] + '.csv')}  "
       f"({res['frames']} frames)")
    cam_path = Path(res["cam"])
    ok(f"HLAE camera file: {cam_path}")
    print("  play it back inside CS2 with:")
    print(f'    mirv_camio import start "{cam_path.as_posix()}"')
    print("  or rebuild the sequence to get it in Unreal:")
    print(f"    cs2toue ue build {scene_dir}")


def cmd_preview(args):
    from .preview import show
    cfg = _cfg(args)
    show(args.scene)


def cmd_update(args):
    from . import updater
    cfg = _cfg(args)
    upd = updater.check(cfg, args.current, force=True)
    print(f"  установлено   {upd.current}")
    print(f"  на GitHub     {upd.version or '?'}   {upd.published}")
    if upd.error:
        warn(upd.error)
        return
    if not upd.available:
        ok(upd.summary)
        return
    print(f"  файл          {upd.asset}  ({human(upd.size)})")
    if upd.notes:
        print("  что нового:")
        for line in upd.notes.splitlines()[:12]:
            print(f"    {line}")
    if args.check:
        print(f"\n  обновить:  cs2toue-cli update --yes")
        return
    if not args.yes:
        answer = input("\nОбновить сейчас? [y/N] ").strip().lower()
        if answer not in ("y", "yes", "д", "да"):
            print("отменено")
            return
    updater.update(cfg, args.current, force=True, restart=not args.no_restart)


def cmd_clips(args):
    cfg = _cfg(args)
    d = _demo(args.demo)
    clips = highlights.detect(cfg, args.demo, d, min_kills=args.min_kills,
                              pre=args.pre, post=args.post, use_cache=not args.no_cache)
    if args.json:
        print(json.dumps([c.__dict__ for c in clips], indent=2, ensure_ascii=False))
        return
    if not clips:
        warn("nothing interesting found - try --min-kills 2")
        return
    print(f"{len(clips)} moments in {Path(args.demo).name} (map {d.map_name})")
    print(highlights.describe(clips[: args.limit]))
    print()
    print(f"  export one with:  cs2toue export {args.demo} --clip-id 1")
    return clips


def cmd_clean(args):
    cfg = _cfg(args)
    targets = []
    if args.downloads or args.all:
        targets.append(("downloads (installer archives)", cfg.downloads_dir, None))
    if args.exports or args.all:
        targets.append(("exported scenes", cfg.exports_dir, None))
    if args.cache or args.all:
        targets.append(("clip / scan cache", cfg.cache_dir, None))
    if args.pipcache or args.all:
        targets.append(("pip cache", cfg.ws / "pipcache", None))
    if args.maps:
        lib = maplib.load_library(cfg)
        if args.maps == "all":
            for name in list(lib):
                targets.append((f"map {name}", Path(lib[name].out), name))
        else:
            for name in args.maps.split(","):
                build = lib.get(name.strip())
                if build:
                    targets.append((f"map {name.strip()}", Path(build.out), name.strip()))
                else:
                    warn(f"map {name.strip()} is not in the library")
    if not targets:
        _print_usage(cfg)
        print("\nnothing selected - add --downloads --exports --cache --pipcache "
              "--maps <name|all> or --all")
        return

    total = 0
    for label, path, map_name in targets:
        size = _dir_size(path)
        total += size
        print(f"  {label:<32} {human(size):>10}   {path}")
    print(f"  {'total':<32} {human(total):>10}")
    if args.dry_run:
        print("\n--dry-run: nothing deleted")
        return
    import shutil
    for label, path, map_name in targets:
        if map_name:
            maplib.remove(cfg, map_name)
        elif path.is_dir():
            shutil.rmtree(path, ignore_errors=True)
            path.mkdir(parents=True, exist_ok=True)
    cfg.ensure_dirs()
    ok(f"freed {human(total)}")


def _dir_size(path) -> int:
    path = Path(path)
    if not path.is_dir():
        return 0
    return sum(p.stat().st_size for p in path.rglob("*") if p.is_file())


def _print_usage(cfg):
    print(f"disk usage under {cfg.ws}")
    rows = [
        ("HLAE builds", cfg.hlae_dir),
        ("maps", cfg.ws / "maps"),
        ("exported scenes", cfg.exports_dir),
        ("downloads", cfg.downloads_dir),
        ("tools (Source 2 Viewer)", cfg.ws / "tools"),
        ("python venv", cfg.ws / "venv"),
        ("pip cache", cfg.ws / "pipcache"),
        ("cache", cfg.cache_dir),
    ]
    total = 0
    for label, path in rows:
        size = _dir_size(path)
        total += size
        if size:
            print(f"  {label:<26} {human(size):>10}")
    print(f"  {'total':<26} {human(total):>10}")


def cmd_usage(args):
    _print_usage(_cfg(args))


def cmd_camio(args):
    cfg = _cfg(args)
    if args.to_csv:
        cam = camio.read(args.cam)
        import csv as _csv
        out = Path(args.to_csv)
        with open(out, "w", encoding="utf-8", newline="") as fh:
            w = _csv.writer(fh)
            w.writerow(["time", "x", "y", "z", "pitch", "yaw", "roll", "fov"])
            for f in cam.frames:
                w.writerow([f.time, f.x, f.y, f.z, f.pitch, f.yaw, f.roll, f.fov])
        ok(f"{len(cam)} frames -> {out}")
        return
    if args.from_csv:
        import csv as _csv
        rows = []
        with open(args.from_csv, "r", encoding="utf-8", newline="") as fh:
            for r in _csv.DictReader(fh):
                rows.append((float(r["time"]), float(r["x"]), float(r["y"]), float(r["z"]),
                             float(r.get("pitch", 0)), float(r.get("yaw", 0)),
                             float(r.get("roll", 0)), float(r.get("fov", 90))))
        frames = camio.from_ue_rows(rows, scale=args.scale or cfg.ue_scale)
        camio.write(args.cam, frames)
        ok(f"{len(frames)} frames -> {args.cam} (use: mirv_camio import start <file>)")
        return
    cam = camio.read(args.cam)
    print(f"  version {cam.version}  frames {len(cam)}  duration {cam.duration:.2f}s")
    if cam.frames:
        f = cam.frames[0]
        print(f"  first: t={f.time:.3f} pos=({f.x:.1f}, {f.y:.1f}, {f.z:.1f}) "
              f"ang=({f.pitch:.1f}, {f.yaw:.1f}, {f.roll:.1f}) fov={f.fov:.1f}")


def cmd_gui(args):
    from .gui import main as gui_main
    gui_main()


# --------------------------------------------------------------------- parser

def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="cs2toue",
        description="Convert Counter-Strike 2 / CS:GO demos into Unreal Engine scenes.",
    )
    p.add_argument("--version", action="version", version=f"cs2toUE {__version__}")
    p.add_argument("--workspace", help="override the workspace folder (default: %s)"
                                       % (PROJECT_DIR / "workspace"))
    sub = p.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("setup", help="detect Steam/CS2 and write the config")
    s.add_argument("--cs2-exe", default="", help="path to cs2.exe")
    s.add_argument("--mmcfg", default="", help="moviemaking cfg parent folder")
    s.add_argument("--scale", type=float, default=0.0, help="source unit -> unreal unit")
    s.set_defaults(func=cmd_setup)

    s = sub.add_parser("doctor", help="show what is installed and configured")
    s.set_defaults(func=cmd_doctor)

    s = sub.add_parser("prefetch", help="download HLAE and the Source 2 Viewer CLI up front")
    s.add_argument("--hlae", default="", help="HLAE version (default: newest stable)")
    s.add_argument("--csgo", action="store_true", help="also fetch the newest CS:GO-capable HLAE")
    s.set_defaults(func=cmd_prefetch)

    s = sub.add_parser("inspect", help="read a demo header and pick the HLAE build for it")
    s.add_argument("demo")
    s.add_argument("--hlae", default="", help="force an HLAE version instead of resolving")
    s.add_argument("--json", action="store_true")
    s.set_defaults(func=cmd_inspect)

    s = sub.add_parser("hlae", help="manage HLAE versions")
    hs = s.add_subparsers(dest="hlae_cmd", required=True)
    x = hs.add_parser("refresh", help="rebuild the release index from GitHub")
    x = hs.add_parser("list", help="list known releases")
    x.add_argument("--limit", type=int, default=40)
    x.add_argument("--csgo", action="store_true", help="only releases with the CS:GO hook")
    x = hs.add_parser("installed", help="list locally installed versions")
    x = hs.add_parser("install", help="download and unpack a version")
    x.add_argument("version", nargs="?", default="latest")
    x.add_argument("--force", action="store_true")
    x = hs.add_parser("remove", help="delete a local version")
    x.add_argument("version")
    x = hs.add_parser("resolve", help="print the HLAE version a demo needs")
    x.add_argument("demo")
    x.add_argument("--hlae", default="")
    x = hs.add_parser("pin", help="remember that a demo range needs a specific HLAE")
    x.add_argument("version")
    x.add_argument("--engine", choices=["source1", "source2"], default="")
    x.add_argument("--build", help="build_num or range, e.g. 14000-14099")
    x.add_argument("--netproto", help="CS:GO network protocol or range")
    x.add_argument("--note", default="")
    s.set_defaults(func=cmd_hlae)

    s = sub.add_parser("play", help="launch the demo in CS2 through the right HLAE build")
    s.add_argument("demo")
    s.add_argument("--hlae", default="")
    s.add_argument("--width", type=int, default=1920)
    s.add_argument("--height", type=int, default=1080)
    s.add_argument("--fullscreen", action="store_true")
    s.add_argument("--camio", default="", help="also start mirv_camio export to this .cam file")
    s.add_argument("--exec", dest="exec_cmds", action="append", default=[],
                   help="extra console command for the session cfg (repeatable)")
    s.add_argument("--game-args", default="", help="extra cs2.exe command line arguments")
    s.add_argument("--dry-run", action="store_true", help="print the HLAE command line only")
    s.set_defaults(func=cmd_play)

    s = sub.add_parser("export", help="parse a demo into a cs2toUE scene folder")
    s.add_argument("demo")
    s.add_argument("--out", default="", help="output folder")
    s.add_argument("--round", type=int, default=0, help="export a single round")
    s.add_argument("--clip", default="", help="time range, e.g. 12:30-13:10")
    s.add_argument("--clip-id", type=int, default=0,
                   help="export moment N found by: cs2toue clips <demo>")
    s.add_argument("--from", dest="from_tick", default=None, help="start tick / mm:ss / 12s")
    s.add_argument("--to", dest="to_tick", default=None, help="end tick / mm:ss / 12s")
    s.add_argument("--fps", type=float, default=0.0, help="sampling rate (default 30)")
    s.add_argument("--camera", default="", help="player:<steamid|name|first> for a POV camera")
    s.add_argument("--max-players", type=int, default=0)
    s.add_argument("--no-grenades", action="store_true")
    s.add_argument("--no-events", action="store_true")
    s.add_argument("--map", action="store_true", help="also decompile the map to glb")
    s.set_defaults(func=cmd_export)

    s = sub.add_parser("maps", help="map library: find, convert and reuse CS2 maps")
    ms = s.add_subparsers(dest="maps_cmd", required=True)
    x = ms.add_parser("list", help="maps found in the install and their conversion state")
    x.add_argument("--cs2-dir", default="",
                   help="path to your Counter-Strike Global Offensive folder")
    x = ms.add_parser("convert", help="convert one or more maps (cached, skipped if current)")
    x.add_argument("names", nargs="*", help="map names, e.g. de_dust2 de_mirage")
    x.add_argument("--cs2-dir", default="", help="path to your CS2 folder")
    x.add_argument("--all", action="store_true", help="convert every map in the install")
    x.add_argument("--demo", default="", help="convert the map used by this demo")
    x.add_argument("--force", action="store_true", help="reconvert even if cached")
    x = ms.add_parser("path", help="print the folder of a converted map")
    x.add_argument("name")
    x = ms.add_parser("remove", help="delete a map from the library")
    x.add_argument("name")
    s.set_defaults(func=cmd_maps)

    s = sub.add_parser("models", help="player / weapon model library from the CS2 install")
    mos = s.add_subparsers(dest="models_cmd", required=True)
    x = mos.add_parser("list", help="models found inside the game archives")
    x.add_argument("--kind", choices=["player", "weapon"], default="player")
    x.add_argument("--filter", default="", help="substring, e.g. ctm or phoenix")
    x.add_argument("--limit", type=int, default=60)
    x.add_argument("--cs2-dir", default="")
    x = mos.add_parser("export", help="export models to glb with skeleton and animations")
    x.add_argument("names", nargs="+")
    x.add_argument("--force", action="store_true")
    x.add_argument("--cs2-dir", default="")
    x = mos.add_parser("library", help="what is already exported")
    x = mos.add_parser("mapping", help="write ue_mapping.json for a scene from the library")
    x.add_argument("scene")
    x.add_argument("--package", default="", help="content path used on import")
    x = mos.add_parser("remove", help="delete a model from the library")
    x.add_argument("name")
    s.set_defaults(func=cmd_models)

    s = sub.add_parser("camera", help="add a smoothed camera to an exported scene")
    s.add_argument("scene")
    s.add_argument("--rig", default="follow",
                   choices=["pov", "follow", "orbit", "static", "action"])
    s.add_argument("--target", default="auto", help="player name / steamid, or auto")
    s.add_argument("--smooth", type=float, default=-1.0,
                   help="smoothing window in seconds (rig default if omitted)")
    s.add_argument("--fov", type=float, default=0.0)
    s.add_argument("--distance", type=float, default=0.0, help="source units behind/around")
    s.add_argument("--height", type=float, default=0.0)
    s.add_argument("--orbit-speed", type=float, default=25.0, help="degrees per second")
    s.add_argument("--name", default="", help="actor id, default camera_<rig>")
    s.add_argument("--cam", default="", help="where to write the HLAE .cam file")
    s.set_defaults(func=cmd_camera)

    s = sub.add_parser("preview", help="top-down preview of a scene with a timeline")
    s.add_argument("scene")
    s.set_defaults(func=cmd_preview)

    s = sub.add_parser("update", help="check GitHub for a new version and update in place")
    s.add_argument("--check", action="store_true", help="only report, do not update")
    s.add_argument("--yes", action="store_true", help="update without asking")
    s.add_argument("--no-restart", action="store_true", help="do not relaunch afterwards")
    s.add_argument("--current", default="", help="pretend this version is installed (testing)")
    s.set_defaults(func=cmd_update)

    s = sub.add_parser("clips", help="find the interesting moments in a demo")
    s.add_argument("demo")
    s.add_argument("--min-kills", type=int, default=3, help="smallest multikill to report")
    s.add_argument("--pre", type=float, default=6.0, help="seconds before the action")
    s.add_argument("--post", type=float, default=3.0, help="seconds after the action")
    s.add_argument("--limit", type=int, default=25)
    s.add_argument("--no-cache", action="store_true")
    s.add_argument("--json", action="store_true")
    s.set_defaults(func=cmd_clips)

    s = sub.add_parser("ue", help="Unreal Engine: pick the engine folder and run the import")
    us = s.add_subparsers(dest="ue_cmd", required=True)
    x = us.add_parser("detect", help="show Unreal installs and projects found on this machine")
    x.add_argument("--search", default="", help="extra folder to look for .uproject files in")
    x = us.add_parser("set", help="choose the engine folder and the project")
    x.add_argument("--engine", default="", help="Unreal folder, e.g. D:\\Epic Games\\UE_5.4")
    x.add_argument("--project", default="", help="path to a .uproject file (or its folder)")
    x = us.add_parser("build", help="build the Level Sequence from a scene folder")
    x.add_argument("scene")
    x.add_argument("--package", default="", help="content path, default /Game/cs2toUE")
    x.add_argument("--name", default="", help="sequence asset name")
    x.add_argument("--scale", type=float, default=0.0)
    x.add_argument("--no-effects", action="store_true", help="skip smokes, molotovs, tracers")
    x.add_argument("--no-tracers", action="store_true", help="skip the per-shot beams")
    x.add_argument("--no-animations", action="store_true", help="skip animation sections")
    x.add_argument("--max-effects", type=int, default=0, help="cap the number of effect actors")
    x.add_argument("--dry-run", action="store_true", help="print the command instead of running")
    x = us.add_parser("models", help="import exported models into the project")
    x.add_argument("--path", default="", help="models folder (default: the models folder in the workspace)")
    x.add_argument("--scene", default="", help="also write ue_mapping.json for this scene")
    x.add_argument("--package", default="", help="content path, default /Game/cs2toUE/Models")
    x.add_argument("--dry-run", action="store_true")
    x = us.add_parser("map", help="import a converted map into the project")
    x.add_argument("map", help="map name from the library, or a folder with glb files")
    x.add_argument("--package", default="", help="content path, default /Game/cs2toUE/Maps")
    x.add_argument("--dry-run", action="store_true")
    s.set_defaults(func=cmd_ue)

    s = sub.add_parser("usage", help="how much disk space the workspace uses")
    s.set_defaults(func=cmd_usage)

    s = sub.add_parser("clean", help="free disk space in the workspace")
    s.add_argument("--all", action="store_true", help="downloads, exports, caches (not maps)")
    s.add_argument("--downloads", action="store_true")
    s.add_argument("--exports", action="store_true")
    s.add_argument("--cache", action="store_true")
    s.add_argument("--pipcache", action="store_true")
    s.add_argument("--maps", default="", help="map name(s) separated by comma, or 'all'")
    s.add_argument("--dry-run", action="store_true")
    s.set_defaults(func=cmd_clean)

    s = sub.add_parser("camio", help="inspect or convert HLAE .cam camera files")
    s.add_argument("cam")
    s.add_argument("--to-csv", default="", help="dump the cam file to csv")
    s.add_argument("--from-csv", default="", help="build a .cam from a csv of UE keys")
    s.add_argument("--scale", type=float, default=0.0)
    s.set_defaults(func=cmd_camio)

    s = sub.add_parser("gui", help="open the small graphical front end")
    s.set_defaults(func=cmd_gui)

    return p


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    try:
        args.func(args)
    except Fail as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        return 130
    return 0
