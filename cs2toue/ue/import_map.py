"""Import a decompiled CS2 map (glb/gltf from Source 2 Viewer) into Unreal.

Run inside Unreal:

    UnrealEditor-Cmd.exe "MyProject.uproject" -run=pythonscript ^
        -script="import_map.py S:\\...\\assets\\de_dust2 --package=/Game/cs2toUE/Maps"

Every .glb under the folder is imported; with --spawn=1 the results are placed at the
world origin, which is exactly where the demo coordinates expect the map to be (the
scene tracks use raw Source coordinates scaled by --scale in build_sequence.py).
"""

import os
import sys

import unreal

DEFAULTS = {
    "path": os.environ.get("CS2TOUE_MAP", ""),
    "package": "/Game/cs2toUE/Maps",
    "spawn": 1,
    "scale": 100.0,   # glTF is in metres; 100 uu per metre keeps 1 uu = 1 cm
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
            positional.append(arg)
    if positional:
        opts["path"] = positional[0]
    return opts


def collect(path):
    if os.path.isfile(path):
        return [path]
    out = []
    for root, _dirs, files in os.walk(path):
        for f in files:
            if f.lower().endswith((".glb", ".gltf", ".fbx")):
                out.append(os.path.join(root, f))
    return sorted(out)


def main(argv):
    opts = parse_args(argv)
    files = collect(opts["path"])
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
        origin = unreal.Vector(0, 0, 0)
        rot = unreal.Rotator(0, 0, 0)
        for path in imported:
            asset = unreal.EditorAssetLibrary.load_asset(path)
            if not isinstance(asset, (unreal.StaticMesh, unreal.Blueprint)):
                continue
            actor = (sub.spawn_actor_from_object(asset, origin, rot) if sub
                     else unreal.EditorLevelLibrary.spawn_actor_from_object(asset, origin, rot))
            if actor:
                actor.set_actor_label("cs2map_" + asset.get_name())
                actor.set_actor_scale3d(unreal.Vector(1, 1, 1))
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
