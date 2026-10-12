"""Read-only retail spawn/layer/landing evidence; never simulate the mission VM."""
from __future__ import annotations

import hashlib
import math
import struct
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from .container import BlockReader, ResourceRecord
from .executable_evidence import DAH1_RETAIL_XBOX_US_SHA256, XbeImage
from .world import ACTOR_ARCHETYPE_TYPE, WorldScene, read_world_scene

LUA_TYPE = 0x42EF1FD7
REGIONS_TYPE = 0xD0894AB3
TAGS_TYPE = 0x7FDB970F
WORLD_NAME = 0x59CE7BCB
RANDOM_CLASS = 0x07066922

_RUNTIME_EVIDENCE = {
    "casefolded_crc": (0xD54A0, "568bf18a0e84c98bc2f7d08bd67426900fbec90fb689a80825008bf0c1ee1833ce8b348da80425008a4a01c1e00833c64284c975dbf7d05ec3"),
    "world_region_lookup": (0x110570, "51e87aa5fcff8b1068cb7bce5968b34a89d08bc8ff521085c074228d4c2408518d542404528bc8e844e108008b04243d0fa00fa074078b400459c2040033c059c20400"),
    "landing_region_center": (0x6ADE4, "d946408b46488b0d68672800d9542420d94644"),
    "random_type_cumulative_choice": (0x119400, "51568bf157b9588a2700e8b1aefbffd95c24088b7e708b0733c985c07e2b8d570853d944240cd81adfe0f6c4057b128b46708b184183c2083bcb7ce65b5f5e59c38b4ccf04894e745b5f5e59c3"),
}


def verify_spawn_executable_evidence(path: str | Path) -> dict[str, object]:
    """Attest the precise retail routines supporting the decoded field semantics."""
    data = Path(path).read_bytes()
    digest = hashlib.sha256(data).hexdigest()
    if digest != DAH1_RETAIL_XBOX_US_SHA256:
        raise ValueError("spawn evidence requires the pinned DAH1 US Xbox retail executable")
    image = XbeImage(data)
    checks = []
    for name, (address, hex_bytes) in _RUNTIME_EVIDENCE.items():
        expected = bytes.fromhex(hex_bytes)
        if image.read_virtual(address, len(expected)) != expected:
            raise ValueError(f"spawn runtime evidence mismatch: {name}")
        checks.append({"name": name, "virtual_address": _hex(address),
                       "byte_length": len(expected), "sha256": hashlib.sha256(expected).hexdigest()})
    return {"status": "verified", "executable_sha256": digest, "checks": checks}


def native_name_hash(value: str) -> int:
    """Retail VA 0xD54A0: ASCII case-folded, non-reflected CRC-32."""
    crc = 0xFFFFFFFF
    for byte in value.encode("ascii"):
        if 65 <= byte <= 90:
            byte += 32
        if byte == 0:
            break
        crc ^= byte << 24
        for _ in range(8):
            crc = ((crc << 1) ^ (0x04C11DB7 if crc & 0x80000000 else 0)) & 0xFFFFFFFF
    return crc ^ 0xFFFFFFFF


def _hex(value: int) -> str:
    return f"0x{value:08X}"


def _span(data: bytes, offset: int, size: int, limit: int, label: str) -> bytes:
    if offset < 0 or size < 0 or offset + size > min(len(data), limit):
        raise ValueError(f"{label} is outside resource data")
    return data[offset : offset + size]


def _resource(reader: BlockReader, record: ResourceRecord) -> dict[str, object]:
    group = reader.load_group(record.load_group)
    raw = _span(group.data, record.offset, record.size, group.resource_limit, "resource")
    return {
        "type_hash": _hex(record.type_hash), "name_hash": _hex(record.name_hash),
        "load_group": record.load_group, "offset": record.offset, "size": record.size,
        "flags": record.flags, "sha256": hashlib.sha256(raw).hexdigest(),
    }


@dataclass(frozen=True)
class LuaPrototype:
    source: str | None
    line_defined: int
    strings: tuple[str | None, ...]
    numbers: tuple[float, ...]
    children: tuple[LuaPrototype, ...]
    instructions: tuple[int, ...]
    code_offset: int


