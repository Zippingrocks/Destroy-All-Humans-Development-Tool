"""Cross-reference validation for complete (v4+) read-only editor scenes."""
from __future__ import annotations

import hashlib
import math
import struct
from collections import Counter, defaultdict

import numpy as np


def _object(value, label):
    if not isinstance(value, dict):
        raise ValueError(f"{label} must be an object")
    return value


def _array(value, label):
    if not isinstance(value, list):
        raise ValueError(f"{label} must be an array")
    return value


def _integer(value, label):
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError(f"{label} must be a nonnegative integer")
    return value


def _vector(value, length, label):
    values = _array(value, label)
    if len(values) != length or any(isinstance(v, bool) or not isinstance(v, (int, float))
                                    or not math.isfinite(v) for v in values):
        raise ValueError(f"{label} must contain {length} finite numbers")
    return values


def _identity(resource):
    resource = _object(resource, "resource identity")
    try:
        return (int(resource["type_hash"], 16), int(resource["name_hash"], 16),
                _integer(resource["load_group"], "resource load group"),
                _integer(resource["offset"], "resource offset"))
    except (KeyError, TypeError) as exc:
        raise ValueError("incomplete resource identity") from exc


def _indexed(values, label):
    result = {}
    for item in _array(values, label):
        item = _object(item, label + " entry")
        key = item.get("id")
        if not isinstance(key, str) or not key or key in result:
            raise ValueError(f"{label} has missing or duplicate id")
        result[key] = item
    return result


def _hex_bytes(value, label, length=None):
    try:
        raw = bytes.fromhex(value)
    except (ValueError, TypeError) as exc:
        raise ValueError(f"{label} is not hexadecimal bytes") from exc
    if length is not None and len(raw) != length:
        raise ValueError(f"{label} has the wrong byte length")
    return raw


