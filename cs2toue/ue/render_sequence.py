"""Render a Level Sequence to an image sequence through Movie Render Queue.

Runs inside the offscreen editor like the other scripts:

    cs2toue ue render <scene>            # after `ue build` - one command to footage

The guide-style workflow renders by hand from the editor; this does the same render
(deferred pass, MRQ) without opening a window. Output is a PNG sequence - what an
editor drops into a timeline; encoding to a video file is a job for the edit, not for
the render.
"""

import os
import sys

import unreal

DEFAULTS = {
    "sequence": "",                     # /Game/cs2toUE/SEQ_name
    "level": "",                        # /Game/cs2toUE/L_SEQ_name or your map
    "out": "",                          # output folder (required)
    "resx": 1920,
    "resy": 1080,
    "quality": "final",                 # final | preview (spatial samples 7 / 1)
    "file_format": "png",               # png | jpg | exr
}


def parse_args(argv):
    opts = dict(DEFAULTS)
    for arg in argv:
        if arg.startswith("--"):
            key, _, val = arg[2:].partition("=")
            key = key.replace("-", "_")
            if key in opts:
                cur = opts[key]
                opts[key] = type(cur)(val) if not isinstance(cur, str) else val
        elif not opts["sequence"]:
            opts["sequence"] = arg
    return opts


def finish():
    try:
        if "-unattended" in unreal.SystemLibrary.get_command_line().lower():
            unreal.SystemLibrary.quit_editor()
    except Exception:
        pass


def main(argv):
    opts = parse_args(argv)
    if not opts["sequence"] or not opts["level"] or not opts["out"]:
        unreal.log_error("cs2toUE: render needs --sequence, --level and --out")
        return finish()

    # engine warnings must not be burnt into the frames, and the default streaming
    # pool is what triggers them on a 4 GB card
    for cmd in ("DisableAllScreenMessages", "r.Streaming.PoolSize 2000",
                "r.Streaming.HLODStrategy 2"):
        try:
            unreal.SystemLibrary.execute_console_command(None, cmd)
        except Exception:
            pass

    subsystem = unreal.get_editor_subsystem(unreal.MoviePipelineQueueSubsystem)
    if subsystem is None:
        unreal.log_error("cs2toUE: Movie Render Queue plugin is not enabled in this project")
        return finish()

    queue = subsystem.get_queue()
    queue.delete_all_jobs()
    job = queue.allocate_new_job(unreal.MoviePipelineExecutorJob)
    job.job_name = "cs2toUE render"
    job.sequence = unreal.SoftObjectPath(opts["sequence"])
    job.map = unreal.SoftObjectPath(opts["level"])

    config = job.get_configuration()
    config.find_or_add_setting_by_class(unreal.MoviePipelineDeferredPassBase)

    fmt = {"png": unreal.MoviePipelineImageSequenceOutput_PNG,
           "jpg": unreal.MoviePipelineImageSequenceOutput_JPG,
           "exr": unreal.MoviePipelineImageSequenceOutput_EXR}.get(
        str(opts["file_format"]).lower(), unreal.MoviePipelineImageSequenceOutput_PNG)
    config.find_or_add_setting_by_class(fmt)

    out_setting = config.find_or_add_setting_by_class(unreal.MoviePipelineOutputSetting)
    out_setting.output_directory = unreal.DirectoryPath(opts["out"])
    out_setting.output_resolution = unreal.IntPoint(int(opts["resx"]), int(opts["resy"]))
    out_setting.file_name_format = "{sequence_name}.{frame_number}"

    # The "video memory exhausted" banner is drawn by the streaming system inside the
    # render world, where a console command from the editor does not reach. Turning the
    # pool budget off removes the condition instead of hiding the message.
    try:
        cvars = config.find_or_add_setting_by_class(
            unreal.MoviePipelineConsoleVariableSetting)
        # turning streaming off removes the budget entirely: no budget, no banner
        wanted = {"r.TextureStreaming": 0.0,
                  "r.Streaming.PoolSize": 0.0,
                  "r.Streaming.LimitPoolSizeToVRAM": 0.0,
                  # A decompiled map is a bright sky over dark interiors; auto exposure
                  # chases that and blows the whole frame to white. Fixed exposure is
                  # what a moviemaker wants anyway.
                  "r.DefaultFeature.AutoExposure": 0.0,
                  "r.EyeAdaptation.MethodOverride": 2.0,
                  "r.DefaultFeature.AutoExposure.Bias": 1.0}
        try:
            entries = []
            for name, value in wanted.items():
                entry = unreal.MoviePipelineConsoleVariableEntry()
                entry.set_editor_property("name", name)
                entry.set_editor_property("value", value)
                entry.set_editor_property("is_enabled", True)
                entries.append(entry)
            cvars.set_editor_property("cvars", entries)
        except Exception:
            cvars.set_editor_property("console_variables", wanted)
        cvars.set_editor_property("start_console_commands", ["DisableAllScreenMessages"])
    except Exception as exc:
        unreal.log_warning("cs2toUE: could not set render cvars ({})".format(exc))

    # MRQ's own overrides: cinematic quality and no texture streaming, which is what
    # removes the "video memory exhausted" banner on a small card - a console command
    # cannot reach the render world, this setting is applied inside it
    try:
        over = config.find_or_add_setting_by_class(unreal.MoviePipelineGameOverrideSetting)
        for prop, value in (("cinematic_quality_settings", True),
                            ("texture_streaming",
                             unreal.MoviePipelineTextureStreamingMethod.DISABLED),
                            ("use_lod_zero", True),
                            ("disable_hlo_ds", True),
                            ("flush_grass_streaming", True)):
            try:
                over.set_editor_property(prop, value)
            except Exception:
                pass
    except Exception as exc:
        unreal.log_warning("cs2toUE: game override unavailable ({})".format(exc))

    aa = config.find_or_add_setting_by_class(unreal.MoviePipelineAntiAliasingSetting)
    if str(opts["quality"]).lower() == "preview":
        aa.spatial_sample_count = 1
        aa.temporal_sample_count = 1
    else:
        aa.spatial_sample_count = 1
        aa.temporal_sample_count = 7
        aa.override_anti_aliasing = True
        aa.anti_aliasing_method = unreal.AntiAliasingMethod.AAM_NONE

    unreal.log("cs2toUE: render start - {} on {} -> {} ({}x{}, {})".format(
        opts["sequence"], opts["level"], opts["out"], opts["resx"], opts["resy"],
        opts["quality"]))

    # keep a reference alive on the module, otherwise the executor is collected mid-render
    global _executor
    _executor = unreal.MoviePipelinePIEExecutor()

    def on_finished(executor, success):
        unreal.log("cs2toUE: render finished, success={}".format(success))
        finish()

    def on_error(executor, pipeline, fatal, text):
        unreal.log_error("cs2toUE: render error (fatal={}): {}".format(fatal, text))

    _executor.on_executor_finished_delegate.add_callable(on_finished)
    _executor.on_executor_errored_delegate.add_callable(on_error)
    subsystem.render_queue_with_executor_instance(_executor)
    # no finish() here: the editor must keep running until the delegate fires


if __name__ == "__main__":
    try:
        main(sys.argv[1:])
    except Exception:
        import traceback
        unreal.log_error("cs2toUE: " + traceback.format_exc())
        finish()
