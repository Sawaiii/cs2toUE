"""Import a decompiled CS2 map (glb/gltf from Source 2 Viewer) into Unreal.

Run inside Unreal:

    UnrealEditor-Cmd.exe "MyProject.uproject" -run=pythonscript ^
        -script="import_map.py S:\\...\\assets\\de_dust2 --package=/Game/cs2toUE/Maps"

Every .glb under the folder is imported; with --spawn=1 the results are placed at the
world origin, which is exactly where the demo coordinates expect the map to be (the
scene tracks use raw Source coordinates scaled by --scale in build_sequence.py).
"""

import json
import os
import sys

import unreal

DEFAULTS = {
    "path": os.environ.get("CS2TOUE_MAP", ""),
    "package": "/Game/cs2toUE/Maps",
    "spawn": 1,
    "scale": 100.0,   # glTF is in metres; 100 uu per metre keeps 1 uu = 1 cm
    "clean": 1,       # skip helper geometry (skybox, clips, triggers, nav)
}

# helper geometry nobody wants in a cinematic level - the by-hand workflow deletes
# these after import, one by one
CLEAN_TOKENS = ("physics", "skybox", "3dsky", "tools_", "toolsclip", "clip_", "_clip",
                "trigger", "navmesh", "_nav", "occluder", "lightprobe",
                "skip", "hint", "blocklight", "playerclip")


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
            positional.append(arg)
    if positional:
        opts["path"] = positional[0]
    return opts


def collect(path, clean=True):
    if os.path.isfile(path):
        return [path], []
    out, skipped = [], []
    for root, _dirs, files in os.walk(path):
        for f in files:
            if not f.lower().endswith((".glb", ".gltf", ".fbx")):
                continue
            full = os.path.join(root, f)
            low = f.lower()
            if clean and any(tok in low for tok in CLEAN_TOKENS):
                skipped.append(full)
                continue
            out.append(full)
    return sorted(out), sorted(skipped)


def _key(name: str) -> str:
    """Loose key so an Unreal asset name still matches its glTF mesh name."""
    return "".join(c for c in str(name).lower() if c.isalnum())


def load_placement(path):
    """{mesh key: transform} written by cs2toue before the import.

    Most of a decompiled map is baked in world space and belongs at the origin; a few
    hundred props carry a real transform, and without this they would all land in a
    heap at 0,0,0.
    """
    folder = path if os.path.isdir(path) else os.path.dirname(path)
    out = {}
    for name in ("placement.json",):
        f = os.path.join(folder, name)
        if not os.path.isfile(f):
            continue
        try:
            with open(f, "r", encoding="utf-8") as fh:
                data = json.load(fh)
        except Exception as exc:
            unreal.log_warning("cs2toUE: placement unreadable ({})".format(exc))
            continue
        for item in data.get("items", []):
            if item.get("translation") == [0.0, 0.0, 0.0] and                     item.get("scale") == [1.0, 1.0, 1.0]:
                continue          # baked - the origin is already right
            out[_key(item.get("mesh", ""))] = item
        unreal.log("cs2toUE: placement for {} mesh(es) loaded".format(len(out)))
    return out


def main(argv):
    opts = parse_args(argv)
    files, skipped = collect(opts["path"], clean=bool(int(opts["clean"])))
    if skipped:
        unreal.log("cs2toUE: {} helper meshes skipped (skybox/clip/trigger); "
                   "--clean=0 keeps them".format(len(skipped)))
    if not files:
        unreal.log_error("cs2toUE: no glb/gltf/fbx found under {}".format(opts["path"]))
        return
    unreal.log("cs2toUE: importing {} file(s) into {}".format(len(files), opts["package"]))

    tasks = []
    for f in files:
        task = unreal.AssetImportTask()
        task.set_editor_property("filename", f)
        task.set_editor_property("destination_path", opts["package"])
        task.set_editor_property("automated", True)
        task.set_editor_property("replace_existing", True)
        task.set_editor_property("save", True)
        tasks.append(task)
    unreal.AssetToolsHelpers.get_asset_tools().import_asset_tasks(tasks)

    imported = []
    for task in tasks:
        imported += list(task.get_editor_property("imported_object_paths") or [])
    unreal.log("cs2toUE: imported {} asset(s)".format(len(imported)))

    if int(opts["spawn"]):
        sub = None
        try:
            sub = unreal.get_editor_subsystem(unreal.EditorActorSubsystem)
        except Exception:
            pass
        placed = load_placement(opts["path"])
        origin = unreal.Vector(0, 0, 0)
        rot = unreal.Rotator(0, 0, 0)
        off = 0
        for path in imported:
            asset = unreal.EditorAssetLibrary.load_asset(path)
            if not isinstance(asset, (unreal.StaticMesh, unreal.Blueprint)):
                continue
            where = placed.get(_key(asset.get_name()))
            loc, rotation, scale = origin, rot, unreal.Vector(1, 1, 1)
            if where:
                # glTF is Y up right handed in metres, Unreal is Z up left handed in cm
                tx, ty, tz = where["translation"]
                loc = unreal.Vector(-tz * 100.0, tx * 100.0, ty * 100.0)
                sx, sy, sz = where["scale"]
                scale = unreal.Vector(sz, sx, sy)
                if any(abs(v) > 0.01 for v in where["rotation"]):
                    rx, ry, rz = where["rotation"]
                    rotation = unreal.Rotator(ry, -rz, rx)
                off += 1
            actor = (sub.spawn_actor_from_object(asset, loc, rotation) if sub
                     else unreal.EditorLevelLibrary.spawn_actor_from_object(asset, loc, rotation))
            if actor:
                actor.set_actor_label("cs2map_" + asset.get_name())
                actor.set_actor_scale3d(scale)
        unreal.log("cs2toUE: {} mesh(es) placed by transform, the rest are baked "
                   "in world space".format(off))
    try:
        unreal.EditorLoadingAndSavingUtils.save_dirty_packages(True, True)
    except Exception as exc:
        unreal.log_warning("cs2toUE: could not save ({})".format(exc))
    unreal.log("cs2toUE: map import done")


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