class _LuaReader:
    """Bounded parser of the pinned Xbox Lua 4 serialized prototype layout."""
    def __init__(self, data: bytes):
        self.data = data
        self.pos = 0

    def take(self, size: int) -> bytes:
        result = _span(self.data, self.pos, size, len(self.data), "Lua chunk")
        self.pos += size
        return result

    def count(self) -> int:
        value = struct.unpack("<i", self.take(4))[0]
        if not 0 <= value <= len(self.data):
            raise ValueError("invalid Lua count")
        return value

    def string(self) -> str | None:
        size = self.count()
        if not size:
            return None
        raw = self.take(size)
        if raw[-1] != 0:
            raise ValueError("Lua string is not terminated")
        return raw[:-1].decode("latin-1")

    def proto(self, depth: int = 0) -> LuaPrototype:
        if depth > 64:
            raise ValueError("Lua prototype nesting is too deep")
        source = self.string()
        line = self.count()
        self.count()  # parameter count
        self.take(1)  # vararg flag
        self.count()  # stack capacity
        for _ in range(self.count()):
            self.string()
            self.count()
            self.count()
        self.take(self.count() * 4)  # encoded debug line deltas, not source lines
        strings = tuple(self.string() for _ in range(self.count()))
        numbers = tuple(struct.unpack("<f", self.take(4))[0] for _ in range(self.count()))
        if not all(math.isfinite(value) for value in numbers):
            raise ValueError("Lua number constant is non-finite")
        children = tuple(self.proto(depth + 1) for _ in range(self.count()))
        count = self.count()
        code_offset = self.pos
        code = struct.unpack(f"<{count}I", self.take(count * 4))
        for instruction in code:
            op, operand = instruction & 63, instruction >> 6
            if op > 48:
                raise ValueError("unsupported Lua opcode")
            if op in {7, 12, 14, 16, 19} and operand >= len(strings):
                raise ValueError("Lua instruction references an invalid string")
            if op in {8, 9} and operand >= len(numbers):
                raise ValueError("Lua instruction references an invalid number")
        return LuaPrototype(source, line, strings, numbers, children, code, code_offset)


def parse_lua4(data: bytes) -> LuaPrototype:
    reader = _LuaReader(data)
    if reader.take(13) != bytes.fromhex("1b4c7561400104040420060904"):
        raise ValueError("unsupported retail Lua 4 header")
    reader.take(4)  # native floating-point test number
    root = reader.proto()
    if reader.pos != len(data):
        raise ValueError("Lua chunk has unparsed trailing bytes")
    return root


def _walk_lua(proto: LuaPrototype, path: str = "0"):
    yield path, proto
    for index, child in enumerate(proto.children):
        yield from _walk_lua(child, f"{path}.{index}")


def _literal(proto: LuaPrototype, instruction: int):
    op, u = instruction & 63, instruction >> 6
    if op == 7:
        return proto.strings[u]
    if op == 6:
        return u - ((1 << 25) - 1)
    if op in {8, 9}:
        return proto.numbers[u] * (-1 if op == 9 else 1)
    raise ValueError("instruction is not a scalar literal")


def literal_calls(proto: LuaPrototype) -> list[dict[str, object]]:
    """Extract direct function lookups and literal prefix arguments, without execution."""
    result = []
    for pc, instruction in enumerate(proto.instructions):
        if instruction & 63 != 12:
            continue
        bits = [proto.strings[instruction >> 6]]
        cursor = pc + 1
        while cursor < len(proto.instructions) and proto.instructions[cursor] & 63 == 14:
            bits.append(proto.strings[proto.instructions[cursor] >> 6])
            cursor += 1
        if len(bits) < 2 or bits[0] not in {"landing", "instance"}:
            continue
        args = []
        while cursor < len(proto.instructions) and proto.instructions[cursor] & 63 in {6, 7, 8, 9}:
            args.append(_literal(proto, proto.instructions[cursor]))
            cursor += 1
        complete = cursor < len(proto.instructions) and proto.instructions[cursor] & 63 == 2
        # Only call a sequence confirmed if it ends in CALL. For dynamic arguments,
        # report the function lookup and its literal prefix as unresolved evidence.
        result.append({
            "function": ".".join(bits), "literal_prefix_arguments": args,
            "pc": pc, "code_offset": proto.code_offset + pc * 4,
            "complete_literal_call": complete,
            "confidence": "verified-bytecode-call" if complete else "function-reference-dynamic-arguments",
        })
    return result


