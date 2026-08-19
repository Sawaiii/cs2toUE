"""Demo parsing backends."""

from . import demoparser2_backend  # noqa: F401


def pick(info):
    """Choose a parsing backend for a demo."""
    if info.engine == "source2":
        return demoparser2_backend
    raise NotImplementedError(
        "CS:GO (Source 1) demos have no direct data backend here. Use the HLAE route: "
        "cs2toue play --demo <file> and record with mirv_camio / AGR, then import into "
        "Blender with afx-blender-scripts and export FBX to Unreal."
    )
