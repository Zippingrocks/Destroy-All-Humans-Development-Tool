"""Build-pinned, read-only DAH1 Xbox material and draw evidence.

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
_INITIAL_RAW_UV_KINDS = frozenset({"actor", "static-world-object", "human-reference-pose", "sky"})


def native_sampler_state(material: NativeMaterial) -> dict[str, object]:
    """Stage-0 sampler after texture bind followed by material setup.

    Retail uses the authored chain, not a generated mip pyramid. The binding's
    +0x24 float is a mip LOD bias, not a texture dimension or opacity field.
    """
    binding = material.binding
    if binding is None:
        return {"evidence_status": "unbound-material", "mip_filter": "none"}
    if len(binding.raw_bytes) < 0x28:
        raise ValueError("texture binding lacks its native mip LOD bias")
    if binding.mip_levels < 1:
        raise ValueError("native sampler requires at least one mip level")
    bias = struct.unpack_from("<f", binding.raw_bytes, 0x24)[0]
    if not np.isfinite(bias):
        raise ValueError("native mip LOD bias is non-finite")
    point_magnification = bool(material.render_flags & 0x100000)
    mip_filter = "linear" if binding.mip_levels > 1 and not point_magnification else "none"
    return {
        "address_u": material.address_u,
        "address_v": material.address_v,
        "min_filter": "linear",
        "mag_filter": "nearest" if point_magnification else "linear",
        "mip_filter": mip_filter,
        "lod_bias": bias,
        "native_mip_levels": binding.mip_levels,
        "base_level": 0,
        "max_level": binding.mip_levels - 1,
        "generate_mipmaps": False,
        "evidence_status": "retail-code-verified",
        "evidence_addresses": ["0xE6692", "0xE66A7", "0xE5CDC", "0xE5C44"],
    }


def native_material_evidence(material: NativeMaterial, kind: str = "terrain") -> dict[str, object]:
    """Describe recovered fields; effective terrain UVs are initial-load state.

    The runtime terrain scale callback may subsequently change the matrix for
    flag 0x800000 materials. Its initial argument is explicitly (1, 1). Unknown
    Actor/sky loading preserves the original matrix; material-animation updates
    happen later. Other kinds must opt into a verified initial-load contract.
    """
    raw = material.raw_bytes
    if len(raw) < 0x80:
        raise ValueError("material lacks the verified native fields")
    matrix = struct.unpack_from("<8f", raw, 0x20)
    if not np.isfinite(matrix).all():
        raise ValueError("material UV transform contains non-finite values")
    shader = struct.unpack_from("<I", raw, 0x84)[0] if len(raw) >= 0x88 else None
    unresolved = []
    initial_raw_uv = kind in _INITIAL_RAW_UV_KINDS
    if kind != "terrain" and not initial_raw_uv:
        unresolved.append("effective non-terrain UV matrix requires its own runtime update path")
    if initial_raw_uv:
        unresolved.append("material animation, script edits and shared-material runtime changes are not simulated")
    if kind == "terrain" and material.render_flags & 0x800000:
        unresolved.append("runtime terrain texture-scale callback changes are not simulated")
    if shader not in (0, 1):
        unresolved.append("pixel shader is outside the verified basic shader 0/1 subset")
    return {
        "raw_uv_transform": list(matrix),
        "effective_uv_transform": list(_IDENTITY_UV) if kind == "terrain" else list(matrix) if initial_raw_uv else None,
        "uv_transform_status": (
            "retail-initial-terrain-load-identity" if kind == "terrain" else
            "retail-initial-material-load-preserved" if initial_raw_uv else "unresolved-runtime-state"
        ),
        "native_sampler": native_sampler_state(material),
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
        "rigid_vertex_color_evidence": None if kind == "terrain" else {
            "scope": "default rigid vertex shader 0x256138; not all actor/skin shader variants",
            "light": "max(dot(v1.xyz, c46.xyz), 0) * c47 + c48",
            "rgb": "((light.rgb * material.diffuse.rgb + material.additive.rgb) * c20.rgb) + c19.rgb",
            "alpha": "material.opacity",
            "uv": "(dot(v2, c78), dot(v2, c79))",
            "status": "shader-dataflow-verified-runtime-light-constants-unresolved",
            "null_tint_multiplier_c20": [1.0, 1.0, 1.0, 1.0],
            "null_tint_additive_c19": [0.0, 0.0, 0.0, 0.0],
            "tint_source": "light/context record +0x14 -> c20; +0x18 -> c19; defaults only when +0x14 is null",
            "pixel_rgb_scale": 2.0 if shader in (0, 1) else None,
            "note": "Do not replace unresolved vertex lighting with white then apply RGB2 as a parity fix.",
        },
        "provenance": {
            "raw_uv_transform": "material+0x20..0x3F -> shader c78/c79 at 0xE5DD0",
            "initial_terrain_uv_transform": "0xE5790 -> 0xE52C0 -> 0xE5A40 with scale=(1,1)",
            "initial_actor_sky_uv_transform": "type 0x9709513E factory 0xE1455 -> 0xE01B0 -> 0xE5AA0 -> 0xE60A0 resolves texture handles only; clone 0xE5F30 copies +0x20 matrix",
            "runtime_uv_animation": "0xCEB65 / 0xCED22 -> 0xE5970 recomputes +0x20 from animated +0x08..+0x18",
            "draw_tint_constants": "0xEB20B..0xEB273 uploads c20/c19; null +0x14 uses 1/0 respectively",
            "terrain_vertex_shader": "0x251E20: DP4(v1,c78/c79); MOV(v2.xyzw,oD0)",
            "pixel_shaders": "0=0x251098; 1=0x250FA8; shader table setup at 0xE5870",
            "rigid_vertex_shader": "0x256138 registered at 0xEEE60 and selected at 0xEF756; c76=(diffuse,0), c77=(additive,opacity) at 0xE5D2A",
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


def actor_material_render_state(
    material: NativeMaterial, *, default_draw_mode: int = 1, draw_opacity: float = 1.0,
) -> dict[str, object]:
    """Native ordinary-actor raster state, not a claim of lighting parity.

    Rigid draw submission passes mode 1 or 4 to 0xEA010. That routine routes
    translucent materials to mode 6; 0xE9F8A independently does so for a faded
    draw record. Unlike terrain, there is no signed segment-layer override.
    Special modes 0/8 deliberately remain non-applicable evidence. Runtime
    shadow, reflection, activation and fade overrides are not inferred here.

    ``evidence_status`` concerns the raster state only. Lighting, pixel shader
    support and effective object UV matrices have separate uncertainty fields.
    This distinction permits the proven additive pass without inventing the
    light constants that feed the actor's vertex color.
    """
    if default_draw_mode not in (1, 4):
        raise ValueError("ordinary actor default_draw_mode must be 1 or 4")
    if len(material.raw_bytes) < 0x88:
        raise ValueError("material lacks the verified native actor fields")
    if not np.isfinite(draw_opacity):
        raise ValueError("draw opacity is non-finite")
    flags = material.render_flags
    opacity = struct.unpack_from("<f", material.raw_bytes, 0x68)[0]
    if not np.isfinite(opacity):
        raise ValueError("material opacity is non-finite")
    if material.binding and len(material.binding.raw_bytes) < 0x14:
        raise ValueError("texture binding lacks the native alpha mode")
    binding_mode = struct.unpack_from("<I", material.binding.raw_bytes, 0x10)[0] if material.binding else None
    shader = struct.unpack_from("<I", material.raw_bytes, 0x84)[0]
    native_blended = bool((binding_mode == 3 and not flags & 0x40000) or flags & 0x700 or opacity < 1.0)
    draw_mode = default_draw_mode
    if flags & 0x80000:
        draw_mode = 8
    elif native_blended:
        if flags & 0x800:
            draw_mode = 0
        elif flags & 0x700 or binding_mode != 2:
            draw_mode = 6
    if draw_mode in (1, 4) and draw_opacity < 1.0:
        draw_mode = 6

    unresolved = []
    if draw_mode not in (1, 4, 6):
        unresolved.append(f"special native draw mode {draw_mode} requires its own pass reconstruction")
    if binding_mode not in (None, 0, 1, 2, 3):
        unresolved.append(f"unknown native texture alpha mode {binding_mode}")
    blend = draw_mode == 6
    if blend and not native_blended:
        unresolved.append("per-draw fade blend factors require draw flags (0x8) or prior GPU state")
    reference = 1 if blend else 64
    if binding_mode == 2:
        # CVTTSS2SI and signed subtraction at 0xE5D65, including the integer
        # indefinite result for an out-of-range conversion (not Python bigint).
        with np.errstate(over="ignore"):
            scaled = float(np.float32(opacity) * np.float32(255))
        converted = int(scaled) if -2147483648 <= scaled < 2147483648 else -2147483648
        value = (256 - converted) & 0xFFFFFFFF
        value = value if value < 0x80000000 else value - 0x100000000
        reference = max(0, min(255, value))
    source, destination, equation = ("src_alpha", "one_minus_src_alpha", "add") if blend else ("one", "zero", "add")
    if native_blended and not flags & 0x80000:
        for mask, operation in ((0x100, "add"), (0x200, "reverse_subtract"), (0x400, "subtract")):
            if flags & mask:
                source, destination, equation = "one", "one", operation
                break
    return {
        "alpha_mode": "blend" if blend else "mask" if binding_mode == 2 else "opaque",
        "alpha_test_enabled": True,
        "alpha_reference_u8": reference,
        "alpha_compare": "greater",
        "blend_source": source,
        "blend_destination": destination,
        "blend_equation": equation,
        "depth_write": not blend,
        "depth_compare": "less_equal",
        "double_sided": bool(flags & 0x4000),
        "native_cull_enabled": not bool(flags & 0x4000),
        "native_cull_winding": "per-draw-orientation" if not flags & 0x4000 else None,
        "native_draw_enabled": not bool(flags & 0x80),
        "native_draw_mode": draw_mode,
        "native_default_draw_mode": default_draw_mode,
        "native_draw_opacity": float(draw_opacity),
        "native_pixel_shader_index": shader,
        "native_texture_alpha_mode": binding_mode,
        "native_opacity": opacity,
        "material_opacity": opacity,
        "evidence_scope": "ordinary-actor-raster-state-only",
        "evidence_status": "unresolved-special-path" if unresolved else "retail-code-verified",
        "evidence_addresses": ["0xEA050", "0xE9F8A", "0xEAB6A", "0xEABE3", "0xE5D65", "0xE6224", "0xEB340", "0xEB429", "0xEAF19", "0xE5C00"],
        "unresolved": unresolved,
        "visual_unresolved": [
            "native actor lighting, tint/fade constants and shader-dependent vertex colors are not reconstructed",
            "material animation and later UV updates are not simulated; initial actor UVs retain the authored matrix",
            "runtime shadow/fog/reflection passes and draw-opacity changes are not simulated",
        ] + ([f"pixel shader {shader} is outside the verified basic 0/1 subset"] if shader not in (0, 1) else []),
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
    "actor_pass_routing": (0xEA050, 127, "192de4b7894f7708a9fb738107bbe0fc6d534f6da2c31993569e4285b020517e"),
    "actor_draw_fade_and_depth": (0xE9F8A, 128, "f7b711061f7cc4e12fa24d5014375e7736534162e54d6ff9cc215ab7f367cba1"),
    "pass_alpha_blend_enable": (0xEAB6A, 179, "74763fd8da8cbe7360308f6d4b03a6358c130a4d7142365737c18e8825338e22"),
    "pass_alpha_blend_jump_table": (0xEBAA4, 40, "2fc75c1db6de0ed0af479f1545acbaeacac7ba7aa1cd36cd5f8b1ea50a65c533"),
    "material_cull": (0xE5C00, 36, "cb2d30d250b916a882f9dba2e0ba42d2263926c8896f6f0b1c5572a1c32ad046"),
    "native_cull_setup": (0xEAF63, 56, "32f79cae71583d43b113694613edd86ad8e5d6926271a24a58d20cab82430bc5"),
    "cull_api": (0x1D8520, 104, "45ccfa8a2acab5b36ed68f50e5838d2552179d51be5dd1ade227a21854bdc71b"),
    "rigid_shader_registration": (0xEEE60, 353, "498f21ab427cb28926ebc867ac75fb3655fee77487610163816bf91a30a0fc7e"),
    "rigid_shader_selection": (0xEF6E3, 145, "fba69545415a12ee4623ac5a5f1cbd221725d230f271280d0a24852852bbae27"),
    "rigid_basic_vertex_shader": (0x256138, 276, "0399d994ea81efb4ab76757df03f4ab8cea7bb48f9f1acf45ef946cca0f7c44f"),
    "rigid_multilight_vertex_shader": (0x256250, 388, "99003529729745193fba92e6500bed78a6b7103c6c32632edd4ec2ba24fe0faa"),
    "material_resource_factory": (0xE1455, 21, "e1c6f0eab7bbf2c86dba3ef5b62f77e43ab3d2232a6571081afa678a1bbece28"),
    "material_wrapper_constructor": (0xE5AA0, 82, "619c40a9813f6126af2291b72e8ee9bf04beb3e15db127dce5807ba585a99852"),
    "material_load_preserves_uv": (0xE60A0, 59, "b0c335fad610baf1f506928082b813932b9e61be21b254a54a2824ffdb9969b6"),
    "material_clone_copies_uv": (0xE5F6E, 47, "259f9b9e3f1d3b0dbae5f7d0eaa04241faf683f3d096ef403e8e144167940435"),
    "material_matrix_copy": (0xD78B0, 8, "b69497b3e817b482455d7c91ce7588f72684ee6611a6f72dcc78b958c2b1462e"),
    "draw_tint_constants": (0xEB20B, 104, "51ce57789fdd3f5892c4c8856db0ee817c53a0a19e253fabadeb0e97ded245dd"),
    "sampler_binding_mips_bias": (0xE6686, 94, "d69da40565189dfeedce4e547d8b53d2dbb749c8d9724b50dfb7e3dda7874ac5"),
    "sampler_material_filters": (0xE5CDC, 78, "025c11bb66558de5264f82ec4f53ec086d6f2c5380e7640c945de20009b0b274"),
    "material_animated_uv": (0xE5970, 194, "bd7fe2583b097db4a1492aeaf9407d5cf31b81a196472dcd9fc0d201a45becaf"),
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
