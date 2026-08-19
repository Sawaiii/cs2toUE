"""Source (Half-Life / Source 2) <-> Unreal Engine coordinate conversion.

Source is right-handed, Z-up: +X forward, +Y left, +Z up.
Unreal is  left-handed, Z-up: +X forward, +Y right, +Z up.

So the two spaces differ by a mirror on Y:  P_ue = (x, -y, z).

Rotations follow from that mirror.  Taking Source AngleVectors()

    forward = ( cp*cy,               cp*sy,               -sp   )
    right   = (-sr*sp*cy + cr*sy,   -sr*sp*sy - cr*cy,    -sr*cp)
    up      = ( cr*sp*cy + sr*sy,    cr*sp*sy - sr*cy,     cr*cp)

mirroring each vector on Y and matching against the Unreal rotation matrix gives

    pitch_ue = -pitch_source
    yaw_ue   = -yaw_source
    roll_ue  = +roll_source      <- note: roll does NOT flip

Scale: 1 Source unit == 1 inch == 2.54 cm, and 1 Unreal unit == 1 cm, so a map
decompiled by Source 2 Viewer to glTF (which is in metres) and imported into UE with
the default 100x scene-unit conversion lines up with SCALE_INCH below.
"""

from __future__ import annotations

SCALE_INCH = 2.54   # real-world: 1 source unit -> 2.54 uu
SCALE_RAW = 1.0     # if you imported the map at raw source-unit scale


def pos_to_ue(x: float, y: float, z: float, scale: float = SCALE_INCH):
    return (x * scale, -y * scale, z * scale)


def pos_to_source(x: float, y: float, z: float, scale: float = SCALE_INCH):
    return (x / scale, -y / scale, z / scale)


def rot_to_ue(pitch: float, yaw: float, roll: float = 0.0):
    """Source QAngle (pitch, yaw, roll) -> Unreal (pitch, yaw, roll), degrees."""
    return (-pitch, -yaw, roll)


def rot_to_source(pitch: float, yaw: float, roll: float = 0.0):
    return (-pitch, -yaw, roll)


def fov_horizontal_to_vertical(fov_h: float, aspect: float = 16.0 / 9.0) -> float:
    import math
    t = math.tan(math.radians(fov_h) / 2.0) / aspect
    return 2.0 * math.degrees(math.atan(t))


def fov_to_ue_focal_length(fov_deg: float, sensor_width_mm: float = 36.0) -> float:
    """CineCameraComponent works in focal length; UE FOV is horizontal."""
    import math
    return sensor_width_mm / (2.0 * math.tan(math.radians(fov_deg) / 2.0))
