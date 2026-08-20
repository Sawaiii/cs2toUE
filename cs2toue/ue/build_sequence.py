"""Unreal Engine side of cs2toUE: turn a scene folder into a Level Sequence.

Run it INSIDE Unreal (it imports `unreal`), either from the editor Python console

    import sys; sys.argv = ["build_sequence.py", r"S:\\...\\exports\\my_demo"]
    exec(open(r"S:\\CLAUDE\\cs2toUE\\cs2toue\\ue\\build_sequence.py").read())

or headless / from the command line:

    UnrealEditor-Cmd.exe "MyProject.uproject" -run=pythonscript ^
        -script="S:\\CLAUDE\\cs2toUE\\cs2toue\\ue\\build_sequence.py S:\\...\\exports\\my_demo"

`cs2toue ue-cmd <scene>` prints the exact command line for your project.

The script is intentionally self-contained (stdlib + unreal only) so it can be copied
into a project without the rest of cs2toUE.
"""

import csv
import json
import math
import os
import sys

import unreal

# ----------------------------------------------------------------- parameters

DEFAULTS = {
    "scene": os.environ.get("CS2TOUE_SCENE", ""),
    "package": "/Game/cs2toUE",          # where the LevelSequence asset is created
    "name": "",                          # sequence asset name (default: demo name)
    "scale": 2.54,                       # source unit -> unreal unit (cm)
    "z_offset": 0.0,                     # extra height in unreal units
    "fps": 0.0,                          # 0 = take sample_fps from the scene
    "players": 1,
    "grenades": 1,
    "camera": 1,
    "visibility": 1,                     # hide actors while dead
    "animations": 1,                     # lay animation sections for skeletal models
    "effects": 1,                        # smokes, molotovs, explosions, flashes
    "tracers": 1,                        # one beam per shot (can be a lot)
    "max_effects": 1500,                 # hard cap so a long clip cannot flood the level
    "active_camera": "",                 # which camera gets the camera cut (default: first)
    "spawn_actors": 1,                   # 0 = only build tracks for actors already bound
    "mapping": "",                       # json file: actor kind/team -> asset path
    "level": "",                         # level to build into, e.g. /Game/cs2toUE/Maps/de_dust2
}


def parse_args(argv):
    opts = dict(DEFAULTS)
    positional = []
    for arg in argv:
        if arg.startswith("--"):
            key, _, val = arg[2:].partition("=")
            key = key.replace("-", "_")
            if key in opts:
                cur = opts[key]
                opts[key] = type(cur)(val) if not isinstance(cur, str) else val
            else:
                unreal.log_warning("cs2toUE: unknown option {}".format(arg))
        else:
            positional.append(arg)
    if positional:
        opts["scene"] = positional[0]
    return opts


# ----------------------------------------------------------------- coordinates
# Source is right handed (+Y left), Unreal is left handed (+Y right):
#   position -> (x, -y, z) * scale
#   rotation -> pitch = -pitch, yaw = -yaw, roll = +roll

def to_ue_pos(x, y, z, scale, z_offset):
    return unreal.Vector(x * scale, -y * scale, z * scale + z_offset)


def to_ue_rot(pitch, yaw, roll):
    return (-pitch, -yaw, roll)


def unwrap(prev, value):
    """Keep euler angles continuous so Sequencer does not spin the actor around."""
    if prev is None:
        return value
    delta = value - prev
    while delta > 180.0:
        value -= 360.0
        delta = value - prev
    while delta < -180.0:
        value += 360.0
        delta = value - prev
    return value


def fov_to_focal_length(fov_deg, sensor_width=36.0):
    fov_deg = max(1.0, min(179.0, fov_deg))
    return sensor_width / (2.0 * math.tan(math.radians(fov_deg) / 2.0))


# ----------------------------------------------------------------- unreal glue

# renamed between engine versions: SequenceTimeUnit up to 5.3, MovieSceneTimeUnit later
TIME_UNIT = getattr(unreal, "MovieSceneTimeUnit", None) or getattr(unreal, "SequenceTimeUnit")

def editor_actor_subsystem():
    try:
        return unreal.get_editor_subsystem(unreal.EditorActorSubsystem)
    except Exception:
        return None


