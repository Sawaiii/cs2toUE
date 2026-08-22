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
    for cmd in ("DisableAllScreenMessages", "r.Streaming.HLODStrategy 2"):
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
        # Streaming ON with a pool that fits the card: turning streaming off made the
        # "video memory exhausted" banner worse, not better, because every texture
        # then loads at full resolution and stays resident.
        wanted = {"r.TextureStreaming": 1.0,
                  "r.Streaming.PoolSize": 1000.0,
                  "r.Streaming.LimitPoolSizeToVRAM": 1.0,
                  # a decompiled map is a bright sky over dark interiors; auto exposure
                  # chases that and blows the frame to white
                  "r.DefaultFeature.AutoExposure": 0.0,
                  "r.EyeAdaptation.MethodOverride": 2.0}
        if str(opts["quality"]).lower() == "preview":
            # A full map plus a dozen agents does not fit in 4 GB at full quality, and
            # the engine prints "video memory has been exhausted" across every frame
            # until it does. Preview trims what costs the most memory and the least
            # in a rough cut; "final" keeps all of it.
            wanted.update({
                "r.Streaming.MipBias": 2.0,
                "r.Streaming.PoolSize": 500.0,
                "r.Shadow.Virtual.Enable": 0.0,
                "r.Nanite.Streaming.StreamingPoolSize": 96.0,
                "r.VolumetricCloud": 0.0,
                "r.VolumetricFog": 0.0,
                "r.SSR.Quality": 0.0,
            })
        # These go through a method, not a property: setting "cvars" directly is
        # silently rejected, which is why none of this applied before.
        for name, value in wanted.items():
            try:
                cvars.add_or_update_console_variable(name, value)
            except Exception as exc:
                unreal.log_warning("cs2toUE: cvar {} skipped ({})".format(name, exc))
        try:
            cvars.set_editor_property(
                "start_console_commands",
                ["DisableAllScreenMessages", "r.Streaming.HLODStrategy 2"])
        except Exception as exc:
            unreal.log_warning("cs2toUE: start commands skipped ({})".format(exc))
        unreal.log("cs2toUE: {} render cvar(s) set".format(len(wanted)))
    except Exception as exc:
        unreal.log_warning("cs2toUE: console variable setting unavailable ({})".format(exc))

    # MRQ's own overrides: cinematic quality and no texture streaming, which is what
    # removes the "video memory exhausted" banner on a small card - a console command
    # cannot reach the render world, this setting is applied inside it
    try:
        over = config.find_or_add_setting_by_class(unreal.MoviePipelineGameOverrideSetting)
        for prop, value in (("cinematic_quality_settings", True),
                            ("texture_streaming",
                             unreal.MoviePipelineTextureStreamingMethod.NONE),
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
