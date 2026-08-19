"""Dependency-free reader for CS2 (PBDEMS2 / Source 2) and CS:GO (HL2DEMO) demo headers.

Why not just use demoparser2 for this?  Because the very first thing cs2toUE has to do
is decide *which* toolchain (which HLAE build, which parser backend) a demo needs - and
that decision must work before anything is installed.  So this module only uses the
standard library: a tiny protobuf scanner is enough to read CDemoFileHeader /
CDemoFileInfo out of a Source 2 demo.

Reference: https://github.com/SteamDatabase/GameTracking-CS2 -> Protobufs/demo.proto
"""

from __future__ import annotations

import struct
from dataclasses import dataclass, field, asdict
from pathlib import Path

MAGIC_S2 = b"PBDEMS2\0"
MAGIC_S1 = b"HL2DEMO\0"

DEM_FILE_HEADER = 1
DEM_FILE_INFO = 2
DEM_IS_COMPRESSED = 64


# --------------------------------------------------------------------- protobuf

def _read_varint(buf: bytes, pos: int):
    result = 0
    shift = 0
    while True:
        if pos >= len(buf):
            raise ValueError("truncated varint")
        b = buf[pos]
        pos += 1
        result |= (b & 0x7F) << shift
        if not b & 0x80:
            return result, pos
        shift += 7
        if shift > 70:
            raise ValueError("varint too long")


def pb_decode(buf: bytes) -> dict:
    """Decode a protobuf message into {field_number: [values]} without a schema.

    Length-delimited fields are kept as raw bytes; the caller decides whether that is
    a string or a nested message.
    """
    out = {}
    pos = 0
    while pos < len(buf):
        key, pos = _read_varint(buf, pos)
        fnum, wire = key >> 3, key & 7
        if wire == 0:
            val, pos = _read_varint(buf, pos)
        elif wire == 1:
            val = struct.unpack_from("<Q", buf, pos)[0]
            pos += 8
        elif wire == 2:
            ln, pos = _read_varint(buf, pos)
            val = buf[pos:pos + ln]
            pos += ln
        elif wire == 5:
            val = struct.unpack_from("<I", buf, pos)[0]
            pos += 4
        else:
            raise ValueError(f"unsupported protobuf wire type {wire}")
        out.setdefault(fnum, []).append(val)
    return out


def _s(fields: dict, num: int, default: str = "") -> str:
    v = fields.get(num)
    if not v:
        return default
    raw = v[0]
    return raw.decode("utf-8", "replace") if isinstance(raw, (bytes, bytearray)) else str(raw)


def _i(fields: dict, num: int, default: int = 0) -> int:
    v = fields.get(num)
    return int(v[0]) if v else default


def _f32(fields: dict, num: int, default: float = 0.0) -> float:
    v = fields.get(num)
    if not v:
        return default
    return struct.unpack("<f", struct.pack("<I", int(v[0])))[0]


def _packed_varints(raw: bytes) -> list:
    out, pos = [], 0
    while pos < len(raw):
        val, pos = _read_varint(raw, pos)
        out.append(val)
    return out


# --------------------------------------------------------------------- model

@dataclass
class DemoInfo:
    path: str = ""
    size: int = 0
    fmt: str = ""                 # "pbdems2" | "hl2demo"
    engine: str = ""              # "source2" | "source1"
    game: str = ""                # "cs2" | "csgo" | other
    demo_file_stamp: str = ""
    demo_version_name: str = ""   # e.g. "valve_demo_2"
    demo_version_guid: str = ""
    map_name: str = ""
    server_name: str = ""
    client_name: str = ""
    game_directory: str = ""
    build_num: int = 0            # Source 2 build number (CS2)
    patch_version: int = 0
    fullpackets_version: int = 0
    demo_protocol: int = 0        # Source 1 only
    network_protocol: int = 0     # Source 1 only
    playback_time: float = 0.0
    playback_ticks: int = 0
    playback_frames: int = 0
    tickrate: float = 0.0
    round_start_ticks: list = field(default_factory=list)
    notes: list = field(default_factory=list)

    def as_dict(self) -> dict:
        return asdict(self)

    @property
    def label(self) -> str:
        g = self.game or self.engine
        b = f" build {self.build_num}" if self.build_num else ""
        p = f" netproto {self.network_protocol}" if self.network_protocol else ""
        return f"{g}{b}{p}"


# --------------------------------------------------------------------- source 2

def _maybe_decompress(data: bytes, compressed: bool, info: DemoInfo):
    if not compressed:
        return data
    try:
        import snappy  # python-snappy, optional
        return snappy.decompress(data)
    except Exception:
        info.notes.append(
            "packet was snappy-compressed and python-snappy is unavailable; "
            "some header details were skipped"
        )
        return None


