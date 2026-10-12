"""Build-pinned, read-only DAH1 Xbox material and terrain draw evidence.

This separates package values from the live values established by game code.
In particular, terrain material matrices are reset at load time; reapplying
their original authoring matrices to already compiled terrain UVs is incorrect.
No package bytes are modified here.
"""
from __future__ import annotations

import hashlib
import struct
from pathlib import Path

import numpy as np

from .executable_evidence import DAH1_RETAIL_XBOX_US_SHA256, XbeImage
from .materials import NativeMaterial


_IDENTITY_UV = (1.0, 0.0, 0.0, 0.0, 0.0, 1.0, 0.0, 0.0)


def native_material_evidence(material: NativeMaterial, kind: str = "terrain") -> dict[str, object]:
    """Describe recovered fields; effective terrain UVs are initial-load state.

    The runtime terrain scale callback may subsequently change the matrix for
    flag 0x800000 materials. Its initial argument is explicitly (1, 1). Unknown
    object-specific matrix update paths are not silently assigned that behavior.
    """
    raw = material.raw_bytes
    if len(raw) < 0x80:
        raise ValueError("material lacks the verified native fields")
    matrix = struct.unpack_from("<8f", raw, 0x20)
    if not np.isfinite(matrix).all():
        raise ValueError("material UV transform contains non-finite values")
    shader = struct.unpack_from("<I", raw, 0x84)[0] if len(raw) >= 0x88 else None
    unresolved = []
    if kind != "terrain":
        unresolved.append("effective non-terrain UV matrix requires its own runtime update path")
    if material.render_flags & 0x800000:
        unresolved.append("runtime terrain texture-scale callback changes are not simulated")
    if shader not in (0, 1):
        unresolved.append("pixel shader is outside the verified basic shader 0/1 subset")
    return {
        "raw_uv_transform": list(matrix),
        "effective_uv_transform": list(_IDENTITY_UV) if kind == "terrain" else None,
        "uv_transform_status": (
            "retail-initial-terrain-load-identity" if kind == "terrain" else "unresolved-runtime-state"
        ),
        "native_pixel_shader_index": shader,
        "native_diffuse_color": list(struct.unpack_from("<3f", raw, 0x50)),
        "native_additive_color": list(struct.unpack_from("<3f", raw, 0x5C)),
        "native_opacity": struct.unpack_from("<f", raw, 0x68)[0],
        "basic_pixel_rgb": "clamp(2 * texture.rgb * vertex.rgb, 0, 1)" if shader == 0 else (
            "clamp(2 * vertex.rgb, 0, 1)" if shader == 1 else None
        ),
        "basic_pixel_alpha": "texture.a * vertex.a" if shader == 0 else (
            "vertex.a" if shader == 1 else None
        ),
        "basic_pixel_material_color_multiply": False if shader in (0, 1) else None,
        "basic_pixel_material_opacity_multiply": False if shader in (0, 1) else None,
        "provenance": {
            "raw_uv_transform": "material+0x20..0x3F -> shader c78/c79 at 0xE5DD0",
            "initial_terrain_uv_transform": "0xE5790 -> 0xE52C0 -> 0xE5A40 with scale=(1,1)",
            "terrain_vertex_shader": "0x251E20: DP4(v1,c78/c79); MOV(v2.xyzw,oD0)",
            "pixel_shaders": "0=0x251098; 1=0x250FA8; shader table setup at 0xE5870",
        },
        "unresolved": unresolved,
    }


