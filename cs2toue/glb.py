"""Minimal glTF/GLB reader - just the scene graph.

Unreal's asset import brings meshes in but does not place them: a decompiled map is
2500+ meshes whose positions live in the node hierarchy, so importing and spawning
everything at the origin piles the whole level into one point.

This reads the node tree, composes world transforms, and writes a placement file the
editor script can follow. Only the parts needed for that are implemented - no buffers,
no materials, no animation.
"""

from __future__ import annotations

import json
import math
import struct
from pathlib import Path


def load_json(path) -> dict:
    """The JSON chunk of a .glb, or a plain .gltf file."""
    path = Path(path)
    if path.suffix.lower() == ".gltf":
        return json.loads(path.read_text(encoding="utf-8"))
    with open(path, "rb") as fh:
        magic, _version, _total = struct.unpack("<III", fh.read(12))
        if magic != 0x46546C67:
            raise ValueError(f"not a glb: {path}")
        length, kind = struct.unpack("<II", fh.read(8))
        if kind != 0x4E4F534A:
            raise ValueError("first chunk is not JSON")
        return json.loads(fh.read(length).decode("utf-8"))


# ------------------------------------------------------------------ math
# 4x4 matrices as flat 16-tuples in column-major order, the way glTF stores them.

IDENTITY = (1.0, 0, 0, 0, 0, 1.0, 0, 0, 0, 0, 1.0, 0, 0, 0, 0, 1.0)


def mat_mul(a, b):
    out = [0.0] * 16
    for col in range(4):
        for row in range(4):
            out[col * 4 + row] = sum(a[k * 4 + row] * b[col * 4 + k] for k in range(4))
    return tuple(out)


def trs_to_matrix(translation, rotation, scale):
    tx, ty, tz = translation or (0.0, 0.0, 0.0)
    qx, qy, qz, qw = rotation or (0.0, 0.0, 0.0, 1.0)
    sx, sy, sz = scale or (1.0, 1.0, 1.0)
    xx, yy, zz = qx * qx, qy * qy, qz * qz
    xy, xz, yz = qx * qy, qx * qz, qy * qz
    wx, wy, wz = qw * qx, qw * qy, qw * qz
    return (
        (1 - 2 * (yy + zz)) * sx, (2 * (xy + wz)) * sx, (2 * (xz - wy)) * sx, 0.0,
        (2 * (xy - wz)) * sy, (1 - 2 * (xx + zz)) * sy, (2 * (yz + wx)) * sy, 0.0,
        (2 * (xz + wy)) * sz, (2 * (yz - wx)) * sz, (1 - 2 * (xx + yy)) * sz, 0.0,
        tx, ty, tz, 1.0,
    )


def node_matrix(node) -> tuple:
    if "matrix" in node:
        return tuple(float(v) for v in node["matrix"])
    return trs_to_matrix(node.get("translation"), node.get("rotation"), node.get("scale"))


def decompose(m):
    """(translation, euler degrees, scale) from a column-major 4x4."""
    tx, ty, tz = m[12], m[13], m[14]
    cols = [(m[0], m[1], m[2]), (m[4], m[5], m[6]), (m[8], m[9], m[10])]
    scale = [math.sqrt(sum(c * c for c in col)) or 1.0 for col in cols]
    r = [[cols[c][i] / scale[c] for c in range(3)] for i in range(3)]
    # ZYX euler, matching how most engines read a rotation matrix back
    sy = math.sqrt(r[0][0] ** 2 + r[1][0] ** 2)
    if sy > 1e-6:
        x = math.atan2(r[2][1], r[2][2])
        y = math.atan2(-r[2][0], sy)
        z = math.atan2(r[1][0], r[0][0])
    else:
        x = math.atan2(-r[1][2], r[1][1])
        y = math.atan2(-r[2][0], sy)
        z = 0.0
    return ((tx, ty, tz), tuple(math.degrees(v) for v in (x, y, z)), tuple(scale))


# ------------------------------------------------------------------ scene graph

def placements(path) -> list:
    """[{mesh, node, translation, rotation, scale}] in glTF space, world transforms.

    One entry per node that draws a mesh; instanced meshes appear once per node, which
    is what a level needs - the same crate placed forty times is forty actors.
    """
    js = load_json(path)
    nodes = js.get("nodes", [])
    meshes = js.get("meshes", [])
    out = []

    def walk(index, parent):
        node = nodes[index]
        world = mat_mul(parent, node_matrix(node))
        if "mesh" in node:
            mesh = meshes[node["mesh"]] if node["mesh"] < len(meshes) else {}
            translation, rotation, scale = decompose(world)
            out.append({
                "mesh": mesh.get("name", "") or f"mesh_{node['mesh']}",
                "node": node.get("name", "") or f"node_{index}",
                "translation": [round(v, 4) for v in translation],
                "rotation": [round(v, 4) for v in rotation],
                "scale": [round(v, 5) for v in scale],
            })
        for child in node.get("children", []) or []:
            walk(child, world)

    scenes = js.get("scenes") or [{}]
    roots = scenes[js.get("scene", 0)].get("nodes") if scenes else None
    if not roots:
        roots = range(len(nodes))
    for root in roots:
        walk(root, IDENTITY)
    return out


def write_placement(glb_path, out_path=None) -> Path:
    glb_path = Path(glb_path)
    out_path = Path(out_path or glb_path.with_name("placement.json"))
    items = placements(glb_path)
    out_path.write_text(json.dumps({
        "source": glb_path.name,
        "space": "gltf (Y up, right handed, metres)",
        "count": len(items),
        "items": items,
    }, indent=1), encoding="utf-8")
    return out_path