def decode_constructor_tables(proto: LuaPrototype) -> dict[str, object]:
    """Decode straight-line literal table construction; reject executable behavior."""
    stack: list[object] = []
    globals_: dict[str, object] = {}
    try:
        for ins in proto.instructions:
            op, u = ins & 63, ins >> 6
            a, b = ins >> 15, (ins >> 6) & 511
            if op == 0:
                break
            if op in {6, 7, 8, 9}:
                stack.append(_literal(proto, ins))
            elif op == 17:
                stack.append({})
            elif op == 12:
                name = proto.strings[u]
                stack.append(globals_.setdefault(name, {}))
            elif op == 14:
                stack[-1] = stack[-1][proto.strings[u]]
            elif op == 19:
                globals_[proto.strings[u]] = stack.pop()
            elif op == 21:  # SETLIST A, B; fields A*64+1 through A*64+B
                values = stack[-b:] if b else []
                if b:
                    del stack[-b:]
                table = stack[-1]
                if not isinstance(table, dict):
                    raise ValueError("SETLIST target is not a table")
                for index, value in enumerate(values, a * 64 + 1):
                    table[index] = value
            elif op == 22:
                values = stack[-2 * u:] if u else []
                if u:
                    del stack[-2 * u:]
                table = stack[-1]
                for index in range(0, len(values), 2):
                    table[values[index]] = values[index + 1]
            elif op == 20 and (a, b) == (3, 3):
                value, key, table = stack.pop(), stack.pop(), stack.pop()
                table[key] = value
            else:
                raise ValueError(f"non-constructor Lua instruction {op}")
    except (IndexError, KeyError, TypeError) as exc:
        raise ValueError("invalid Lua constructor stack or table") from exc
    if stack:
        raise ValueError("Lua constructor leaves values on the stack")

    def plain(value):
        if not isinstance(value, dict):
            return value
        if value and set(value) == set(range(1, len(value) + 1)):
            return [plain(value[index]) for index in range(1, len(value) + 1)]
        return {str(key): plain(item) for key, item in value.items()}
    return plain(globals_)


def _hash_entries(reader: BlockReader, resource: ResourceRecord):
    group = reader.load_group(resource.load_group)
    raw = _span(group.data, resource.offset, resource.size, group.resource_limit, "hash table")
    if len(raw) < 4:
        raise ValueError("native hash table is truncated")
    mask = struct.unpack_from("<I", raw)[0]
    count = mask + 1
    if count & (count - 1) or 4 + count * 8 > len(raw):
        raise ValueError("native hash table mask or size is invalid")
    keys = set()
    for slot in range(count):
        field = resource.offset + 4 + slot * 8
        key = struct.unpack_from("<I", group.data, field)[0]
        target = group.relocations.pointers.get(field + 4)
        if target is None:
            if key:
                raise ValueError("occupied native hash slot lacks a relocation")
            continue
        if key in keys:
            raise ValueError("native hash table has duplicate keys")
        keys.add(key)
        yield key, target, field + 4


def decode_population_definition(reader: BlockReader, actor: ResourceRecord) -> dict[str, object]:
    group = reader.load_group(actor.load_group)
    raw = _span(group.data, actor.offset, actor.size, group.resource_limit, "random archetype")
    if actor.type_hash != ACTOR_ARCHETYPE_TYPE or len(raw) < 0x78:
        raise ValueError("random archetype has an unexpected type or size")
    if struct.unpack_from("<I", raw)[0] != RANDOM_CLASS:
        raise ValueError("actor is not the retail RandomType class")
    table = group.relocations.pointers.get(actor.offset + 0x70)
    if table is None:
        raise ValueError("RandomType choice array lacks its relocation")
    count = struct.unpack("<I", _span(group.data, table, 4, group.resource_limit, "random choice count"))[0]
    choices_raw = _span(group.data, table, 4 + count * 8, group.resource_limit, "random choices")
    if count == 0:
        raise ValueError("RandomType has no choices")
    choices = []
    previous = 0.0
    for index in range(count):
        field = table + 4 + index * 8
        target = group.relocations.pointers.get(field)
        resource = reader.resource_by_location(actor.load_group, target) if target is not None else None
        if resource is None or resource.type_hash != ACTOR_ARCHETYPE_TYPE:
            raise ValueError("RandomType choice does not resolve to an actor archetype")
        threshold = struct.unpack_from("<f", group.data, field + 4)[0]
        if not math.isfinite(threshold) or not previous < threshold <= 1.0:
            raise ValueError("RandomType cumulative thresholds are invalid")
        choices.append({
            "index": index, "pointer_field_offset": field, "actor": _resource(reader, resource),
            "cumulative_probability": threshold, "probability": threshold - previous,
        })
        previous = threshold
    if previous != 1.0:
        raise ValueError("RandomType cumulative thresholds do not end at one")
    return {
        "id": f"population:{actor.name_hash:08X}", "kind": "random-type",
        "resource": _resource(reader, actor), "raw_resource_hex": raw.hex(),
        "choice_table_offset": table, "choice_table_raw_hex": choices_raw.hex(),
        "choices": choices, "confidence": "verified-retail-layout-and-runtime",
        "runtime_evidence": "VA 0x119400 reads archetype+0x70, tests cumulative float thresholds at array+8+8*i, selects actor pointer at array+4+8*i",
    }


