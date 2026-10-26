"""최소 glTF 2.0 바이너리(GLB) 작성기. 외부 의존성 없이 numpy 배열을 그대로 기록한다."""
from __future__ import annotations

import json
import struct

import numpy as np

FLOAT, UINT16, UINT32 = 5126, 5123, 5125
ARRAY_BUFFER, ELEMENT_ARRAY_BUFFER = 34962, 34963


class GLB:
    def __init__(self):
        self.bin = bytearray()
        self.j = {
            "asset": {"version": "2.0", "generator": "firescan-3d"},
            "scene": 0, "scenes": [{"nodes": []}], "nodes": [], "meshes": [],
            "materials": [], "accessors": [], "bufferViews": [], "buffers": [],
        }
        self._mat_idx: dict[str, int] = {}

    def _view(self, data: bytes, target: int | None = None) -> int:
        while len(self.bin) % 4:
            self.bin.append(0)
        v = {"buffer": 0, "byteOffset": len(self.bin), "byteLength": len(data)}
        if target:
            v["target"] = target
        self.bin.extend(data)
        self.j["bufferViews"].append(v)
        return len(self.j["bufferViews"]) - 1

    def _accessor(self, arr: np.ndarray, kind: str, target: int, minmax=False) -> int:
        if arr.dtype.kind == "f":
            arr, ctype = arr.astype(np.float32), FLOAT
        else:
            ctype = UINT16 if arr.max(initial=0) < 65535 else UINT32
            arr = arr.astype(np.uint16 if ctype == UINT16 else np.uint32)
        acc = {"bufferView": self._view(np.ascontiguousarray(arr).tobytes(), target),
               "componentType": ctype, "count": int(arr.shape[0]), "type": kind}
        if minmax:
            acc["min"] = arr.min(axis=0).tolist()
            acc["max"] = arr.max(axis=0).tolist()
        self.j["accessors"].append(acc)
        return len(self.j["accessors"]) - 1

    def material(self, name: str, color, roughness=0.9, alpha=None, unlit=False, texture: bytes | None = None,
                 double_sided=False, emissive=None) -> int:
        if name in self._mat_idx:
            return self._mat_idx[name]
        pbr = {"baseColorFactor": [*color[:3], alpha if alpha is not None else 1.0],
               "metallicFactor": 0.0, "roughnessFactor": roughness}
        m = {"name": name, "pbrMetallicRoughness": pbr, "doubleSided": double_sided}
        if alpha is not None:
            m["alphaMode"] = "BLEND"
        if emissive:
            m["emissiveFactor"] = list(emissive)
        if unlit:
            m["extensions"] = {"KHR_materials_unlit": {}}
            self.j.setdefault("extensionsUsed", []).append("KHR_materials_unlit")
        if texture is not None:
            img = {"bufferView": self._view(texture), "mimeType": "image/jpeg"}
            self.j.setdefault("images", []).append(img)
            self.j.setdefault("samplers", []).append({"magFilter": 9729, "minFilter": 9987, "wrapS": 33071,
                                                      "wrapT": 33071})
            self.j.setdefault("textures", []).append({"source": len(self.j["images"]) - 1,
                                                      "sampler": len(self.j["samplers"]) - 1})
            pbr["baseColorTexture"] = {"index": len(self.j["textures"]) - 1}
        self.j["materials"].append(m)
        self._mat_idx[name] = len(self.j["materials"]) - 1
        return self._mat_idx[name]

    def mesh(self, name: str, prims: list[tuple[dict, int]], extras: dict | None = None):
        """prims: [({'pos','nrm','idx','uv'?}, material_idx)]"""
        out = []
        for g, mat in prims:
            if g is None or len(g["idx"]) == 0:
                continue
            attrs = {"POSITION": self._accessor(g["pos"], "VEC3", ARRAY_BUFFER, minmax=True),
                     "NORMAL": self._accessor(g["nrm"], "VEC3", ARRAY_BUFFER)}
            if "uv" in g:
                attrs["TEXCOORD_0"] = self._accessor(g["uv"], "VEC2", ARRAY_BUFFER)
            out.append({"attributes": attrs, "indices": self._accessor(g["idx"].reshape(-1), "SCALAR",
                                                                       ELEMENT_ARRAY_BUFFER),
                        "material": mat})
        if not out:
            return
        self.j["meshes"].append({"name": name, "primitives": out})
        node = {"name": name, "mesh": len(self.j["meshes"]) - 1}
        if extras:
            node["extras"] = extras
        self.j["nodes"].append(node)
        self.j["scenes"][0]["nodes"].append(len(self.j["nodes"]) - 1)

    def build(self, extras: dict | None = None) -> bytes:
        if extras:
            self.j["scenes"][0]["extras"] = extras
        if "extensionsUsed" in self.j:
            self.j["extensionsUsed"] = sorted(set(self.j["extensionsUsed"]))
        while len(self.bin) % 4:
            self.bin.append(0)
        self.j["buffers"] = [{"byteLength": len(self.bin)}]
        js = json.dumps(self.j, separators=(",", ":"), ensure_ascii=False).encode()
        js += b" " * ((4 - len(js) % 4) % 4)
        total = 12 + 8 + len(js) + 8 + len(self.bin)
        return b"".join([
            struct.pack("<III", 0x46546C67, 2, total),
            struct.pack("<II", len(js), 0x4E4F534A), js,
            struct.pack("<II", len(self.bin), 0x004E4942), bytes(self.bin),
        ])
