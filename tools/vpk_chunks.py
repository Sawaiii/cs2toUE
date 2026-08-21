"""Which pak01_NNN.vpk chunks hold the files matching given patterns.

    python tools/vpk_chunks.py <pak01_dir.vpk> <regex> [<regex> ...]

Parses the VPK directory tree by hand (signature 0x55aa1234, v2): every entry names
the archive chunk it lives in - exactly what you need to copy a subset of a 30 GB
archive over a remote-desktop link.
"""

import re
import struct
import sys
from collections import defaultdict


def read_cstr(buf, pos):
    end = buf.index(b"\0", pos)
    return buf[pos:end].decode("utf-8", "replace"), end + 1


def walk(dir_path):
    data = open(dir_path, "rb").read()
    sig, version, tree_size = struct.unpack_from("<IIi", data, 0)
    assert sig == 0x55AA1234, "not a VPK dir"
    pos = 12 + (16 if version == 2 else 0)
    while True:
        ext, pos = read_cstr(data, pos)
        if not ext:
            break
        while True:
            path, pos = read_cstr(data, pos)
            if not path:
                break
            while True:
                name, pos = read_cstr(data, pos)
                if not name:
                    break
                crc, preload, archive, offset, length, term = struct.unpack_from(
                    "<IHHIIH", data, pos)
                pos += 18 + preload
                full = f"{path}/{name}.{ext}" if path != " " else f"{name}.{ext}"
                yield full, archive, length


def main():
    dir_path, patterns = sys.argv[1], [re.compile(p, re.I) for p in sys.argv[2:]]
    by_chunk = defaultdict(lambda: [0, 0])
    files = 0
    for full, archive, length in walk(dir_path):
        if not any(p.search(full) for p in patterns):
            continue
        files += 1
        by_chunk[archive][0] += 1
        by_chunk[archive][1] += length
    total = sum(v[1] for v in by_chunk.values())
    print(f"файлов подходит: {files}, суммарно {total/1e6:.0f} МБ, чанков: {len(by_chunk)}")
    for arc in sorted(by_chunk):
        n, size = by_chunk[arc]
        name = "pak01_dir.vpk (встроено)" if arc == 0x7FFF else f"pak01_{arc:03d}.vpk"
        print(f"  {name:<26} файлов {n:<6} {size/1e6:8.1f} МБ")


if __name__ == "__main__":
    main()
