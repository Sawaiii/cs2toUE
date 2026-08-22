"""Import exported CS2 models (glb with skeleton + animations) into Unreal.

    UnrealEditor-Cmd.exe "MyProject.uproject" -run=pythonscript ^
        -script="import_models.py S:\\...\\workspace\\models --package=/Game/cs2toUE/Models"

Each model folder becomes its own content folder, so the asset paths match what
`cs2toue models mapping` writes into ue_mapping.json.
"""

import json
import os
import sys

import unreal

DEFAULTS = {
    "path": os.environ.get("CS2TOUE_MODELS", ""),
    "package": "/Game/cs2toUE/Models",
    # importing a finished agent again costs many minutes, so a rerun skips what is
    # already in the project unless it is named explicitly
    "only": "",
    "skip_existing": 1,
}


def parse_args(argv):
    opts = dict(DEFAULTS)
    positional = []
    for arg in argv:
        if arg.startswith("--"):
            key, _, val = arg[2:].partition("=")
            key = key.replace("-", "_")
            if key in opts:
                opts[key] = val
        else:
            positional.append(arg)
    if positional:
        opts["path"] = positional[0]
    return opts


def model_folders(root):
    """[(model name, folder with glb files)].

    The name is the top folder under the models root - the same name the library and
    the mapping use. Taking the basename of the deep folder instead gave "ak47" for a
    model the rest of the program calls "weapon_rif_ak47", so its assets landed in a
    package nothing was looking in.
    """
    if not os.path.isdir(root):
        return []
    out = []
    for dirpath, _dirs, files in os.walk(root):
        if not any(f.lower().endswith((".glb", ".gltf")) for f in files):
            continue
        rel = os.path.relpath(dirpath, root)
        name = rel.split(os.sep)[0] if rel not in (".", "") else os.path.basename(root)
        out.append((name, dirpath))
    return sorted(out)


def main(argv):
    opts = parse_args(argv)
    folders = model_folders(opts["path"])
    if not folders:
        unreal.log_error("cs2toUE: no glb files under {}".format(opts["path"]))
        return

    tools = unreal.AssetToolsHelpers.get_asset_tools()
    # "+" not "," - Unreal splits an -ExecCmds value on commas, so a comma separated
    # list arrives as one name and everything after it is run as console commands
    raw = str(opts["only"]).replace(",", "+")
    only = [w.strip().lower() for w in raw.split("+") if w.strip()]
    total = 0
    for name, folder in folders:
        package = "{}/{}".format(opts["package"].rstrip("/"), name)
        if only and name.lower() not in only:
            continue
        if not only and int(opts["skip_existing"]):
            try:
                if unreal.EditorAssetLibrary.does_directory_exist(package):
                    unreal.log("cs2toUE: {} already imported, skipping".format(name))
                    continue
            except Exception:
                pass
        tasks = []
        for f in sorted(os.listdir(folder)):
            if not f.lower().endswith((".glb", ".gltf")):
                continue
            task = unreal.AssetImportTask()
            task.set_editor_property("filename", os.path.join(folder, f))
            task.set_editor_property("destination_path", package)
            task.set_editor_property("automated", True)
            task.set_editor_property("replace_existing", True)
            task.set_editor_property("save", True)
            tasks.append(task)
        if not tasks:
            continue
        unreal.log("cs2toUE: importing {} ({} files) -> {}".format(name, len(tasks), package))
        tools.import_asset_tasks(tasks)
        paths = []
        for task in tasks:
            paths += list(task.get_editor_property("imported_object_paths") or [])
        total += len(paths)
        # Unreal renames on import: "animation/anims/world/rifle/.../run_ne_rifle"
        # becomes "animation_anims_world_rifle__default_rifle_run_ne_rifle". Guessing
        # that rule is fragile, so the real paths are written down for the mapping
        # generator to read.
        try:
            manifest = os.path.join(folder, "imported_assets.json")
            with open(manifest, "w", encoding="utf-8") as fh:
                json.dump({"package": package, "assets": sorted(paths)}, fh, indent=1)
            unreal.log("cs2toUE: {} -> {} asset paths written".format(name, len(paths)))
        except Exception as exc:
            unreal.log_warning("cs2toUE: could not write manifest ({})".format(exc))

    unreal.log("cs2toUE: imported {} asset(s) from {} model folder(s)".format(
        total, len(folders)))
    unreal.log("cs2toUE: check the skeletal meshes and animations, then run build_sequence.py")


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
