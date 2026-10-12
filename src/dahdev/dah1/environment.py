"""Read-only, build-specific native DAH1 Xbox environment reconstruction.

Authored fog is not the active gameplay fog. Camera scripts configure endpoints;
retail code interpolates them and composes the fog with a live parent matrix.
Sky visuals are decoded as dependencies, not misrepresented as ordinary scenery.
"""
from __future__ import annotations

import hashlib
import struct
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from .container import BlockReader, LoadedGroup, ResourceRecord
from .executable_evidence import DAH1_RETAIL_XBOX_US_SHA256, XbeImage
from .material_evidence import native_material_evidence
from .spawns import LuaPrototype, _literal, parse_lua4
from .transforms import quaternion_matrix_xyzw
from .world import STATIC_VISUAL_TYPE, StaticVisual, _decode_static_visual

WORLD_TYPE = 0x59CE7BCB
SCENE_NODE_TYPE = 0xE3554C44
FOG_TYPE = 0x6FF6291D
SUN_TYPE = 0x438B6A74
SKY_TYPE = 0x958378DE
LUA_TYPE = 0x42EF1FD7


@dataclass(frozen=True)
class NativeSpan:
    load_group: int
    offset: int
    raw_bytes: bytes
    type_hash: int
    name_hash: int
    pointer_field: int | None
    relocations: tuple[tuple[int, int], ...]
    group_sha256: str

    @property
    def sha256(self) -> str:
        return hashlib.sha256(self.raw_bytes).hexdigest()


@dataclass(frozen=True)
class NativeFog:
    span: NativeSpan
    extent: float
    position: tuple[float, ...]
    quaternion: tuple[float, ...]
    scale: tuple[float, ...]
    color: tuple[float, ...]

    @property
    def authored_matrix(self) -> np.ndarray:
        return fog_matrix(self)


@dataclass(frozen=True)
class NativeSun:
    role: str
    node_span: NativeSpan
    span: NativeSpan
    position: tuple[float, ...]
    quaternion: tuple[float, ...]
    scale: float
    raw_vectors: tuple[tuple[float, ...], ...]
    raw_tail: float


@dataclass(frozen=True)
class NativeSky:
    span: NativeSpan
    raw_vector: tuple[float, ...]
    visuals: tuple[StaticVisual, ...]


@dataclass(frozen=True)
class CameraFogProfile:
    mode: str
    start: float
    end: float
    farplane: float
    provenance: tuple[dict[str, object], ...]


@dataclass(frozen=True)
class EnvironmentScene:
    reader: BlockReader
    world_resource: ResourceRecord
    world_span: NativeSpan
    fog: NativeFog
    suns: tuple[NativeSun, ...]
    sky: NativeSky
    camera_profiles: tuple[CameraFogProfile, ...]
    scripts: tuple[dict[str, object], ...]
    unresolved: tuple[str, ...]


def _span(group: LoadedGroup, offset: int, size: int, expected_type: int,
          pointer_field: int | None = None, name: int | None = None) -> NativeSpan:
    if offset < 0 or offset % 4 or size < 8 or offset + size > min(group.resource_limit, len(group.data)):
        raise ValueError("environment payload is outside native resource data")
    raw = group.data[offset:offset + size]
    type_hash, name_hash = struct.unpack_from("<II", raw)
    if type_hash != expected_type or (name is not None and name_hash != name):
        raise ValueError("environment payload identity mismatch")
    pointers = tuple(sorted((field - offset, target) for field, target in
                            group.relocations.pointers.items() if offset <= field < offset + size))
    return NativeSpan(group.index, offset, raw, type_hash, name_hash, pointer_field,
                      pointers, group.decoded_sha256)


def _target(group: LoadedGroup, field: int) -> int:
    target = group.relocations.pointers.get(field)
    if target is None:
        raise ValueError(f"environment field 0x{field:X} lacks a relocation")
    if target % 4 or not 0 <= target < group.resource_limit:
        raise ValueError("environment relocation target is invalid")
    return target


def _floats(raw: bytes, offset: int, count: int) -> tuple[float, ...]:
    values = struct.unpack_from(f"<{count}f", raw, offset)
    if not np.isfinite(values).all():
        raise ValueError("environment field contains non-finite values")
    return values


