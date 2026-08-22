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
import re
import sys

import unreal

DEFAULTS = {
    "path": os.environ.get("CS2TOUE_MAP", ""),
    "package": "/Game/cs2toUE/Maps",
    "spawn": 1,
    "scale": 100.0,   # glTF is in metres; 100 uu per metre keeps 1 uu = 1 cm
    "clean": 1,       # skip helper geometry (skybox, clips, triggers, nav)
    # re-place actors from assets already in the project, without
    # importing the glb again - the import is the slow part
    "reuse": 0,
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


def _base_key(name: str) -> str:
    """Key that survives the renaming between exporter and engine.

    Three differences, both tools' habits rather than any one map's:
      - the exporter writes one mesh per fragment ("..._ibeams_0_fragment10");
      - Unreal appends a 32 character content hash when two assets would collide;
      - dots in a mesh name become underscores, and Unreal often extends the name
        ("a.b" -> "a_b_v1_bg_studio_lod0"), so matching has to allow a prefix.
    """
    low = str(name).lower()
    low = re.sub(r"_fragment\d+$", "", low)
    low = re.sub(r"_[0-9a-f]{32}$", "", low)
    return "".join(c for c in low if c.isalnum())


class AssetIndex:
    """Finds the imported mesh for a glTF mesh name, exact hit or shortest prefix."""

    def __init__(self):
        self.exact = {}
        self.sorted_keys = []

    def add(self, key, asset):
        self.exact.setdefault(key, []).append(asset)

    def freeze(self):
        for group in self.exact.values():
            group.sort(key=lambda a: a.get_name())
        self.sorted_keys = sorted(self.exact, key=len)

    def find(self, key):
        hit = self.exact.get(key)
        if hit:
            return hit
        for candidate in self.sorted_keys:
            if candidate.startswith(key) or key.startswith(candidate):
                return self.exact[candidate]
        return []


def load_placement(path):
    """[{mesh, translation, rotation, scale}] written by cs2toue before the import."""
    folder = path if os.path.isdir(path) else os.path.dirname(path)
    # the file sits next to the glb, which is nested a few folders below the map root
    f = ""
    for root, _dirs, files in os.walk(folder):
        if "placement.json" in files:
            f = os.path.join(root, "placement.json")
            break
    if not f:
        unreal.log_warning("cs2toUE: no placement.json - props will sit at the origin")
        return []
    try:
        with open(f, "r", encoding="utf-8") as fh:
            return json.load(fh).get("items", [])
    except Exception as exc:
        unreal.log_warning("cs2toUE: placement unreadable ({})".format(exc))
        return []


def to_ue(item):
    """glTF (Y up, right handed, metres) -> Unreal (Z up, left handed, centimetres)."""
    tx, ty, tz = item["translation"]
    sx, sy, sz = item["scale"]
    rx, ry, rz = item["rotation"]
    loc = unreal.Vector(-tz * 100.0, tx * 100.0, ty * 100.0)
    scale = unreal.Vector(sz, sx, sy)
    # unreal.Rotator takes (roll, pitch, yaw), not (pitch, yaw, roll)
    rot = unreal.Rotator(rx, ry, -rz)
    return loc, rot, scale


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
    imported = []
    if int(opts["reuse"]):
        try:
            imported = list(unreal.EditorAssetLibrary.list_assets(
                opts["package"], recursive=True))
            unreal.log("cs2toUE: reusing {} asset(s) already in the project".format(
                len(imported)))
        except Exception as exc:
            unreal.log_error("cs2toUE: nothing to reuse ({})".format(exc))
            return
    else:
        unreal.AssetToolsHelpers.get_asset_tools().import_asset_tasks(tasks)
        for task in tasks:
            imported += list(task.get_editor_property("imported_object_paths") or [])
        unreal.log("cs2toUE: imported {} asset(s)".format(len(imported)))

    if int(opts["spawn"]):
        sub = None
        try:
            sub = unreal.get_editor_subsystem(unreal.EditorActorSubsystem)
        except Exception:
            pass
        cleared = 0
        try:
            for actor in (sub.get_all_level_actors() if sub else []):
                if str(actor.get_actor_label()).startswith("cs2map_"):
                    sub.destroy_actor(actor)
                    cleared += 1
        except Exception as exc:
            unreal.log_warning("cs2toUE: could not clear old map actors ({})".format(exc))
        if cleared:
            unreal.log("cs2toUE: {} map actor(s) from a previous run removed".format(cleared))

        # index the imported meshes by the key their glTF name reduces to
        index = AssetIndex()
        for path in imported:
            asset = unreal.EditorAssetLibrary.load_asset(path)
            if not isinstance(asset, (unreal.StaticMesh, unreal.Blueprint)):
                continue
            index.add(_base_key(asset.get_name()), asset)
        index.freeze()

        # One actor per placement, not per asset: a prop that appears forty times in
        # the map is forty actors sharing one mesh, and Unreal deduplicates identical
        # geometry, so the counts on the two sides do not have to match.
        items = load_placement(opts["path"])
        used = set()
        placed = 0
        seen_in_group = {}
        for item in items:
            key = _base_key(item.get("mesh", ""))
            group = index.find(key)
            if not group:
                continue
            nth = seen_in_group.get(key, 0)
            asset = group[nth] if nth < len(group) else group[0]
            seen_in_group[key] = nth + 1
            used.add(key)
            loc, rot, scale = to_ue(item)
            actor = (sub.spawn_actor_from_object(asset, loc, rot) if sub
                     else unreal.EditorLevelLibrary.spawn_actor_from_object(asset, loc, rot))
            if actor:
                actor.set_actor_label("cs2map_" + asset.get_name())
                actor.set_actor_scale3d(scale)
                placed += 1

        # anything the scene graph never mentioned still belongs in the level
        origin, no_rot = unreal.Vector(0, 0, 0), unreal.Rotator(0, 0, 0)
        leftover = 0
        for key, group in index.exact.items():
            if key in used:
                continue
            for asset in group:
                actor = (sub.spawn_actor_from_object(asset, origin, no_rot) if sub
                         else unreal.EditorLevelLibrary.spawn_actor_from_object(
                             asset, origin, no_rot))
                if actor:
                    actor.set_actor_label("cs2map_" + asset.get_name())
                    leftover += 1
        unreal.log("cs2toUE: {} mesh(es) placed from the scene graph, {} without a "
                   "placement entry".format(placed, leftover))

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
