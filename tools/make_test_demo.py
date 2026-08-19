"""Generate tiny synthetic demo files to exercise the header reader without a real demo.

    python tools/make_test_demo.py workspace/test

Produces test_cs2.dem (PBDEMS2 / Source 2) and test_csgo.dem (HL2DEMO / Source 1).
These are NOT playable demos - they only carry a correct CDemoFileHeader /
CDemoFileInfo (or a correct 1072 byte Source 1 header), which is exactly what
cs2toue inspect reads.
"""

import struct
import sys
from pathlib import Path


def varint(n: int) -> bytes:
    out = bytearray()
    while True:
        b = n & 0x7F
        n >>= 7
        out.append(b | (0x80 if n else 0))
        if not n:
            return bytes(out)


def field_str(num: int, value: str) -> bytes:
    raw = value.encode("utf-8")
    return varint((num << 3) | 2) + varint(len(raw)) + raw


def field_varint(num: int, value: int) -> bytes:
    return varint((num << 3) | 0) + varint(value)


def field_float(num: int, value: float) -> bytes:
    return varint((num << 3) | 5) + struct.pack("<f", value)


def field_msg(num: int, payload: bytes) -> bytes:
    return varint((num << 3) | 2) + varint(len(payload)) + payload


def packet(cmd: int, tick: int, payload: bytes) -> bytes:
    return varint(cmd) + varint(tick) + varint(len(payload)) + payload


def make_cs2(path: Path, map_name="de_dust2", build=14107, ticks=76800, seconds=1200.0,
             rounds=(1024, 12000, 24000, 36000)):
    header = (
        field_str(1, "PBDEMS2\0")
        + field_varint(2, 13992)
        + field_str(3, "cs2toUE synthetic server")
        + field_str(4, "GOTV")
        + field_str(5, map_name)
        + field_str(6, "csgo")
        + field_varint(7, 2)
        + field_str(11, "valve_demo_2")
        + field_str(12, "00000000-0000-0000-0000-000000000000")
        + field_varint(13, build)
        + field_str(14, "csgo")
    )
    cs_info = b"".join(field_varint(1, t) for t in rounds)
    game_info = field_msg(5, cs_info)
    file_info = (
        field_float(1, seconds)
        + field_varint(2, ticks)
        + field_varint(3, int(ticks / 2))
        + field_msg(4, game_info)
    )
    head_pkt = packet(1, 0, header)
    body = b"\x00" * 256                      # filler so offsets are not degenerate
    info_offset = 16 + len(head_pkt) + len(body)
    blob = (b"PBDEMS2\0" + struct.pack("<ii", info_offset, 0)
            + head_pkt + body + packet(2, ticks, file_info))
    path.write_bytes(blob)
    return path


def make_csgo(path: Path, map_name="de_mirage", netproto=13992, ticks=64000,
              seconds=1000.0):
    def pad(text, n=260):
        raw = text.encode("utf-8")[: n - 1]
        return raw + b"\0" * (n - len(raw))

    blob = (b"HL2DEMO\0" + struct.pack("<ii", 4, netproto)
            + pad("cs2toUE synthetic server") + pad("GOTV") + pad(map_name) + pad("csgo")
            + struct.pack("<fiii", seconds, ticks, int(ticks / 2), 0))
    path.write_bytes(blob)
    return path


if __name__ == "__main__":
    out = Path(sys.argv[1] if len(sys.argv) > 1 else "workspace/test")
    out.mkdir(parents=True, exist_ok=True)
    print(make_cs2(out / "test_cs2.dem"))
    print(make_csgo(out / "test_csgo.dem"))