def _decode_fog(span: NativeSpan) -> NativeFog:
    raw = span.raw_bytes
    if len(raw) != 0xEC:
        raise ValueError("fog payload is not the verified 236-byte layout")
    extent = _floats(raw, 0xB4, 1)[0]
    scale = _floats(raw, 0xD4, 3)
    quaternion = _floats(raw, 0xC4, 4)
    color = _floats(raw, 0xE0, 3)
    if extent <= 0 or any(x == 0 for x in scale) or not any(quaternion):
        raise ValueError("fog transform is degenerate")
    if any(not 0 <= x <= 1 for x in color):
        raise ValueError("fog color is outside the verified normalized RGB subset")
    return NativeFog(span, extent, _floats(raw, 0xB8, 3), quaternion, scale, color)


def _matrix(matrix: np.ndarray) -> np.ndarray:
    result = np.asarray(matrix, dtype=np.float32)
    if result.shape != (4, 4) or not np.isfinite(result).all():
        raise ValueError("expected a finite 4x4 row-vector matrix")
    if not np.allclose(result[:, 3], [0, 0, 0, 1], atol=1e-6, rtol=0):
        raise ValueError("expected an affine row-vector matrix")
    return result


def fog_matrix(fog: NativeFog, parent_matrix: np.ndarray | None = None,
               profile: CameraFogProfile | None = None) -> np.ndarray:
    """Retail 0xF3710; optional profile is an endpoint, not live interpolation.

    Native 0x7C0B6 overwrites position and extent using interpolated camera state.
    Without an explicit live parent this returns a local, not camera/world plane.
    """
    extent, position = fog.extent, fog.position
    if profile is not None:
        if not np.isfinite((profile.start, profile.end)).all() or profile.end <= profile.start:
            raise ValueError("camera fog range is degenerate")
        extent, position = profile.end - profile.start, (0.0, -profile.end, 0.0)
    matrix = quaternion_matrix_xyzw(np.asarray(fog.quaternion, dtype=np.float32))
    matrix[0, :3] *= np.float32(fog.scale[0])
    matrix[1, :3] *= np.float32(fog.scale[1])
    matrix[2, :3] *= np.float32(fog.scale[2]) * np.float32(extent)
    matrix[3, :3] = position
    if parent_matrix is not None:
        matrix = matrix @ _matrix(parent_matrix)
    return _matrix(matrix)


def fog_plane(fog_transform: np.ndarray, object_matrix: np.ndarray | None = None) -> np.ndarray:
    """Reproduce 0xF55E0 shader c56: n=z/dot(z,z), d=-dot(origin,n).

    The render-object inverse at 0xD69B0 is rigid, not a general matrix inverse.
    Reject scaled/sheared objects rather than silently claiming runtime parity.
    oFog=DP4(local_position,c56); verified linear hardware setup preserves oFog.
    """
    matrix = _matrix(fog_transform)
    if object_matrix is not None:
        obj = _matrix(object_matrix)
        if not np.allclose(obj[:3, :3] @ obj[:3, :3].T, np.eye(3), rtol=0, atol=2e-5):
            raise ValueError("native fog object inverse requires a rigid matrix")
        inverse = np.eye(4, dtype=np.float32)
        inverse[:3, :3] = obj[:3, :3].T
        inverse[3, :3] = -obj[3, :3] @ inverse[:3, :3]
        matrix = matrix @ inverse
    z = matrix[2, :3]
    squared_length = np.dot(z, z)
    if not np.isfinite(squared_length) or squared_length <= 0:
        raise ValueError("fog plane has degenerate extent")
    normal = z / squared_length
    return np.asarray((*normal, -np.dot(matrix[3, :3], normal)), dtype=np.float32)


def _string(proto: LuaPrototype, instruction: int, opcode: int) -> str:
    if instruction & 63 != opcode or instruction >> 6 >= len(proto.strings):
        raise ValueError("camera Lua does not match the verified straight-line instruction shape")
    result = proto.strings[instruction >> 6]
    if result is None:
        raise ValueError("camera Lua contains a null string")
    return result


