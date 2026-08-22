"""Checks for the glTF scene-graph reader.

The map placement depends on composing parent matrices and pulling a rotation back
out of them; both are easy to get subtly wrong and hard to notice in a render.
"""

import math
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from cs2toue import glb


def close(a, b, eps=1e-4):
    return all(abs(x - y) < eps for x, y in zip(a, b))


def test_identity():
    assert glb.mat_mul(glb.IDENTITY, glb.IDENTITY) == glb.IDENTITY
    t, r, s = glb.decompose(glb.IDENTITY)
    assert close(t, (0, 0, 0)) and close(r, (0, 0, 0)) and close(s, (1, 1, 1))


def test_translation_composes():
    a = glb.trs_to_matrix((1, 2, 3), None, None)
    b = glb.trs_to_matrix((10, 20, 30), None, None)
    t, _, _ = glb.decompose(glb.mat_mul(a, b))
    assert close(t, (11, 22, 33)), t


def test_scale_and_translation():
    parent = glb.trs_to_matrix((0, 0, 0), None, (2, 2, 2))
    child = glb.trs_to_matrix((5, 0, 0), None, None)
    t, _, s = glb.decompose(glb.mat_mul(parent, child))
    assert close(t, (10, 0, 0)), t          # parent scale moves the child out
    assert close(s, (2, 2, 2)), s


def test_rotation_roundtrip():
    # 90 degrees about Y as a quaternion
    half = math.radians(90) / 2
    q = (0.0, math.sin(half), 0.0, math.cos(half))
    m = glb.trs_to_matrix((0, 0, 0), q, None)
    _, r, _ = glb.decompose(m)
    assert abs(abs(r[1]) - 90.0) < 0.01, r


def test_real_map():
    p = Path("workspace/maps/de_vertigo/maps/de_vertigo/world.glb")
    if not p.is_file():
        print("  (карта не найдена, пропускаю)")
        return
    items = glb.placements(p)
    assert items, "no placements"
    moved = [i for i in items if i["translation"] != [0.0, 0.0, 0.0]]
    print(f"  реальная карта: {len(items)} размещений, из них со смещением {len(moved)}")
    for i in items:
        assert len(i["translation"]) == 3 and len(i["rotation"]) == 3
        assert all(abs(v) < 1e5 for v in i["translation"]), i


def main():
    for name, fn in sorted(globals().items()):
        if name.startswith("test_"):
            fn()
            print(f"ok  {name}")
    print("all glb checks passed")


if __name__ == "__main__":
    main()