def validate_complete_scene(manifest: dict, payload: bytes) -> None:
    """Reject incomplete native list coverage and invalid component references.

    Generic scenes are validated structurally. The named Area42 US Xbox retail
    profile additionally has independently decoded inventory/count gates.
    This validates preserved evidence and buffer structure, not runtime state.
    """
    _object(manifest, "manifest")
    version = _integer(_object(manifest.get("schema"), "schema").get("version"), "schema version")
    if version < 4:
        raise ValueError("complete scene validation requires schema version 4 or newer")
    counts = _object(manifest.get("counts"), "counts")
    objects = _indexed(manifest.get("object_placements"), "object placements")
    landings = _indexed(manifest.get("landing_placements"), "landing placements")
    geometries = _indexed(manifest.get("geometries"), "geometries")
    materials = _indexed(manifest.get("materials"), "materials")
    for key, actual in (("world_placements", len(objects)), ("landing_visual_placements", len(landings))):
        if _integer(counts.get(key), key) != actual:
            raise ValueError(f"complete scene {key} count mismatch")

    checked_geometry = set()
    def geometry_check(geometry_id):
        if geometry_id not in geometries:
            raise ValueError("visual part references unknown geometry")
        geometry = geometries[geometry_id]
        if geometry_id in checked_geometry:
            return geometry
        views = _object(geometry.get("buffers"), "geometry buffers")
        blobs = {}
        for semantic, view in views.items():
            view = _object(view, "geometry buffer view")
            offset = _integer(view.get("offset"), "buffer offset")
            length = _integer(view.get("length"), "buffer length")
            if offset + length > len(payload):
                raise ValueError("visual geometry buffer is out of range")
            raw = payload[offset:offset + length]
            if hashlib.sha256(raw).hexdigest() != view.get("sha256"):
                raise ValueError("visual geometry buffer SHA-256 mismatch")
            blobs[semantic] = raw
        gc = _object(geometry.get("counts"), "geometry counts")
        vc = _integer(gc.get("vertices"), "vertex count")
        tc = _integer(gc.get("triangles"), "triangle count")
        layout = _object(geometry.get("vertex_layout"), "vertex layout")
        stride = _integer(layout.get("stride"), "vertex stride")
        if stride < 12 or len(blobs.get("vertices", b"")) != vc * stride:
            raise ValueError("visual geometry vertex buffer size mismatch")
        for semantic, dimensions in (("position", 3), ("uv", 2)):
            channel = _object(layout.get(semantic), "vertex " + semantic)
            offset = _integer(channel.get("offset"), "vertex channel offset")
            if offset + dimensions * 4 > stride:
                raise ValueError("visual geometry vertex channel is outside stride")
            values = np.ndarray((vc, dimensions), dtype="<f4", buffer=blobs["vertices"],
                                offset=offset, strides=(stride, 4)) if vc else np.empty((0, dimensions))
            if not np.isfinite(values).all():
                raise ValueError("visual geometry has non-finite vertex data")
        triangles = blobs.get("triangles", b"")
        if len(triangles) != tc * 12:
            raise ValueError("visual geometry triangle buffer size mismatch")
        indices = np.frombuffer(triangles, dtype="<u4")
        if len(indices) and int(indices.max()) >= vc:
            raise ValueError("visual geometry triangle index is out of range")
        checked_geometry.add(geometry_id)
        return geometry

    def lod_check(placement):
        lods = _array(placement.get("lods"), "placement LODs")
        seen_lods = set()
        for lod in lods:
            lod = _object(lod, "LOD")
            index = _integer(lod.get("index"), "LOD index")
            if index in seen_lods:
                raise ValueError("placement has duplicate LOD index")
            seen_lods.add(index)
            parts = lod.get("parts")
            if parts is not None:
                parts = _array(parts, "LOD parts")
                if not parts:
                    raise ValueError("LOD component array is empty")
                if (lod.get("geometry") != parts[0].get("geometry")
                        or lod.get("material_slots") != parts[0].get("material_slots")):
                    raise ValueError("LOD representative disagrees with its first component")
            else:
                parts = [lod]
            for part in parts:
                part = _object(part, "visual part")
                geometry = geometry_check(part.get("geometry"))
                slots = _array(part.get("material_slots"), "part material slots")
                if any(not isinstance(slot, str) or slot not in materials for slot in slots):
                    raise ValueError("visual part references unknown material")
                for segment in _array(geometry.get("segments"), "geometry segments"):
                    slot = _integer(_object(segment, "segment").get("material_slot"), "segment material slot")
                    if slot >= len(slots):
                        raise ValueError("visual part segment material slot is out of range")
                if "parts" in lod:
                    _vector(part.get("matrix_row_major_f32"), 16, "component matrix")
                    bone = part.get("bone_index")
                    raw = _hex_bytes(part.get("raw_table_record_hex"), "component native table record")
                    if bone is not None:
                        _integer(bone, "component bone index")
                        _integer(part.get("table_offset"), "component native table offset")
                        if len(raw) != 8 or struct.unpack_from("<I", raw)[0] != bone:
                            raise ValueError("component bone differs from native table record")
                    elif raw or part.get("table_offset") is not None:
                        raise ValueError("unattached static component carries a native attachment record")
        return bool(lods)

    records = set()
    list_members = defaultdict(list)
    list_fields = {}
    bucket_members = defaultdict(list)
    bucket_lists = defaultdict(set)
    actor_members = defaultdict(list)
    renderable = 0
    for placement in objects.values():
        native = _object(placement.get("native"), "world placement native provenance")
        offset = _integer(native.get("record_offset"), "native record offset")
        if offset in records:
            raise ValueError("duplicate native world record offset")
        records.add(offset)
        bucket = _integer(native.get("bucket_index"), "native bucket index")
        list_offset = _integer(native.get("list_offset"), "native list offset")
        field = _integer(native.get("list_pointer_field"), "native list pointer field")
        if field not in (0xC, 0x10):
            raise ValueError("unverified native list pointer field")
        list_key = (bucket, list_offset)
        if list_key in list_fields and list_fields[list_key] != field:
            raise ValueError("native list has inconsistent pointer provenance")
        list_fields[list_key] = field
        list_members[list_key].append(_integer(native.get("list_index"), "native list index"))
        bucket_members[bucket].append(_integer(native.get("record_index"), "bucket record index"))
        bucket_lists[bucket].add(list_offset)
        raw = _hex_bytes(native.get("raw_header_hex"), "native placement header", 0x40)
        if hashlib.sha256(raw).hexdigest() != native.get("raw_header_sha256"):
            raise ValueError("native placement header SHA-256 mismatch")
        position = _vector(placement.get("position_f32"), 3, "world position")
        matrix = _vector(placement.get("matrix_row_major_f32"), 16, "world matrix")
        if position != list(struct.unpack_from("<3f", raw, 0x10)) or matrix[12:15] != position:
            raise ValueError("world transform position differs from native header")
        archetype = _object(placement.get("archetype"), "world archetype")
        actor_members[_identity(archetype.get("resource"))].append(placement)
        renderable += lod_check(placement)
    for indices in [*list_members.values(), *bucket_members.values()]:
        if sorted(indices) != list(range(len(indices))):
            raise ValueError("native list/bucket indices are duplicated or incomplete")
    if _integer(counts.get("renderable_world_placements"), "renderable world count") != renderable:
        raise ValueError("renderable world placement count mismatch")
    staging = _object(manifest.get("staging"), "native staging provenance")
    seen_buckets = set()
    for bucket in _array(staging.get("native_lists"), "native bucket lists"):
        index = _integer(bucket.get("bucket"), "staging bucket index")
        if index in seen_buckets:
            raise ValueError("duplicate staging bucket")
        seen_buckets.add(index)
        offsets = _array(bucket.get("list_offsets"), "staging list offsets")
        if len(offsets) != len(set(offsets)) or set(offsets) != bucket_lists.get(index, set()):
            raise ValueError("native staging list provenance disagrees with placements")
        if _integer(bucket.get("placements"), "staging placements") != len(bucket_members.get(index, [])):
            raise ValueError("native staging bucket placement count mismatch")
    if not set(bucket_members).issubset(seen_buckets):
        raise ValueError("world placements reference unreported native buckets")

    inventory = {}
    for item in _array(manifest.get("actor_visual_inventory"), "actor visual inventory"):
        item = _object(item, "actor inventory entry")
        key = _identity(item.get("resource"))
        if key in inventory or key not in actor_members:
            raise ValueError("duplicate or unplaced actor inventory entry")
        inventory[key] = item
        members = actor_members[key]
        if _integer(item.get("placements"), "inventory placement count") != len(members):
            raise ValueError("actor inventory placement count mismatch")
        if any(p["archetype"].get("native_class") != item.get("native_class") for p in members):
            raise ValueError("actor inventory native class mismatch")
        status = item.get("status")
        if status not in {"decoded", "pedestrian-skin-reader", "no-resolved-primary-visual", "unresolved-visual"}:
            raise ValueError("unknown actor visual inventory status")
        if status in {"decoded", "pedestrian-skin-reader"} and not all(p["lods"] for p in members):
            raise ValueError("decoded actor inventory has unrenderable placements")
        if status in {"no-resolved-primary-visual", "unresolved-visual"} and any(p["lods"] for p in members):
            raise ValueError("unresolved actor inventory unexpectedly has visual placements")
    if set(inventory) != set(actor_members):
        raise ValueError("actor inventory does not cover every placed archetype")

    markers = _indexed(manifest.get("scene_markers"), "scene markers")
    marker_links = set()
    for placement in landings.values():
        native = _object(placement.get("native"), "landing native provenance")
        marker_id = native.get("id")
        marker = markers.get(marker_id)
        if marker_id in marker_links or marker is None or marker.get("kind") != "landing-site":
            raise ValueError("landing visual has missing or duplicate native region marker")
        marker_links.add(marker_id)
        position = _vector(placement.get("position_f32"), 3, "landing position")
        matrix = _vector(placement.get("matrix_row_major_f32"), 16, "landing matrix")
        if position != marker.get("position_f32") or matrix[12:15] != position:
            raise ValueError("landing visual position differs from authored region")
        if not lod_check(placement):
            raise ValueError("landing visual has no native geometry")
    expected_markers = {key for key, m in markers.items() if m.get("kind") == "landing-site"}
    if marker_links != expected_markers or len(marker_links) != counts.get("landing_sites"):
        raise ValueError("landing visual coverage/count mismatch")

    if manifest.get("profile_id") == "dah1-retail-xbox-us" and manifest.get("map_name") == "area42":
        random = [p for p in objects.values() if p["archetype"]["native_class"] == "RandomType"]
        if (len(objects), len(inventory), renderable, len(random), len(landings)) != (2674, 150, 2535, 139, 3):
            raise ValueError("Area42 complete native inventory/count gate failed")
        if any(p["lods"] for p in random) or any(not p["lods"] for p in objects.values() if p not in random):
            raise ValueError("Area42 only RandomType population definitions may lack fixed visuals")
        if Counter(item["status"] for item in inventory.values()) != Counter(
                {"decoded": 132, "pedestrian-skin-reader": 15, "no-resolved-primary-visual": 3}):
            raise ValueError("Area42 actor visual inventory status gate failed")