def _local_assignments(proto: LuaPrototype) -> dict[str, tuple[float, int]]:
    """Decode actual cfgCamera table assignments; do not execute arbitrary Lua."""
    code, result, pc = proto.instructions, {}, 0
    while pc + 4 <= len(code) and code[pc] & 63 == 12:
        if _string(proto, code[pc], 12) != "cfgCamera":
            break
        key = _string(proto, code[pc + 1], 7)
        value = _literal(proto, code[pc + 2])
        if isinstance(value, (str, bool)) or not np.isfinite(value) or code[pc + 3] != (1539 << 6 | 20):
            raise ValueError("unsupported cfgCamera assignment")
        if key in result:
            raise ValueError("duplicate cfgCamera assignment")
        result[key] = (float(value), pc)
        pc += 4
    tail = code[pc:]
    if len(tail) != 4 or _string(proto, tail[0], 12) != "dprint" or tail[1] & 63 != 7 or tail[2:] != (2, 0):
        raise ValueError("camera local settings contain unverified control flow")
    return result


def _camera_mapping(proto: LuaPrototype) -> tuple[LuaPrototype, dict[str, tuple[str, int]]]:
    # Prove which child is installed as siteCamera.Init before interpreting it.
    if len(proto.instructions) != 15 or proto.instructions[-1] != 0 or any(
            word & 63 not in (0, 7, 12, 17, 19, 20, 48) for word in proto.instructions):
        raise ValueError("siteCamera definitions contain unverified control flow")
    candidates = []
    for pc in range(len(proto.instructions) - 3):
        code = proto.instructions[pc:pc + 4]
        if code[0] & 63 == 12 and _string(proto, code[0], 12) == "siteCamera" and code[1] & 63 == 7 and _string(proto, code[1], 7) == "Init":
            if code[2] & 63 != 48 or code[2] >> 6 & 511 or code[3] != (1539 << 6 | 20):
                raise ValueError("siteCamera.Init is not a direct zero-upvalue closure")
            child = code[2] >> 15
            if child >= len(proto.children):
                raise ValueError("invalid siteCamera.Init child")
            candidates.append(proto.children[child])
    if len(candidates) != 1:
        raise ValueError("expected one direct siteCamera.Init definition")
    init = candidates[0]
    code, result = init.instructions, {}
    if len(code) != 61 or code[-1] != 0:
        raise ValueError("unsupported siteCamera.Init control flow")
    for pc in range(0, 60, 6):
        root = _string(init, code[pc], 12)
        member = _string(init, code[pc + 1], 14)
        key = _string(init, code[pc + 2], 7)
        if _string(init, code[pc + 3], 12) != "cfgCamera" or code[pc + 5] != 2:
            raise ValueError("unsupported camera config call")
        config = _string(init, code[pc + 4], 14)
        expected = ("player", "SetParam") if pc < 36 else ("siteCamera", "SetLightParam")
        if (root, member) != expected or key in result:
            raise ValueError("unsupported or duplicate camera config call")
        result[key] = (config, pc)
    return init, result


def _script_report(resource: ResourceRecord, raw: bytes, proto: LuaPrototype) -> dict[str, object]:
    return {"resource_index": resource.index, "name_hash": f"0x{resource.name_hash:08X}",
            "load_group": resource.load_group, "offset": resource.offset, "size": resource.size,
            "sha256": hashlib.sha256(raw).hexdigest(), "source": proto.source}