def spawn_actor(asset_path, location, rotation, label, tags):
    sub = editor_actor_subsystem()
    actor = None
    obj = unreal.EditorAssetLibrary.load_asset(asset_path) if asset_path else None
    if obj is None:
        unreal.log_warning("cs2toUE: asset not found, using a cylinder: {}".format(asset_path))
        obj = unreal.EditorAssetLibrary.load_asset("/Engine/BasicShapes/Cylinder.Cylinder")
    if sub is not None:
        actor = sub.spawn_actor_from_object(obj, location, rotation)
    else:
        actor = unreal.EditorLevelLibrary.spawn_actor_from_object(obj, location, rotation)
    if actor:
        try:
            actor.set_actor_label(label)
            actor.tags = [unreal.Name(t) for t in tags if t]
        except Exception:
            pass
    return actor


def add_transform_track(seq, binding, rows, fps, scale, z_offset, duration):
    track = binding.add_track(unreal.MovieScene3DTransformTrack)
    section = track.add_section()
    section.set_range_seconds(0.0, max(duration, 1.0 / fps))
    ch = section.get_all_channels()   # locX locY locZ rotX(roll) rotY(pitch) rotZ(yaw) sclXYZ
    lin = unreal.MovieSceneKeyInterpolation.LINEAR
    unit = TIME_UNIT.DISPLAY_RATE

    prev_pitch = prev_yaw = prev_roll = None
    keys = 0
    for row in rows:
        t = float(row["time"])
        frame = unreal.FrameNumber(int(round(t * fps)))
        pos = to_ue_pos(float(row["x"]), float(row["y"]), float(row["z"]), scale, z_offset)
        pitch, yaw, roll = to_ue_rot(float(row["pitch"] or 0.0), float(row["yaw"] or 0.0),
                                     float(row["roll"] or 0.0))
        pitch = unwrap(prev_pitch, pitch)
        yaw = unwrap(prev_yaw, yaw)
        roll = unwrap(prev_roll, roll)
        prev_pitch, prev_yaw, prev_roll = pitch, yaw, roll

        ch[0].add_key(frame, pos.x, 0.0, unit, lin)
        ch[1].add_key(frame, pos.y, 0.0, unit, lin)
        ch[2].add_key(frame, pos.z, 0.0, unit, lin)
        ch[3].add_key(frame, roll, 0.0, unit, lin)
        ch[4].add_key(frame, pitch, 0.0, unit, lin)
        ch[5].add_key(frame, yaw, 0.0, unit, lin)
        keys += 1
    return keys


# ------------------------------------------------------------- animation states
# reference speeds in source units per second, used to pick a clip and its play rate
SPEED_RUN = 140.0
SPEED_MOVE = 20.0
REF_SPEED = {"run": 250.0, "walk": 130.0, "crouch_walk": 85.0}
MIN_SEGMENT = 0.25          # seconds - shorter states are absorbed by their neighbour


def row_state(row):
    if str(row.get("alive", "1")) in ("0", "False", "false"):
        return "death"
    duck = float(row.get("duck") or 0.0)
    speed = float(row.get("speed") or 0.0)
    if duck > 0.5:
        return "crouch_walk" if speed > SPEED_MOVE else "crouch_idle"
    if speed > SPEED_RUN:
        return "run"
    if speed > SPEED_MOVE:
        return "walk"
    return "idle"


def movement_segments(rows):
    """[(start_time, end_time, state, average_speed)] with the flicker filtered out."""
    segments = []
    for row in rows:
        t = float(row["time"])
        state = row_state(row)
        speed = float(row.get("speed") or 0.0)
        if segments and segments[-1][2] == state:
            seg = segments[-1]
            segments[-1] = (seg[0], t, state, (seg[3] * seg[4] + speed) / (seg[4] + 1), seg[4] + 1)
        else:
            segments.append((t, t, state, speed, 1))
    # absorb very short segments into the previous one
    merged = []
    for seg in segments:
        if merged and (seg[1] - seg[0]) < MIN_SEGMENT and seg[2] != "death":
            prev = merged[-1]
            merged[-1] = (prev[0], seg[1], prev[2], prev[3], prev[4])
        else:
            merged.append(seg)
    return [(s[0], s[1], s[2], s[3]) for s in merged]