def _read_packet(fh):
    """Read one Source 2 demo packet: varint cmd, varint tick, varint size, payload."""
    def rv():
        result, shift = 0, 0
        while True:
            b = fh.read(1)
            if not b:
                return None
            b = b[0]
            result |= (b & 0x7F) << shift
            if not b & 0x80:
                return result
            shift += 7
    cmd = rv()
    if cmd is None:
        return None
    tick = rv()
    size = rv()
    if tick is None or size is None:
        return None
    return cmd, tick, fh.read(size)


def _read_source2(path: Path, info: DemoInfo) -> DemoInfo:
    info.fmt = "pbdems2"
    info.engine = "source2"
    with open(path, "rb") as fh:
        fh.seek(8)
        fileinfo_offset = struct.unpack("<i", fh.read(4))[0]
        fh.read(4)  # spawn groups offset (unused here)

        # --- CDemoFileHeader: always the first packet, never compressed in practice
        for _ in range(4):
            pkt = _read_packet(fh)
            if pkt is None:
                break
            cmd, _tick, data = pkt
            compressed = bool(cmd & DEM_IS_COMPRESSED)
            cmd &= ~DEM_IS_COMPRESSED
            if cmd != DEM_FILE_HEADER:
                continue
            payload = _maybe_decompress(data, compressed, info)
            if payload is None:
                break
            f = pb_decode(payload)
            info.demo_file_stamp = _s(f, 1)
            info.patch_version = _i(f, 2)
            info.server_name = _s(f, 3)
            info.client_name = _s(f, 4)
            info.map_name = _s(f, 5)
            info.game_directory = _s(f, 6)
            info.fullpackets_version = _i(f, 7)
            info.demo_version_name = _s(f, 11)
            info.demo_version_guid = _s(f, 12)
            info.build_num = _i(f, 13)
            info.game = _s(f, 14) or "cs2"
            break

        # --- CDemoFileInfo: playback length + round start ticks, stored at the end
        if 0 < fileinfo_offset < path.stat().st_size:
            try:
                fh.seek(fileinfo_offset)
                pkt = _read_packet(fh)
                if pkt:
                    cmd, _tick, data = pkt
                    compressed = bool(cmd & DEM_IS_COMPRESSED)
                    cmd &= ~DEM_IS_COMPRESSED
                    payload = _maybe_decompress(data, compressed, info)
                    if payload and cmd == DEM_FILE_INFO:
                        f = pb_decode(payload)
                        info.playback_time = _f32(f, 1)
                        info.playback_ticks = _i(f, 2)
                        info.playback_frames = _i(f, 3)
                        gi = f.get(4)
                        if gi:
                            cs = pb_decode(gi[0]).get(5)
                            if cs:
                                cs_fields = pb_decode(cs[0])
                                ticks = []
                                for raw in cs_fields.get(1, []):
                                    if isinstance(raw, (bytes, bytearray)):
                                        ticks += _packed_varints(raw)
                                    else:
                                        ticks.append(int(raw))
                                info.round_start_ticks = ticks
            except Exception as exc:  # a truncated/corrupt tail must not kill inspection
                info.notes.append(f"could not read CDemoFileInfo: {exc}")

    # In CS2 the game directory is still "csgo"; the engine tells the two games apart.
    if info.game in ("csgo", "", "cs2"):
        info.game = "cs2"
    return info


# --------------------------------------------------------------------- source 1

def _read_source1(path: Path, info: DemoInfo) -> DemoInfo:
    info.fmt = "hl2demo"
    info.engine = "source1"
    with open(path, "rb") as fh:
        head = fh.read(1072)
    if len(head) < 1072:
        raise ValueError("HL2DEMO header is truncated")

    def cstr(off: int, ln: int = 260) -> str:
        return head[off:off + ln].split(b"\0", 1)[0].decode("utf-8", "replace")

    info.demo_protocol, info.network_protocol = struct.unpack_from("<ii", head, 8)
    info.server_name = cstr(16)
    info.client_name = cstr(276)
    info.map_name = cstr(536)
    info.game_directory = cstr(796)
    info.playback_time, info.playback_ticks, info.playback_frames = struct.unpack_from(
        "<fii", head, 1056
    )
    info.game = "csgo" if info.game_directory.endswith("csgo") else (info.game_directory or "source1")
    return info


# --------------------------------------------------------------------- public

def read(path) -> DemoInfo:
    path = Path(path)
    if not path.is_file():
        raise FileNotFoundError(path)
    info = DemoInfo(path=str(path), size=path.stat().st_size)
    with open(path, "rb") as fh:
        magic = fh.read(8)
    if magic == MAGIC_S2:
        _read_source2(path, info)
    elif magic == MAGIC_S1:
        _read_source1(path, info)
    else:
        raise ValueError(
            f"unknown demo magic {magic!r} - not a CS2 (PBDEMS2) or CS:GO (HL2DEMO) demo"
        )
    if info.playback_time > 0 and info.playback_ticks > 0:
        info.tickrate = round(info.playback_ticks / info.playback_time, 3)
    if not info.tickrate:
        info.tickrate = 64.0
        info.notes.append("tickrate unknown, assuming 64")
    return info