def _camera_profiles(reader: BlockReader) -> tuple[tuple[CameraFogProfile, ...], tuple[dict[str, object], ...]]:
    # Resource identities select these two known Area42 scripts; values still
    # come from structural bytecode interpretation, never fixture constants.
    scripts = []
    for name in (3656195570, 269095971):
        resource = reader.resource_by_identity(LUA_TYPE, name)
        if resource is None:
            raise ValueError("missing verified Area42 camera script")
        group = reader.load_group(resource.load_group)
        if resource.offset + resource.size > group.resource_limit:
            raise ValueError("camera script is outside native resource data")
        raw = group.data[resource.offset:resource.offset + resource.size]
        scripts.append((resource, raw, parse_lua4(raw)))
    local, shared = scripts
    assignments = _local_assignments(local[2])
    init, mapping = _camera_mapping(shared[2])
    profiles = []
    for mode, prefix in (("alien", "cr"), ("ufo", "ship")):
        values, provenance = [], []
        for suffix in ("FogStart", "FogEnd", "CamFarDist"):
            key = prefix + suffix
            if key not in mapping or mapping[key][0] not in assignments:
                raise ValueError("camera profile is not completely resolved from local settings")
            config, call_pc = mapping[key]
            value, assignment_pc = assignments[config]
            values.append(value)
            provenance.append({"player_parameter": key, "config_key": config,
                               "assignment_pc": assignment_pc, "assignment_code_offset": local[2].code_offset + assignment_pc * 4,
                               "assignment_bytes_hex": local[1][local[2].code_offset + assignment_pc * 4:local[2].code_offset + (assignment_pc + 4) * 4].hex(),
                               "init_call_pc": call_pc, "init_call_code_offset": init.code_offset + call_pc * 4,
                               "init_call_bytes_hex": shared[1][init.code_offset + call_pc * 4:init.code_offset + (call_pc + 6) * 4].hex()})
        if values[1] <= values[0] or values[2] <= 0:
            raise ValueError("camera profile has an invalid fog range or farplane")
        profiles.append(CameraFogProfile(mode, *values, tuple(provenance)))
    return tuple(profiles), tuple(_script_report(*script) for script in scripts)


def read_environment_scene(source: str | Path | BlockReader) -> EnvironmentScene:
    reader = source if isinstance(source, BlockReader) else BlockReader(source)
    worlds = reader.resources_of_type(WORLD_TYPE)
    if len(worlds) != 1:
        raise ValueError("expected one native world resource")
    world = worlds[0]
    if world.size != 56:
        raise ValueError("unsupported native world layout")
    group = reader.load_group(world.load_group)
    world_span = _span(group, world.offset, world.size, WORLD_TYPE, name=world.name_hash)
    fog_field, sky_field = world.offset + 0x18, world.offset + 0x1C
    fog = _decode_fog(_span(group, _target(group, fog_field), 0xEC, FOG_TYPE, fog_field, FOG_TYPE))
    sky_span = _span(group, _target(group, sky_field), 0xC8, SKY_TYPE, sky_field, SKY_TYPE)
    mesh_cache, visuals = {}, []
    for relative in (0xB4, 0xB8):
        location = _target(group, sky_span.offset + relative)
        resource = reader.resource_by_location(group.index, location)
        if resource is None or resource.type_hash != STATIC_VISUAL_TYPE:
            raise ValueError("sky references a missing or unsupported native visual")
        visuals.append(_decode_static_visual(reader, resource, mesh_cache))
    sky = NativeSky(sky_span, _floats(sky_span.raw_bytes, 0xBC, 3), tuple(visuals))
    suns = []
    unresolved = []
    for role, relative in (("shadow", 0x0C), ("static", 0x10), ("dynamic", 0x14)):
        field = world.offset + relative
        node = _span(group, _target(group, field), 64, SCENE_NODE_TYPE, field)
        payload = _span(group, _target(group, node.offset + 0x14), 0xDC, SUN_TYPE, node.offset + 0x14, node.name_hash)
        if node.offset + 8 in group.relocations.pointers:
            unresolved.append(f"{role} sun scene parent is preserved but not composed")
        suns.append(NativeSun(role, node, payload, _floats(node.raw_bytes, 0x1C, 3),
                              _floats(node.raw_bytes, 0x28, 4), _floats(node.raw_bytes, 0x38, 1)[0],
                              tuple(_floats(payload.raw_bytes, offset, 3) for offset in (0xB4, 0xC0, 0xCC)),
                              _floats(payload.raw_bytes, 0xD8, 1)[0]))
    profiles, scripts = _camera_profiles(reader)
    unresolved.extend((
        "camera profiles are configured endpoints; active mode, transition interpolation and cinematic overrides are not simulated",
        "World.Update supplies graphics-global+0xEC as fog parent; live parent selection and preview-camera correspondence remain unverified",
        "sky draw has a translation-free first visual and a separate camera-scaled/query-controlled second visual; exact placement/compositing remains unimplemented",
        "sky material UV runtime state and material-to-vertex lighting path are not yet verified; terrain identity-UV and vertex-RGB assumptions must not be applied",
        "sun vectors are loader-verified fields, not yet verified diffuse/ambient lighting, shadow or light-level semantics",
        "no native save, collision or gameplay activation parity is established by this read-only reconstruction",
    ))
    return EnvironmentScene(reader, world, world_span, fog, tuple(suns), sky, profiles, scripts, tuple(unresolved))


