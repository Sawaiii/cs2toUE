"""HLAE Camera IO (.cam) reader / writer.

Format, straight from advancedfx/shared/CamIO.cpp:

    advancedfx Cam
    version 2
    channels time xPosition yPosition zPosition xRotation yRotation zRotation fov
    DATA
    <time> <x> <y> <z> <xRot> <yRot> <zRot> <fov>

Channel naming is a trap: as afx-blender-scripts decodes it
(QAngle(words[5], words[6], words[4]) with QAngle(pitch, yaw, roll)),

    xRotation = roll, yRotation = pitch, zRotation = yaw

Positions are Source units, angles degrees, fov horizontal degrees.  Version 1 files
also carry a `scaleFov none|alienSwarm` line; for those, `none` actually means the fov
went through Alien Swarm scaling (the two were swapped back then), which is undone here
so callers always get a real horizontal fov.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from pathlib import Path

HEADER_CHANNELS = ("time xPosition yPosition zPosition "
                   "xRotation yRotation zRotation fov")


@dataclass
class CamFrame:
    time: float
    x: float
    y: float
    z: float
    pitch: float
    yaw: float
    roll: float
    fov: float


@dataclass
class CamPath:
    version: int = 2
    scale_fov: str = ""
    frames: list = field(default_factory=list)

    def __len__(self) -> int:
        return len(self.frames)

    @property
    def duration(self) -> float:
        return (self.frames[-1].time - self.frames[0].time) if len(self.frames) > 1 else 0.0


def _alien_swarm_fov(width: float, height: float, fov: float) -> float:
    if not height:
        return fov
    ratio = (width / height) / (4.0 / 3.0)
    t = ratio * math.tan(math.radians(0.5 * fov))
    return 2.0 * math.degrees(math.atan(t))


def read(path, width: float = 1920.0, height: float = 1080.0) -> CamPath:
    cam = CamPath()
    with open(path, "r", encoding="utf-8", errors="replace") as fh:
        first = fh.readline().split()
        if len(first) < 2 or first[0] != "advancedfx" or first[1] != "Cam":
            raise ValueError(f"{path} is not an advancedfx Cam file")
        channels = HEADER_CHANNELS.split()
        while True:
            line = fh.readline()
            if not line:
                raise ValueError("no DATA section in cam file")
            words = line.split()
            if not words:
                continue
            if words[0] == "DATA":
                break
            if words[0] == "version" and len(words) > 1:
                cam.version = int(words[1])
            elif words[0] == "scaleFov" and len(words) > 1:
                cam.scale_fov = words[1]
            elif words[0] == "channels" and len(words) > 1:
                channels = words[1:]
        idx = {name: i for i, name in enumerate(channels)}

        def col(words, name, default=0.0):
            i = idx.get(name)
            return float(words[i]) if i is not None and i < len(words) else default

        for line in fh:
            words = line.split()
            if len(words) < 8:
                continue
            fov = col(words, "fov")
            if cam.version == 1 and cam.scale_fov == "none":
                fov = _alien_swarm_fov(width, height, fov)
            cam.frames.append(CamFrame(
                time=col(words, "time"),
                x=col(words, "xPosition"), y=col(words, "yPosition"), z=col(words, "zPosition"),
                roll=col(words, "xRotation"),
                pitch=col(words, "yRotation"),
                yaw=col(words, "zRotation"),
                fov=fov or 90.0,
            ))
    return cam


def write(path, frames, version: int = 2) -> Path:
    """frames: iterable of CamFrame (Source space, degrees)."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="\n") as fh:
        fh.write("advancedfx Cam\n")
        fh.write(f"version {version}\n")
        fh.write(f"channels {HEADER_CHANNELS}\n")
        fh.write("DATA\n")
        for f in frames:
            fh.write(
                f"{f.time:.6f} {f.x:.6f} {f.y:.6f} {f.z:.6f} "
                f"{f.roll:.6f} {f.pitch:.6f} {f.yaw:.6f} {f.fov:.6f}\n"
            )
    return path


def from_ue_rows(rows, scale: float = 2.54, version: int = 2):
    """Rows of (time, ue_x, ue_y, ue_z, ue_pitch, ue_yaw, ue_roll, fov) -> CamFrames.

    Inverse of coords.pos_to_ue / coords.rot_to_ue, so a camera animated in Unreal can
    be pushed back into CS2 with `mirv_camio import start <file>`.
    """
    from ..coords import pos_to_source, rot_to_source
    out = []
    for t, x, y, z, p, yw, r, fov in rows:
        sx, sy, sz = pos_to_source(x, y, z, scale)
        sp, syaw, sroll = rot_to_source(p, yw, r)
        out.append(CamFrame(t, sx, sy, sz, sp, syaw, sroll, fov))
    return out