def terrain_segment_render_state(material: NativeMaterial, raw_flags: int) -> dict[str, object]:
    """Decode segment-dependent state, including native shadow blend equations.

    The low segment byte selects a material; the signed high byte participates
    in the native draw sort key and, when nonzero, forces a blended pass. This
    state is therefore per segment, not solely a material property.
    """
    if not isinstance(raw_flags, int) or not 0 <= raw_flags <= 0xFFFF:
        raise ValueError("terrain raw_flags must be a uint16")
    flags = material.render_flags
    opacity = struct.unpack_from("<f", material.raw_bytes, 0x68)[0]
    if not np.isfinite(opacity):
        raise ValueError("material opacity is non-finite")
    binding_mode = struct.unpack_from("<I", material.binding.raw_bytes, 0x10)[0] if material.binding else None
    layer_byte = raw_flags >> 8
    layer = layer_byte if layer_byte < 128 else layer_byte - 256
    native_blended = bool((binding_mode == 3 and not flags & 0x40000) or flags & 0x700 or opacity < 1.0)
    force_blend = bool(layer_byte or (native_blended and not flags & 0x800))
    mode = "blend" if force_blend else "mask" if binding_mode == 2 else "opaque"
    reference = max(0, min(255, 256 - int(np.float32(opacity) * np.float32(255)))) if binding_mode == 2 else (1 if force_blend else 64)
    source, destination, equation = ("src_alpha", "one_minus_src_alpha", "add") if force_blend else ("one", "zero", "add")
    # Retail 0xEB340 tests these in priority order, before the flag8 branch.
    # Native shadow/detail materials use ONE/ONE reverse-subtraction: dst-src.
    if native_blended and not flags & 0x80000:
        for mask, operation in ((0x100, "add"), (0x200, "reverse_subtract"), (0x400, "subtract")):
            if flags & mask:
                source, destination, equation = "one", "one", operation
                break
    shader = struct.unpack_from("<I", material.raw_bytes, 0x84)[0] if len(material.raw_bytes) >= 0x88 else None
    unresolved = []
    if flags & 0x80800:
        unresolved.append("special pass flags 0x800/0x80000 require further runtime state reconstruction")
    if binding_mode not in (None, 0, 1, 2, 3):
        unresolved.append(f"unknown native texture alpha mode {binding_mode}")
    if shader not in (0, 1):
        unresolved.append(f"pixel shader {shader} is outside the verified basic subset")
    return {
        "terrain_layer": layer,
        "native_segment_flags": raw_flags,
        "alpha_mode": mode,
        "alpha_reference_u8": reference,
        "alpha_compare": "greater",
        "blend_source": source,
        "blend_destination": destination,
        "blend_equation": equation,
        "depth_write": not force_blend,
        "depth_compare": "less_equal",
        "double_sided": bool(flags & 0x4000),
        "vertex_rgb_scale": 2.0 if shader in (0, 1) else 1.0,
        "vertex_alpha": "native",
        "shade_faces": False,
        "native_pixel_shader_index": shader,
        "native_texture_alpha_mode": binding_mode,
        "native_opacity": opacity,
        "evidence_status": "unresolved-special-path" if unresolved else "retail-code-verified",
        "evidence_addresses": ["0xECC10", "0xE5D65", "0xE61C7", "0xE6224", "0xEAF19", "0xEB340", "0x251E20", "0x251098"],
        "unresolved": unresolved,
    }


# Exact instruction/data spans in the pinned retail executable. Hashing every
# span supplements (not replaces) the structural and semantic fixture tests.
_SPANS = {
    "terrain_layer_pass": (0xECC10, 87, "ad27983f10a37dcb0f2c9fd8af69c610664594f0a360058271a3d3e9653448ca"),
    "terrain_layer_sort_key": (0xECB92, 126, "c90ec773de3080d4a15b73cfbc7f3ddace60d3cf3dc937f732f6f6aaf2756750"),
    "blend_equations": (0xEB340, 323, "4371cbdca127c7158a311498695c240cb5279327a20f4e69daddb109d249bb7d"),
    "depth_less_equal": (0xE61C7, 21, "1b715edca7c2b59f06a5d58deb6d14487a15f6b2aada8a3ad299d85dfeeb4ec9"),
    "depth_write": (0xEAF19, 35, "eb1ffce2614a00d5c1dc79b4f4d9549e7c54218dc31d00db63a805f9c7b97578"),
    "terrain_shader": (0x251E20, 164, "6978eb1c96ad5c4cc4adbe72e6bad5df7c2969637d2c18a761c253e075d76776"),
    "textured_pixel_shader": (0x251098, 240, "16370030f3ede0565f3f8b1f58f2564da2c74469fc344200888b3dd9e53a7e81"),
    "untextured_pixel_shader": (0x250FA8, 240, "94c5775954e85329e0ea718c6a2b6693f840820b43c9d3a053e3619fae53bdec"),
    "material_gpu_constants": (0xE5D2A, 181, "abe62e56697ecf5eaf84b0852a62691c67ad7e39b6ca0f21336626bae3a05c48"),
    "terrain_initial_uv_reset": (0xE5790, 36, "ab1fb4f71238008932fc721e33eb519f67bf0249d5300eab3b46716c0ed1ceeb"),
    "terrain_material_walk": (0xE52C0, 213, "d3a32eb5a7d93f60fabafe7faa14b6884d919f272622755bb08b0b60d68e9a4e"),
    "terrain_uv_callback": (0xE5A40, 91, "6821ecb17b7827d4e21aacc2a8b05807cce730da3ec32c11ced0214cb47af7cf"),
    "native_texture_format_table": (0x251A48, 40, "db10eada3c84a4d95fce354fc7b5a27e75e455172861b2bad7bda0afce8230a4"),
    "texture_create": (0xE64F0, 163, "600b386c318aca6fc35964f127417dd516a0190c3965b950b1284dca33c6cb28"),
}


def verify_material_executable_evidence(path: str | Path) -> dict[str, object]:
    data = Path(path).read_bytes()
    if hashlib.sha256(data).hexdigest() != DAH1_RETAIL_XBOX_US_SHA256:
        raise ValueError("not the pinned DAH1 US Xbox retail executable")
    image = XbeImage(data)
    checks = []
    for name, (address, length, expected) in _SPANS.items():
        actual = hashlib.sha256(image.read_virtual(address, length)).hexdigest()
        if actual != expected:
            raise ValueError(f"material executable evidence mismatch: {name}")
        checks.append({"name": name, "virtual_address": f"0x{address:X}", "byte_length": length, "sha256": actual})
    return {"status": "verified", "sha256": DAH1_RETAIL_XBOX_US_SHA256, "checks": checks}