def _span_report(span: NativeSpan) -> dict[str, object]:
    return {"type_hash": f"0x{span.type_hash:08X}", "name_hash": f"0x{span.name_hash:08X}",
            "load_group": span.load_group, "offset": span.offset, "size": len(span.raw_bytes),
            "pointer_field": span.pointer_field, "sha256": span.sha256,
            "group_sha256": span.group_sha256, "raw_bytes_hex": span.raw_bytes.hex(),
            "relocations": [{"relative_field": field, "target": target} for field, target in span.relocations]}


def environment_report(scene: EnvironmentScene) -> dict[str, object]:
    """Serializable native evidence; deliberately does not select a live fog."""
    sky_visuals = []
    for visual in scene.sky.visuals:
        sky_visuals.append({"type_hash": f"0x{visual.resource.type_hash:08X}",
            "name_hash": f"0x{visual.resource.name_hash:08X}", "load_group": visual.resource.load_group,
            "offset": visual.resource.offset, "mesh_name_hash": f"0x{visual.mesh.resource.name_hash:08X}",
            "vertex_count": len(visual.mesh.local_positions), "triangle_count": len(visual.mesh.triangles),
            "segment_count": len(visual.mesh.segments),
            "materials": [{"name_hash": f"0x{material.resource.name_hash:08X}",
                           "render_flags": material.render_flags,
                           "texture_name_hash": f"0x{material.binding.texture_hash:08X}" if material.binding else None,
                           **native_material_evidence(material, "sky")} for material in visual.materials]})
    return {"status": "decoded-native-evidence-not-live-environment", "world": _span_report(scene.world_span),
        "fog": {"native": _span_report(scene.fog.span), "extent": scene.fog.extent,
                "position": list(scene.fog.position), "quaternion_xyzw": list(scene.fog.quaternion),
                "scale": list(scene.fog.scale), "color_rgb": list(scene.fog.color),
                "authored_local_matrix": scene.fog.authored_matrix.tolist(),
                "local_plane": fog_plane(scene.fog.authored_matrix).tolist(),
                "plane_status": "local authored data, not an active world-space camera plane",
                "equation": "oFog=dot(local_position_xyzw,c56); c56=(z/dot(z,z),-dot(origin,z/dot(z,z)))",
                "distance_basis": "transformed plane coordinate, not Euclidean camera distance",
                "provenance": {"fields": "0xF3630: payload+B4..E8", "matrix": "0xF3710", "plane": "0xF55E0", "linear_mode": "0x1E2ACF mode 0 -> NV_LINEAR 0x2601; parameters (1,1,0)"}},
        "suns": [{"role": sun.role, "node": _span_report(sun.node_span), "native": _span_report(sun.span),
                  "position": list(sun.position), "quaternion_xyzw": list(sun.quaternion), "scale": sun.scale,
                  "raw_vectors_b4_c0_cc": [list(vector) for vector in sun.raw_vectors], "raw_tail_d8": sun.raw_tail,
                  "field_status": "0xE4DB0 loader verified; lighting interpretation unresolved"} for sun in scene.suns],
        "sky": {"native": _span_report(scene.sky.span), "raw_vector_bc": list(scene.sky.raw_vector),
                "visuals": sky_visuals, "draw_provenance": "world+1C -> 0xF687C -> 0xE7150; first visual+1C, second+2C; loader 0xF38C0",
                "render_status": "dependencies decoded; special sky runtime transforms and shader path unresolved"},
        "camera_profiles": [{"mode": profile.mode, "start": profile.start, "end": profile.end,
                             "farplane": profile.farplane, "provenance": list(profile.provenance),
                             "status": "native Lua configured endpoint, not active interpolated state"} for profile in scene.camera_profiles],
        "camera_scripts": list(scene.scripts),
        "camera_runtime_evidence": {"setters": ["0x828C5", "0x82AF8", "0x82972", "0x831E2"],
            "interpolation_input": "0x7BCBC camera config+58/+5C -> interpolators+13C/+154",
            "fog_update": "0x7C08F..0x7C0DC current end+15C/start+144 -> fog position(0,-end,0), extent=end-start",
            "parent_update": "0xE5120 -> fog vtable+1C with graphics-global+EC"},
        "unresolved": list(scene.unresolved)}