def add_animation_track(seq, binding, rows, fps, anims, duration):
    """Lay one AnimSequence section per movement state onto the sequence."""
    if not anims:
        return 0
    try:
        track = binding.add_track(unreal.MovieSceneSkeletalAnimationTrack)
    except Exception as exc:
        unreal.log_warning("cs2toUE: no animation track ({})".format(exc))
        return 0

    made = 0
    for start, end, state, speed in movement_segments(rows):
        asset_path = anims.get(state) or anims.get("idle")
        if not asset_path:
            continue
        anim = unreal.EditorAssetLibrary.load_asset(asset_path)
        if anim is None:
            continue
        if state == "death":
            end = duration          # stay down until the end of the clip
        if end <= start:
            end = start + 1.0 / fps
        section = track.add_section()
        section.set_range_seconds(start, end)
        rate = 1.0
        ref = REF_SPEED.get(state)
        if ref:
            rate = max(0.4, min(2.5, speed / ref))
        _set_anim_params(section, anim, rate)
        made += 1
    return made


def _set_anim_params(section, anim, rate):
    """Params moved from a plain float to a channel across engine versions."""
    try:
        params = section.get_editor_property("params")
        params.set_editor_property("animation", anim)
        try:
            params.set_editor_property("play_rate", rate)
        except Exception:
            channel = params.get_editor_property("play_rate")
            channel.set_default(rate)
        section.set_editor_property("params", params)
    except Exception as exc:
        unreal.log_warning("cs2toUE: could not set animation params ({})".format(exc))


def add_visibility_track(seq, binding, rows, fps):
    """One bool key per alive/dead transition (bHidden is inverted visibility)."""
    transitions = []
    last = None
    for row in rows:
        alive = str(row.get("alive", "1")) not in ("0", "", "False", "false")
        if alive != last:
            transitions.append((float(row["time"]), alive))
            last = alive
    if len(transitions) <= 1 and (not transitions or transitions[0][1]):
        return 0
    track = binding.add_track(unreal.MovieSceneVisibilityTrack)
    section = track.add_section()
    section.set_range_seconds(0.0, float(rows[-1]["time"]) + 1.0 / fps)
    channel = section.get_all_channels()[0]
    unit = TIME_UNIT.DISPLAY_RATE
    for t, alive in transitions:
        channel.add_key(unreal.FrameNumber(int(round(t * fps))), bool(alive), 0.0, unit)
    return len(transitions)


def _camera_component(cam):
    """CineCameraActor exposes its component differently across engine versions."""
    for way in ("get_cine_camera_component",):
        try:
            comp = getattr(cam, way)()
            if comp is not None:
                return comp
        except Exception:
            pass
    for prop in ("cine_camera_component", "camera_component"):
        try:
            comp = cam.get_editor_property(prop)
            if comp is not None:
                return comp
        except Exception:
            continue
    return None


def add_camera(seq, label, rows, fps, scale, z_offset, duration, make_cut):
    """One camera actor with its motion; only the active one owns the camera cut.

    A sequence must carry a single camera cut track - one per camera made Sequencer
    fight over the view when a scene had several rigs exported side by side.
    """
    sub = editor_actor_subsystem()
    loc = to_ue_pos(float(rows[0]["x"]), float(rows[0]["y"]), float(rows[0]["z"]), scale, z_offset)
    if sub is not None:
        cam = sub.spawn_actor_from_class(unreal.CineCameraActor, loc, unreal.Rotator(0, 0, 0))
    else:
        cam = unreal.EditorLevelLibrary.spawn_actor_from_class(
            unreal.CineCameraActor, loc, unreal.Rotator(0, 0, 0))
    cam.set_actor_label(label or "cs2toUE_Camera")

    binding = seq.add_possessable(cam)
    add_transform_track(seq, binding, rows, fps, scale, z_offset, duration)

    # focal length from the demo fov (horizontal)
    comp = _camera_component(cam)
    fov_values = [float(r["fov"]) for r in rows if r.get("fov") not in (None, "")]
    if comp is not None and fov_values:
        comp_binding = seq.add_possessable(comp)
        track = comp_binding.add_track(unreal.MovieSceneFloatTrack)
        track.set_property_name_and_path("CurrentFocalLength", "CurrentFocalLength")
        section = track.add_section()
        section.set_range_seconds(0.0, duration)
        channel = section.get_all_channels()[0]
        unit = TIME_UNIT.DISPLAY_RATE
        lin = unreal.MovieSceneKeyInterpolation.LINEAR
        last = None
        for row in rows:
            fov = row.get("fov")
            if fov in (None, ""):
                continue
            fl = round(fov_to_focal_length(float(fov)), 4)
            if fl == last:
                continue
            last = fl
            channel.add_key(unreal.FrameNumber(int(round(float(row["time"]) * fps))),
                            fl, 0.0, unit, lin)

    # camera cut so the sequence actually looks through it
    if make_cut:
        cut_track = seq.add_track(unreal.MovieSceneCameraCutTrack)
        cut_section = cut_track.add_section()
        cut_section.set_range_seconds(0.0, duration)
        try:
            cut_section.set_camera_binding_id(
                unreal.MovieSceneSequenceExtensions.get_binding_id(seq, binding))
        except Exception:
            binding_id = unreal.MovieSceneObjectBindingID()
            binding_id.set_editor_property("guid", binding.get_id())
            cut_section.set_camera_binding_id(binding_id)
    return cam