@dataclass(frozen=True)
class SpawnScene:
    reader: BlockReader
    scene_markers: tuple[dict[str, object], ...]
    spawn_definitions: tuple[dict[str, object], ...]
    spawn_layers: tuple[dict[str, object], ...]
    regions: tuple[dict[str, object], ...]
    tags: tuple[dict[str, object], ...]
    script_calls: tuple[dict[str, object], ...]
    report: dict[str, object]


def read_spawn_scene(source: str | Path | BlockReader, world: WorldScene | None = None) -> SpawnScene:
    reader = source if isinstance(source, BlockReader) else BlockReader(source)
    world = world or read_world_scene(reader)
    names: dict[int, set[str]] = defaultdict(set)
    script_calls = []
    definitions = []
    unresolved = []
    parsed_scripts = 0
    for record in reader.resources_of_type(LUA_TYPE):
        group = reader.load_group(record.load_group)
        raw = _span(group.data, record.offset, record.size, group.resource_limit, "Lua resource")
        if raw[:4] != b"\x1bLua":
            unresolved.append({"resource": _resource(reader, record), "reason": "unsupported Lua resource wrapper"})
            continue
        proto = parse_lua4(raw)
        parsed_scripts += 1
        provenance = _resource(reader, record)
        for path, child in _walk_lua(proto):
            for value in child.strings:
                if value and value.isascii():
                    names[native_name_hash(value)].add(value)
            for call in literal_calls(child):
                call.update({
                    "script": provenance, "source_path": child.source or proto.source,
                    "prototype": path, "line_defined": child.line_defined,
                    "instruction_offset": record.offset + call["code_offset"],
                    "activation": "not-evaluated; mission/progress conditions remain in the script",
                })
                script_calls.append(call)
        if proto.source and proto.source.endswith("\\site_directedspawn_local.lua"):
            tables = decode_constructor_tables(proto)
            for name, table in tables.get("area42spawn", {}).items():
                recipes = []
                if not isinstance(table, list):
                    raise ValueError("directed spawn levels must be an array")
                for level_index, level in enumerate(table, 1):
                    if not isinstance(level, list):
                        raise ValueError("directed spawn level rows must be an array")
                    for row_index, row in enumerate(level, 1):
                        if not isinstance(row, list) or len(row) != 6 or not isinstance(row[0], str):
                            raise ValueError("directed spawn row does not have six fields")
                        target = reader.resource_by_identity(ACTOR_ARCHETYPE_TYPE, native_name_hash(row[0]))
                        recipes.append({"level": level_index, "index": row_index,
                                        "actor_name": row[0], "actor": _resource(reader, target) if target else None,
                                        "behavior_name": row[1], "native_fields": row,
                                        "dependency_status": "resolved" if target else "missing-actor"})
                        if target is None:
                            unresolved.append({"kind": "directed-spawn-dependency", "actor_name": row[0], "resource": provenance})
                definitions.append({
                    "id": f"directed-spawn:{record.name_hash:08X}:{name}",
                    "kind": "directed-spawn-table", "name": f"area42spawn.{name}",
                    "levels": table, "recipes": recipes, "resource": provenance,
                    "raw_resource_hex": raw.hex(), "source_path": proto.source,
                    "initializer_code_offset": record.offset + proto.code_offset,
                    "confidence": "verified-literal-bytecode-table",
                    "row_layout": ["actor_type_name", "behavior_name", "parameter_2", "parameter_3", "parameter_4", "mode_or_mode_list"],
                    "parameter_semantics": "numeric parameters preserved; timing/count field names require separate runtime proof",
                })

    def labels(key):
        return sorted(names.get(key, ()))

    region_table = reader.resource_by_identity(REGIONS_TYPE, WORLD_NAME)
    regions = []
    if region_table is None:
        raise ValueError("world.regions resource was not found")
    group = reader.load_group(region_table.load_group)
    for key, target, field in _hash_entries(reader, region_table):
        record = reader.resource_by_location(region_table.load_group, target)
        if record is None or record.type_hash != REGIONS_TYPE or record.size != 0x50:
            raise ValueError("world region target has an unexpected type or size")
        raw = _span(group.data, target, 0x50, group.resource_limit, "region")
        matrix = np.frombuffer(raw[:64], dtype="<f4").reshape((4, 4)).copy()
        position = list(struct.unpack_from("<3f", raw, 0x40))
        if not np.isfinite(matrix).all() or not all(math.isfinite(v) for v in position):
            raise ValueError("region has non-finite transform data")
        regions.append({
            "id": f"region:{key:08X}", "name_hash": _hex(key), "name_candidates": labels(key),
            "resource": _resource(reader, record), "table_pointer_field": field,
            "position": position, "world_to_region_matrix": matrix.tolist(),
            "native_layer_hash": _hex(struct.unpack_from("<I", raw, 0x4C)[0]),
            "raw_record_hex": raw.hex(), "confidence": "verified-retail-region-table",
        })
    by_region = {int(item["name_hash"], 16): item for item in regions}
    landing_calls = defaultdict(list)
    for call in script_calls:
        if call["function"] == "landing.RegisterZone" and call["complete_literal_call"]:
            args = call["literal_prefix_arguments"]
            if args and isinstance(args[0], str):
                landing_calls[args[0]].append(call)
    markers = []
    for name, calls in sorted(landing_calls.items()):
        key = native_name_hash(name)
        region = by_region.get(key)
        if region is None:
            unresolved.append({"kind": "landing-site", "name": name, "reason": "registered region is missing", "calls": calls})
            continue
        position = region["position"]
        matrix = np.eye(4, dtype=np.float32)
        matrix[3, :3] = position
        markers.append({
            "id": f"landing:{key:08X}", "kind": "landing-site", "collection": "landing_sites",
            "label": name, "position": position, "position_f32": position,
            "status": "authored landing region; runtime activation conditional", "matrix": matrix.tolist(),
            "region_id": region["id"], "resource": region["resource"],
            "raw_record_hex": region["raw_record_hex"], "registration_calls": calls,
            "confidence": "verified-script-call-and-native-region",
            "position_semantics": "authored region center; runtime samples terrain height for the landing marker",
        })

    tags = []
    tag_table = reader.resource_by_identity(TAGS_TYPE, WORLD_NAME)
    by_entity_tags = defaultdict(list)
    if tag_table is not None:
        group = reader.load_group(tag_table.load_group)
        for key, target, field in _hash_entries(reader, tag_table):
            count = struct.unpack("<I", _span(group.data, target, 4, group.resource_limit, "tag count"))[0]
            raw = _span(group.data, target, 4 + count * 4, group.resource_limit, "tag identifiers")
            ids = list(struct.unpack_from(f"<{count}i", raw, 4))
            item = {"id": f"tag:{key:08X}", "name_hash": _hex(key), "name_candidates": labels(key),
                    "offset": target, "load_group": tag_table.load_group, "table_pointer_field": field,
                    "entity_ids": ids, "raw_record_hex": raw.hex(), "sha256": hashlib.sha256(raw).hexdigest()}
            tags.append(item)
            for entity_id in ids:
                by_entity_tags[entity_id].append(item["id"])

    for actor in sorted(world.archetypes.values(), key=lambda a: a.resource.name_hash):
        if actor.native_class == "RandomType":
            definition = decode_population_definition(reader, actor.resource)
            definition["name"] = actor.asset_name
            definitions.append(definition)
    layers = []
    population_count = 0
    for bucket in world.buckets:
        layer_names = labels(bucket.type_hash)
        calls = [call for call in script_calls if call["function"] in {
            "instance.StartLayer", "instance.StopLayer", "instance.RestartLayer", "instance.CullLayer"
        } and call["complete_literal_call"] and call["literal_prefix_arguments"]
            and isinstance(call["literal_prefix_arguments"][0], str)
            and native_name_hash(call["literal_prefix_arguments"][0]) == bucket.type_hash]
        records = []
        for placement in bucket.placements:
            actor = placement.actor
            entity_id = struct.unpack_from("<i", placement.raw_header, 4)[0]
            # Matches the exporter existing object identity; this is a relationship,
            # not a new entity or proof that a mission layer is currently active.
            item = {
                "bucket_index": bucket.index, "placement_index": placement.index,
                "load_group": world.database.load_group, "offset": placement.offset,
                "entity_id": entity_id, "tag_ids": sorted(by_entity_tags[entity_id]),
                "actor": _resource(reader, actor.resource) if actor else None,
                "asset_name": actor.asset_name if actor else None,
                "native_class": actor.native_class if actor else None,
                "position": placement.position.tolist(), "matrix": placement.matrix.tolist(),
                "raw_header_hex": placement.raw_header.hex(),
                "raw_header_sha256": hashlib.sha256(placement.raw_header).hexdigest(),
                "activity": "authored-placement; mission-layer activation not evaluated",
            }
            records.append(item)
            if actor and actor.native_class == "RandomType":
                population_count += 1
                markers.append({
                    "id": f"population-placement:{bucket.index}:{placement.index}",
                    "kind": "population-spawn", "collection": "entity_spawns",
                    "label": actor.asset_name or _hex(actor.resource.name_hash),
                    "position": item["position"], "position_f32": item["position"],
                    "status": "authored population spawn; runtime activation not evaluated", "matrix": item["matrix"],
                    "definition_id": f"population:{actor.resource.name_hash:08X}",
                    "world_placement": {"bucket_index": bucket.index, "index": placement.index},
                    "resource": item["actor"], "load_group": world.database.load_group,
                    "offset": placement.offset, "entity_id": entity_id,
                    "layer_hash": _hex(bucket.type_hash), "layer_names": layer_names,
                    "raw_header_hex": item["raw_header_hex"],
                    "confidence": "verified-native-placement-and-random-type",
                    "activity": item["activity"],
                })
            elif actor and not actor.lods and not (
                actor.native_class == "ActorTypePedestrian" and actor.asset_name
                and actor.asset_name.startswith("h_")
            ):
                markers.append({
                    "id": f"entity-placement:{bucket.index}:{placement.index}",
                    "kind": "entity-spawn", "collection": "entity_spawns",
                    "label": actor.asset_name or actor.native_class or _hex(actor.resource.name_hash),
                    "position_f32": item["position"], "position": item["position"],
                    "matrix": item["matrix"], "status": "authored entity; runtime activation not evaluated",
                    "world_placement": {"bucket_index": bucket.index, "index": placement.index},
                    "resource": item["actor"], "load_group": world.database.load_group,
                    "offset": placement.offset, "entity_id": entity_id,
                    "layer_hash": _hex(bucket.type_hash), "layer_names": layer_names,
                    "raw_header_hex": item["raw_header_hex"],
                    "confidence": "verified-native-placement",
                })
        layers.append({"index": bucket.index, "hash": _hex(bucket.type_hash),
                       "name_candidates": layer_names, "offset": bucket.offset,
                       "load_group": world.database.load_group, "script_calls": calls,
                       "placements": records})
    report = {
        "status": "decoded-with-explicit-runtime-unknowns",
        "counts": {"landing_sites": len(landing_calls), "landing_site_markers": sum(m["kind"] == "landing-site" for m in markers),
                   "regions": len(regions), "tags": len(tags), "spawn_layers": len(layers),
                   "authored_entity_records": len(world.placements), "population_spawn_placements": population_count,
                   "population_definitions": sum(d["kind"] == "random-type" for d in definitions),
                   "directed_spawn_tables": sum(d["kind"] == "directed-spawn-table" for d in definitions),
                   "directed_spawn_recipes": sum(len(d.get("recipes", [])) for d in definitions),
                   "parsed_lua_resources": parsed_scripts, "script_function_references": len(script_calls),
                   "scene_markers": len(markers),
                   "entity_spawn_markers": sum(m["kind"] == "entity-spawn" for m in markers)},
        "unresolved": unresolved,
        "limitations": [
            "Markers and authored entity records do not imply simultaneous runtime activation.",
            "Landing positions are authored region centers; runtime projects the visible marker to terrain.",
            "Directed spawn recipe numeric fields remain positional values until their runtime semantics are verified.",
            "Dynamic instance.CreateObject arguments and traffic/road spawn selection are reported without invented fixed positions.",
            "Entity record metadata after the existing 0x40-byte header remains undecoded.",
        ],
        "source_hashes": reader.source_hashes(),
    }
    return SpawnScene(reader, tuple(markers), tuple(definitions), tuple(layers), tuple(regions), tuple(tags), tuple(script_calls), report)