_SPANS = {
    "world_environment_refs": (0xE5752, 36, "98c5a5b1e6368fe8935e8b99404605cceea97e332894701e8b2deaaa176b2abc"),
    "fog_loader": (0xF3630, 220, "1261564633c84b1320cba1e7d7bb163f471a94c638fa6cd1ac21fe0b04de7045"),
    "light_loader": (0xE4DB0, 160, "d0cc1e58c6c20acb630e7fb0cd1f926dc36945ae958a7d1e6435c4a877274e6d"),
    "fog_color_script": (0xE3CB0, 80, "1ca2d79b0fbf7c42e652cb68c579152375d0a1533bf0a9e6c71bc10a55c1b7ac"),
    "fog_matrix": (0xF3710, 240, "c3408be9b72303f5caaa33466aba6bbc832928bbcaf0c4b7771d20dd4762afa5"),
    "fog_plane": (0xF55E0, 288, "dfe0935d7f14f4ee2246ebc842dc971a192b3d5ff92e63f9c2af40873389657d"),
    "squared_length": (0xD4EA0, 31, "7ab81ef75aaeb8fdc804bd92be5aff0fe4b42e0a6f4ca6c8a8b3028fa8072a38"),
    "upload_c56": (0xEB1C9, 66, "972dff6283897fe62a2abd4aaf08de85eaaf95ff66a81b809ae2218494a53350"),
    "linear_fog_mode": (0x1E2ACF, 215, "427489ad31486a8284fc1c3edcef41f97dc29fb3d74dfde5899ecad27162f215"),
    "sky_loader": (0xF38C0, 82, "f494c062bf5b530919890e54afb036231562ff830aa4b77e0bb974115dfb4e80"),
    "sky_draw": (0xE7150, 1724, "392939c5bcc47a72261132779bfd011d786c8c04d24ddf9741aeffa04af36d61"),
    "camera_fog_start_alien": (0x828C5, 33, "0466718fa83ed2630d5c0d95067b403773e7163360fb8c380651f95248d973f5"),
    "camera_fog_start_ufo": (0x82972, 33, "e0248c765ea2242f6d3ce5c2f627d491ad6d3ca983f0aeceeb34640db8f15844"),
    "camera_fog_end_alien": (0x82AF8, 33, "1bcd390a7d9abc8315a5ad909ee8850157c7f4bb46567843d558732ab4856a78"),
    "camera_fog_end_ufo": (0x831E2, 33, "2428cb127614e03f04fc2d4bb07e56f887d5bafca1c2fccc1dce5057767ff67b"),
    "camera_interpolation_input": (0x7BCBC, 54, "a913ca2a1f76c0812aecef88e059bad3ff6dd4b1f1c352127005169fcbfbf5c0"),
    "camera_fog_update": (0x7C08F, 77, "e4e935e718d129bdb51136be4eac8ea27dcb46237fc878f2f5c263b601ec4c3e"),
    "world_fog_update": (0xE5120, 40, "755ffc046d714a90563c92b46b7de39ad513bdbb203b89938930640440dd6961"),
}


def verify_environment_executable_evidence(path: str | Path) -> dict[str, object]:
    data = Path(path).read_bytes()
    if hashlib.sha256(data).hexdigest() != DAH1_RETAIL_XBOX_US_SHA256:
        raise ValueError("not the pinned DAH1 US Xbox retail executable")
    image, checks = XbeImage(data), []
    for name, (address, length, expected) in _SPANS.items():
        actual = hashlib.sha256(image.read_virtual(address, length)).hexdigest()
        if actual != expected:
            raise ValueError(f"environment executable evidence mismatch: {name}")
        checks.append({"name": name, "virtual_address": f"0x{address:X}", "byte_length": length, "sha256": actual})
    return {"status": "verified", "sha256": DAH1_RETAIL_XBOX_US_SHA256, "checks": checks}
