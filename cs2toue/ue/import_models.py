"""Import exported CS2 models (glb with skeleton + animations) into Unreal.

    UnrealEditor-Cmd.exe "MyProject.uproject" -run=pythonscript ^
        -script="import_models.py S:\\...\\workspace\\models --package=/Game/cs2toUE/Models"

Each model folder becomes its own content folder, so the asset paths match what
`cs2toue models mapping` writes into ue_mapping.json.
"""

import os
import sys

import unreal

DEFAULTS = {
    "path": os.environ.get("CS2TOUE_MODELS", ""),
    "package": "/Game/cs2toUE/Models",
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
    if not os.path.isdir(root):
        return []
    # a folder that directly contains glb files is one model
    out = []
    for dirpath, _dirs, files in os.walk(root):
        if any(f.lower().endswith((".glb", ".gltf")) for f in files):
            out.append(dirpath)
    return sorted(out)


def main(argv):
    opts = parse_args(argv)
    folders = model_folders(opts["path"])
    if not folders:
        unreal.log_error("cs2toUE: no glb files under {}".format(opts["path"]))
        return

    tools = unreal.AssetToolsHelpers.get_asset_tools()
    total = 0
    for folder in folders:
        name = os.path.basename(folder.rstrip("\\/"))
        package = "{}/{}".format(opts["package"].rstrip("/"), name)
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
        for task in tasks:
            total += len(task.get_editor_property("imported_object_paths") or [])

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