# ----------------------------------------------------------------- effects
# radius in source units, used to size the fallback shapes
EFFECT_RADIUS = {"smoke": 144.0, "molotov": 150.0, "he": 70.0, "flash": 50.0,
                 "decoy": 60.0, "bomb": 250.0}
FALLBACK_SHAPE = {
    "smoke": "/Engine/BasicShapes/Sphere.Sphere",
    "molotov": "/Engine/BasicShapes/Cylinder.Cylinder",
    "he": "/Engine/BasicShapes/Sphere.Sphere",
    "flash": "/Engine/BasicShapes/Sphere.Sphere",
    "decoy": "/Engine/BasicShapes/Sphere.Sphere",
    "bomb": "/Engine/BasicShapes/Sphere.Sphere",
    "tracer": "/Engine/BasicShapes/Cylinder.Cylinder",
}


def spawn_effect_actor(effect, mapping, scale, z_offset):
    """One actor per effect. A Niagara system from the mapping is used when present."""
    kind = effect["type"]
    asset_path = mapping.get("effect." + kind) or FALLBACK_SHAPE.get(kind)
    asset = unreal.EditorAssetLibrary.load_asset(asset_path) if asset_path else None
    if asset is None:
        return None
    sub = editor_actor_subsystem()
    pos = effect["pos"]
    loc = to_ue_pos(pos[0], pos[1], pos[2], scale, z_offset)
    rot = unreal.Rotator(0, 0, 0)
    mesh_scale = None

    if kind == "tracer":
        end = effect.get("end") or pos
        loc_end = to_ue_pos(end[0], end[1], end[2], scale, z_offset)
        delta = unreal.Vector(loc_end.x - loc.x, loc_end.y - loc.y, loc_end.z - loc.z)
        length = max(1.0, math.sqrt(delta.x ** 2 + delta.y ** 2 + delta.z ** 2))
        loc = unreal.Vector((loc.x + loc_end.x) / 2, (loc.y + loc_end.y) / 2,
                            (loc.z + loc_end.z) / 2)
        try:
            rot = unreal.MathLibrary.make_rot_from_z(delta)
        except Exception:
            rot = unreal.Rotator(0, 0, 0)
        # the engine cylinder is 100 cm tall and 100 cm wide
        mesh_scale = unreal.Vector(0.02, 0.02, length / 100.0)
    elif asset_path in FALLBACK_SHAPE.values():
        radius_uu = EFFECT_RADIUS.get(kind, 80.0) * scale
        s = radius_uu / 50.0
        mesh_scale = unreal.Vector(s, s, s if kind != "molotov" else s * 0.15)

    actor = (sub.spawn_actor_from_object(asset, loc, rot) if sub
             else unreal.EditorLevelLibrary.spawn_actor_from_object(asset, loc, rot))
    if actor is None:
        return None
    try:
        if mesh_scale:
            actor.set_actor_scale3d(mesh_scale)
        actor.set_actor_label("fx_{}_{:.2f}".format(kind, effect["time"]))
        actor.set_folder_path("cs2toUE/Effects")
        actor.tags = [unreal.Name("cs2toue_fx"), unreal.Name(kind)]
    except Exception:
        pass
    return actor


def add_effects(seq, effects, fps, scale, z_offset, mapping, duration, max_effects):
    """Spawn effect actors and make each one visible only for its own lifetime."""
    made = 0
    for effect in effects:
        if made >= max_effects:
            unreal.log_warning(
                "cs2toUE: effect limit {} reached, {} effects skipped".format(
                    max_effects, len(effects) - made))
            break
        actor = spawn_effect_actor(effect, mapping, scale, z_offset)
        if actor is None:
            continue
        start = float(effect["time"])
        end = min(duration, start + float(effect.get("duration") or 0.5))
        binding = seq.add_possessable(actor)
        try:
            track = binding.add_track(unreal.MovieSceneVisibilityTrack)
            section = track.add_section()
            section.set_range_seconds(0.0, duration)
            channel = section.get_all_channels()[0]
            unit = TIME_UNIT.DISPLAY_RATE
            channel.add_key(unreal.FrameNumber(0), False, 0.0, unit)
            channel.add_key(unreal.FrameNumber(int(round(start * fps))), True, 0.0, unit)
            channel.add_key(unreal.FrameNumber(int(round(end * fps))), False, 0.0, unit)
        except Exception as exc:
            unreal.log_warning("cs2toUE: no visibility track for effect ({})".format(exc))
        made += 1
    return made


def add_event_markers(seq, events, fps):
    """Round starts and kills as sequencer marked frames - handy for scrubbing."""
    try:
        marks = []
        for ev in events:
            if ev["type"] in ("round_start", "player_death", "bomb_planted"):
                mark = unreal.MovieSceneMarkedFrame()
                mark.set_editor_property("frame_number",
                                         unreal.FrameNumber(int(round(ev["time"] * fps))))
                mark.set_editor_property("label", ev["type"])
                marks.append(mark)
        for m in marks[:512]:
            unreal.MovieSceneSequenceExtensions.add_marked_frame(seq, m)
    except Exception as exc:
        unreal.log_warning("cs2toUE: marked frames skipped ({})".format(exc))


# ----------------------------------------------------------------- main

def mapping_for(actor, mapping):
    """Mapping entry for an actor: either an asset path, or a dict with a skeletal mesh.

    {"player.CT": {"skeletal_mesh": "/Game/.../SK_ctm.SK_ctm",
                   "animations": {"idle": "...", "run": "..."}}}
    """
    key_specific = "{}.{}".format(actor.get("kind"), actor.get("team") or actor.get("name"))
    for key in (key_specific, actor.get("kind"), "default"):
        if key in mapping:
            return mapping[key]
    return "/Engine/BasicShapes/Cylinder.Cylinder"


def main(argv):
    opts = parse_args(argv)
    scene_dir = opts["scene"]
    if not scene_dir:
        unreal.log_error("cs2toUE: no scene folder given")
        return
    scene_dir = scene_dir.rstrip("\"")
    with open(os.path.join(scene_dir, "scene.json"), "r", encoding="utf-8") as fh:
        scene = json.load(fh)
    meta = scene["meta"]

    fps = float(opts["fps"]) or float(meta.get("sample_fps") or 30.0)
    # keys are placed on the display-rate grid, so both must use the same number: a
    # fractional sample rate (64/3 = 21.33) against an integer display rate would
    # stretch the whole clip by the difference
    fps = max(1, int(round(fps)))
    scale = float(opts["scale"])
    z_offset = float(opts["z_offset"])
    seq_name = opts["name"] or "SEQ_" + "".join(
        c if c.isalnum() else "_" for c in os.path.splitext(str(meta.get("demo", "demo")))[0])

    mapping = {}
    mapping_path = opts["mapping"] or os.path.join(scene_dir, "ue_mapping.json")
    if os.path.isfile(mapping_path):
        with open(mapping_path, "r", encoding="utf-8") as fh:
            mapping = json.load(fh)

    unreal.log("cs2toUE: building {} from {} ({} actors, {} fps)".format(
        seq_name, scene_dir, len(scene["actors"]), fps))

    # actors go into a real level: either the one asked for, or a fresh one that gets
    # saved at the end - bindings into an unsaved Untitled world die with the editor
    if opts["level"]:
        if not unreal.EditorLevelLibrary.load_level(str(opts["level"])):
            unreal.log_error("cs2toUE: could not load level {}".format(opts["level"]))
            return

    seq = unreal.AssetToolsHelpers.get_asset_tools().create_asset(
        seq_name, opts["package"], unreal.LevelSequence, unreal.LevelSequenceFactoryNew())
    seq.set_display_rate(unreal.FrameRate(int(round(fps)), 1))

    # ---- load all tracks first so the sequence length is known up front
    loaded = []
    duration = 0.0
    for actor in scene["actors"]:
        if not actor.get("track"):
            continue
        if actor["kind"] == "player" and not int(opts["players"]):
            continue
        if actor["kind"] == "grenade" and not int(opts["grenades"]):
            continue
        if actor["kind"] == "camera" and not int(opts["camera"]):
            continue
        path = os.path.join(scene_dir, actor["track"].replace("/", os.sep))
        with open(path, "r", encoding="utf-8", newline="") as fh:
            rows = list(csv.DictReader(fh))
        if not rows:
            continue
        duration = max(duration, float(rows[-1]["time"]))
        loaded.append((actor, rows))

    duration = max(duration, 1.0 / fps)
    seq.set_playback_start_seconds(0.0)
    seq.set_playback_end_seconds(duration)

    total_keys = 0
    cameras = []
    with unreal.ScopedSlowTask(len(loaded), "cs2toUE: building sequence") as task:
        # no dialog in headless mode: Slate is absent under -unattended and the
        # attempt to open one brings the whole editor down
        try:
            if "-unattended" not in unreal.SystemLibrary.get_command_line().lower():
                task.make_dialog(True)
        except Exception:
            pass
        for actor, rows in loaded:
            if task.should_cancel():
                break
            task.enter_progress_frame(1, actor["name"] or actor["id"])
            unreal.log("cs2toUE: + {} ({} rows)".format(actor.get("id"), len(rows)))
            if actor["kind"] == "camera":
                cameras.append((actor, rows))
                continue
            first = rows[0]
            loc = to_ue_pos(float(first["x"]), float(first["y"]), float(first["z"]),
                            scale, z_offset)
            label = "{}_{}".format(actor["kind"], actor["name"] or actor["id"])
            entry = mapping_for(actor, mapping)
            anims = {}
            asset_path = entry
            if isinstance(entry, dict):
                asset_path = entry.get("skeletal_mesh") or entry.get("asset") or ""
                anims = entry.get("animations") or {}
            spawned = None
            if int(opts["spawn_actors"]):
                spawned = spawn_actor(asset_path, loc, unreal.Rotator(0, 0, 0), label,
                                      [actor["kind"], actor.get("team", "")])
            if spawned is None:
                continue
            binding = seq.add_possessable(spawned)
            total_keys += add_transform_track(seq, binding, rows, fps, scale, z_offset, duration)
            if anims and int(opts["animations"]):
                n = add_animation_track(seq, binding, rows, fps, anims, duration)
                if n:
                    unreal.log("cs2toUE: {} - {} animation sections".format(label, n))
            if int(opts["visibility"]) and actor["kind"] == "player":
                add_visibility_track(seq, binding, rows, fps)

    if cameras:
        want = str(opts["active_camera"]).strip().lower()
        active = 0
        for i, (actor, _rows) in enumerate(cameras):
            if want and want in (str(actor.get("id", "")).lower(),
                                 str(actor.get("name", "")).lower()):
                active = i
                break
        for i, (actor, rows) in enumerate(cameras):
            label = actor.get("id") or actor.get("name") or "cs2toUE_Camera"
            unreal.log("cs2toUE: + camera {}".format(label))
            add_camera(seq, label, rows, fps, scale, z_offset, duration,
                       make_cut=(i == active))
        unreal.log("cs2toUE: {} cameras, the cut follows '{}'".format(
            len(cameras), cameras[active][0].get("id")))

    unreal.log("cs2toUE: tracks done, placing effects")
    effects = scene.get("effects", [])
    if effects and int(opts["effects"]):
        if not int(opts["tracers"]):
            effects = [e for e in effects if e["type"] != "tracer"]
        n = add_effects(seq, effects, fps, scale, z_offset, mapping, duration,
                        int(opts["max_effects"]))
        unreal.log("cs2toUE: {} effects placed".format(n))

    add_event_markers(seq, scene.get("events", []), fps)
    unreal.EditorAssetLibrary.save_loaded_asset(seq)

    world = unreal.EditorLevelLibrary.get_editor_world()
    world_path = world.get_path_name() if world else ""
    if world_path.startswith("/Temp/"):
        level_path = "{}/L_{}".format(opts["package"].rstrip("/"), seq_name)
        if unreal.EditorLoadingAndSavingUtils.save_map(world, level_path):
            unreal.log("cs2toUE: level saved: {}".format(level_path))
    else:
        unreal.EditorLoadingAndSavingUtils.save_dirty_packages(True, True)
    unreal.log("cs2toUE: done - {} keyframes, {:.1f}s, asset {}/{}".format(
        total_keys, duration, opts["package"], seq_name))


def finish():
    """Close the editor when running headless; stay open in an interactive session."""
    try:
        if "-unattended" in unreal.SystemLibrary.get_command_line().lower():
            unreal.SystemLibrary.quit_editor()
    except Exception:
        pass


if __name__ == "__main__":
    try:
        main(sys.argv[1:])
    except Exception:
        import traceback
        unreal.log_error("cs2toUE: " + traceback.format_exc())
    finally:
        finish()
