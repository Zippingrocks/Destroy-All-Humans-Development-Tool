"""Read-only pickup and mission-objective evidence from retail actors and Lua 4.

This is deliberately not a mission interpreter. Tables, closures and direct call
expressions are inspected without executing scripts, assigning a mission state,
moving targets, or creating additional copies of authored pickups.
"""
from __future__ import annotations

import hashlib
import struct
from collections import Counter, defaultdict
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from .container import BlockReader
from .executable_evidence import DAH1_RETAIL_XBOX_US_SHA256, XbeImage
from .spawns import (
    LUA_TYPE, LuaPrototype, SpawnScene, _hex, _resource, _span, _walk_lua,
    native_name_hash, parse_lua4, read_spawn_scene,
)
from .world import WorldScene, read_world_scene

POWERUP_CLASS = 0xD98FEBFE
_CALL_ROOTS = {"mission", "instance", "probeManager", "powerup"}
_CALLS = {
    "hud.radar.AddRegion", "hud.radar.AddTag", "hud.radar.RemoveRegion",
    "hud.radar.RemoveTag", "siteTracker.GetProbeCount", "siteTracker.GetProbeTotal",
}
_ACTIVITY = "Authored/script evidence only; mission conditions and collected profile state are not evaluated."
_RUNTIME_SPANS = {
    "marker_lua_registration": (0x73C21, "68300f07006894be22008bcee8be590c0068900f07006880be22008bcee8ad590c0068b00f07006870be22008bcee89c590c00"),
    "tag_marker_two_string_arguments": (0x70FB0, "56578b3d68fc25006a028bf1e85f850c00506a018bcee855850c00508bcfe88dcaffff5f33c05ec3"),
    "region_marker_native_center_then_runtime_projection": (0x6D9A4, "8b442428d940408b0d68672800d95424186808912200d940448b4048d954242089442424d9c98d542414d95c2414528d442428d95c241c8b895030000050e8692c0a00"),
    "powerup_native_class_dispatch": (0x638B1, "3dfeeb8fd9776a7448"),
    "powerup_native_constructor": (0x63902, "68a4010000e8e47d000083c40485c07416578bc8e8454f0200"),
}


def verify_objective_executable_evidence(path: str | Path) -> dict:
    data = Path(path).read_bytes()
    digest = hashlib.sha256(data).hexdigest()
    if digest != DAH1_RETAIL_XBOX_US_SHA256:
        raise ValueError("objective evidence requires the pinned DAH1 US Xbox retail executable")
    image = XbeImage(data)
    checks = []
    for name, (address, value) in _RUNTIME_SPANS.items():
        raw = bytes.fromhex(value)
        if image.read_virtual(address, len(raw)) != raw:
            raise ValueError(f"objective executable span mismatch: {name}")
        checks.append({"name": name, "virtual_address": _hex(address),
                       "byte_length": len(raw), "sha256": hashlib.sha256(raw).hexdigest()})
    for address, value in ((0x22BE94, b"AddRegionMarker\0"), (0x22BE70, b"AddTagMarker\0")):
        if image.read_virtual(address, len(value)) != value:
            raise ValueError("objective marker registration name differs")
    return {"status": "verified", "executable_sha256": digest, "checks": checks}


@dataclass(frozen=True)
class ObjectiveScene:
    collectibles: tuple[dict, ...]
    objectives: tuple[dict, ...]
    mission_registrations: tuple[dict, ...]
    scene_markers: tuple[dict, ...]
    script_calls: tuple[dict, ...]
    visual_dependencies: tuple[dict, ...]
    report: dict


def _code(proto: LuaPrototype, start: int, end: int) -> dict:
    words = proto.instructions[start:end]
    raw = struct.pack(f"<{len(words)}I", *words)
    return {"pc": start, "end_pc": end,
            "code_offset": proto.code_offset + start * 4,
            "raw_instructions_hex": raw.hex(), "sha256": hashlib.sha256(raw).hexdigest()}


def _expression(proto: LuaPrototype, pc: int, path: str, depth: int = 0):
    """Decode one non-executing expression; function calls are never expressions here."""
    if depth > 32 or pc >= len(proto.instructions):
        raise ValueError("truncated or deeply nested Lua evidence expression")
    start = pc
    ins = proto.instructions[pc]
    op, u = ins & 63, ins >> 6
    pc += 1
    if op == 7:
        value = proto.strings[u]
    elif op == 6:
        value = u - ((1 << 25) - 1)
    elif op in {8, 9}:
        value = proto.numbers[u] * (-1 if op == 9 else 1)
    elif op == 4 and u == 1:
        value = None
    elif op in {11, 12}:
        fields = []
        while pc < len(proto.instructions) and proto.instructions[pc] & 63 == 14:
            fields.append(proto.strings[proto.instructions[pc] >> 6])
            pc += 1
        value = {"global_reference": proto.strings[u], "fields": fields} if op == 12 else {
            "local_reference": u, "fields": fields}
    elif op == 48:
        child, upvalues = ins >> 15, (ins >> 6) & 511
        if upvalues or child >= len(proto.children):
            raise ValueError("unresolved or invalid Lua closure captures")
        value = {"prototype": f"{path}.{child}"}
    elif op == 17:
        if u == 0:
            return {}, pc, {"instruction_span": _code(proto, start, pc), "fields": {}}
        values, fields, provenance = [], {}, {}
        while pc < len(proto.instructions):
            ins = proto.instructions[pc]
            opcode, operand = ins & 63, ins >> 6
            if opcode == 22:  # SETMAP consumes literal key/value pairs.
                if len(values) != operand * 2:
                    raise ValueError("Lua SETMAP evidence has an inconsistent stack")
                for index in range(0, len(values), 2):
                    key, key_source = values[index]
                    value, value_source = values[index + 1]
                    if not isinstance(key, (str, int, float)) or isinstance(key, bool):
                        raise ValueError("Lua evidence table has a nonliteral key")
                    # Last assignment wins within this literal table, matching Lua.
                    fields[str(key)] = value
                    provenance[str(key)] = {"key": key_source, "value": value_source}
                pc += 1
                return fields, pc, {"instruction_span": _code(proto, start, pc), "fields": provenance}
            if opcode == 21:  # Bounded one-flush literal array; do not infer loops.
                base, count = ins >> 15, (ins >> 6) & 511
                if base or count != len(values):
                    raise ValueError("unsupported multi-flush Lua array constructor")
                pc += 1
                return [item[0] for item in values], pc, {
                    "instruction_span": _code(proto, start, pc),
                    "items": [item[1] for item in values]}
            value, pc, source = _expression(proto, pc, path, depth + 1)
            values.append((value, source))
        raise ValueError("unterminated Lua literal table")
    else:
        raise ValueError(f"dynamic Lua expression opcode {op}")
    return value, pc, {"instruction_span": _code(proto, start, pc)}


def objective_definitions(proto: LuaPrototype, path: str = "0") -> list[dict]:
    """Recognize exact ``global[key] = {type=..., ...}`` bytecode assignments.

The source strings alone are insufficient: a table constructor and SETTABLE
must both exist. Unrecognized executable statements remain in the source chunk.
"""
    result = []
    pc = 0
    while pc + 3 < len(proto.instructions):
        if proto.instructions[pc] & 63 != 12:
            pc += 1
            continue
        start = pc
        try:
            owner, cursor, _ = _expression(proto, pc, path)
            if proto.instructions[cursor] & 63 != 7 or proto.instructions[cursor + 1] & 63 != 17:
                pc += 1
                continue
            name = proto.strings[proto.instructions[cursor] >> 6]
            table, cursor, evidence = _expression(proto, cursor + 1, path)
            end = proto.instructions[cursor]
            if end & 63 != 20 or (end >> 15, (end >> 6) & 511) != (3, 3):
                pc += 1
                continue
            if isinstance(table, dict) and isinstance(table.get("type"), str):
                result.append({"mission_table": ".".join([owner["global_reference"], *owner["fields"]]),
                               "name": name, "type": table["type"], "fields": table,
                               "field_provenance": evidence["fields"],
                               "instruction_span": _code(proto, start, cursor + 1)})
            pc = cursor + 1
        except (ValueError, IndexError, KeyError):
            pc = start + 1
    return result


def objective_calls(proto: LuaPrototype, path: str = "0") -> list[dict]:
    """Inspect selected dotted function references and their nonexecuting arguments."""
    result = []
    for pc, ins in enumerate(proto.instructions):
        if ins & 63 != 12:
            continue
        function, cursor, _ = _expression(proto, pc, path)
        parts = [function["global_reference"], *function["fields"]]
        name = ".".join(parts)
        if len(parts) < 2 or not (parts[0] in _CALL_ROOTS or name in _CALLS):
            continue
        # Select function-like names, not mission.currentObjective data references.
        if not parts[-1][:1].isupper():
            continue
        args, provenance = [], []
        complete = False
        nested_function_reference = False
        while cursor < len(proto.instructions):
            if proto.instructions[cursor] & 63 == 2:
                # A nested function's CALL is not proof that the outer function
                # was called; expression evaluation is deliberately not simulated.
                complete = not nested_function_reference
                cursor += 1
                break
            try:
                value, cursor, source = _expression(proto, cursor, path)
                args.append(value)
                provenance.append(source)
                if isinstance(value, dict) and value.get("global_reference") in _CALL_ROOTS:
                    fields = value.get("fields", [])
                    nested_function_reference |= bool(fields and fields[-1][:1].isupper())
            except ValueError:
                break
        result.append({"function": name, "arguments": args,
                       "argument_provenance": provenance, "complete_call": complete,
                       "status": "static-expression-call" if complete else "unresolved-call-expression",
                       "instruction_span": _code(proto, pc, cursor)})
    return result


def _world_ref(world: WorldScene, p) -> dict:
    return {"world_placement_id": f"world-placement:{p.bucket_index:02d}:{p.index:04d}",
            "bucket_index": p.bucket_index, "placement_index": p.index,
            "native_layer_hash": _hex(p.bucket_hash), "offset": p.offset,
            "load_group": world.database.load_group,
            "entity_id": struct.unpack_from("<i", p.raw_header, 4)[0],
            "position": p.position.tolist(), "matrix": p.matrix.tolist(),
            "list_pointer_field": p.list_pointer_field, "list_offset": p.list_offset,
            "list_index": p.list_index, "raw_header_hex": p.raw_header.hex(),
            "raw_header_sha256": hashlib.sha256(p.raw_header).hexdigest()}


def _resolved_target(kind: str, name: str, regions: dict, tags: dict, entities: dict) -> dict:
    key = _hex(native_name_hash(name))
    result = {"kind": kind, "name": name, "name_hash": key}
    source = (regions if kind == "region" else tags).get(key)
    if source is None:
        return {**result, "status": "unresolved-runtime-or-absent-native-target"}
    if kind == "region":
        return {**result, "status": "resolved-native-region", "native_region_id": source["id"],
                "position": source["position"], "native_layer_hash": source["native_layer_hash"],
                "resource": source["resource"], "raw_record_hex": source["raw_record_hex"],
                "world_to_region_matrix": source["world_to_region_matrix"]}
    placements = [entry for identity in source["entity_ids"] for entry in entities.get(identity, ())]
    missing = [identity for identity in source["entity_ids"] if identity not in entities]
    return {**result, "status": "resolved-native-tag" if not missing else "partially-resolved-native-tag",
            "native_tag_id": source["id"], "entity_ids": source["entity_ids"],
            "placement_refs": placements, "unresolved_entity_ids": missing,
            "offset": source["offset"], "load_group": source["load_group"],
            "raw_record_hex": source["raw_record_hex"]}


def _resolve_argument(value, fields: dict):
    if isinstance(value, dict) and value.get("local_reference") == 0:
        names = value.get("fields", [])
        if len(names) == 1 and names[0] in fields:
            return fields[names[0]]
    return value


def read_objectives(source: str | Path | BlockReader | WorldScene, spawns: SpawnScene | None = None) -> ObjectiveScene:
    world = source if isinstance(source, WorldScene) else read_world_scene(source, include_visuals=False)
    reader = world.reader
    spawns = spawns or read_spawn_scene(reader, world)
    regions = {entry["name_hash"]: entry for entry in spawns.regions}
    tags = {entry["name_hash"]: entry for entry in spawns.tags}
    entities = defaultdict(list)
    for p in world.placements:
        ref = _world_ref(world, p)
        entities[ref["entity_id"]].append(ref)

    collectibles, archetypes = [], {}
    for p in world.placements:
        actor = p.actor
        if actor is None or actor.native_class != "PowerupType":
            continue
        resource = actor.resource
        group = reader.load_group(resource.load_group)
        raw = _span(group.data, resource.offset, resource.size, group.resource_limit, "pickup archetype")
        if len(raw) < 0x1E8 or struct.unpack_from("<I", raw)[0] != POWERUP_CLASS:
            raise ValueError("pickup archetype class or native record is invalid")
        name_offset = group.relocations.pointers.get(resource.offset + 0x0C)
        if name_offset is None or not resource.offset <= name_offset < resource.offset + resource.size:
            raise ValueError("pickup archetype name relocation is missing or outside its resource")
        end = group.data.find(b"\0", name_offset, resource.offset + resource.size)
        if end < 0:
            raise ValueError("pickup archetype name is unterminated")
        name = group.data[name_offset:end].decode("ascii")
        if not name.startswith("pu_") or native_name_hash(name) != resource.name_hash:
            raise ValueError("pickup archetype name does not match its resource identity")
        kind = "probe" if name == "pu_probe" else "mission-debris" if name == "pu_ufo_debris" else "pickup"
        identity = _hex(resource.name_hash)
        if identity not in archetypes:
            archetypes[identity] = {"resource": _resource(reader, resource), "asset_name": name,
                "name_pointer_field": resource.offset + 0x0C, "name_offset": name_offset,
                "raw_name_hex": group.data[name_offset:end + 1].hex(), "raw_archetype_hex": raw.hex(),
                "relocations": [{"field_offset": f, "target_offset": t}
                                for f, t in sorted(group.relocations.pointers.items())
                                if resource.offset <= f < resource.offset + resource.size],
                "visual_resource": _resource(reader, actor.lod_resource) if actor.lod_resource else None,
                "unknown_fields": "All non-name/class archetype fields retained raw; pickup behavior, rewards and profile bit semantics not inferred."}
        collectibles.append({"id": f"collectible:{p.bucket_index:02d}:{p.index:04d}", **_world_ref(world, p),
                             "asset_name": name, "kind": kind, "actor_resource": _resource(reader, resource),
                             "native_class": actor.native_class, "activity": _ACTIVITY,
                             "representation": "metadata-for-existing-world-placement; do-not-spawn-a-copy"})

    definitions, calls, scripts, unresolved = [], [], [], []
    for resource in reader.resources_of_type(LUA_TYPE):
        group = reader.load_group(resource.load_group)
        raw = _span(group.data, resource.offset, resource.size, group.resource_limit, "script")
        if not raw.startswith(b"\x1bLua"):
            unresolved.append({"resource": _resource(reader, resource), "reason": "not-a-supported-Lua-4-chunk"})
            continue
        proto = parse_lua4(raw)
        source_path = proto.source or ""
        site_script = "\\blocks\\sites\\area42\\scripts\\" in source_path.lower()
        evidence = {"script": _resource(reader, resource), "source_path": source_path}
        script_defs, script_calls = [], []
        for path, child in _walk_lua(proto):
            if site_script:
                for definition in objective_definitions(child, path):
                    definition.update(evidence)
                    definition["prototype"] = path
                    definition["id"] = f"objective:{resource.name_hash:08X}:{path}:{definition['instruction_span']['pc']}"
                    definition["instruction_offset"] = resource.offset + definition["instruction_span"]["code_offset"]
                    definition["activity"] = _ACTIVITY
                    definition["targets"] = [_resolved_target("region" if key == "region" else "tag", value,
                                                  regions, tags, entities)
                                             for key, value in definition["fields"].items()
                                             if key in {"tag", "tag2", "region"} and isinstance(value, str)]
                    script_defs.append(definition)
            for call in objective_calls(child, path):
                # Site objectives plus the shared definitions of marker/probe interfaces.
                if not site_script and call["function"] not in {
                    "mission.RegisterType", "probeManager.LoadSite", "probeManager.SaveSite",
                    "probeManager.SetProfileKeyPrefix", "probeManager.GetPickupCount",
                    "siteTracker.GetProbeCount", "siteTracker.GetProbeTotal"}:
                    continue
                call.update(evidence)
                call["prototype"] = path
                call["id"] = f"objective-call:{resource.name_hash:08X}:{path}:{call['instruction_span']['pc']}"
                call["instruction_offset"] = resource.offset + call["instruction_span"]["code_offset"]
                call["activity"] = _ACTIVITY
                script_calls.append(call)
        if script_defs or script_calls:
            scripts.append({**evidence, "raw_resource_hex": raw.hex()})
        definitions.extend(script_defs)
        calls.extend(script_calls)

    callback_owners = defaultdict(list)
    by_mission_name = defaultdict(list)
    for definition in definitions:
        by_mission_name[(definition["mission_table"], definition["name"])].append(definition["id"])
        for phase, value in definition["fields"].items():
            if isinstance(value, dict) and "prototype" in value:
                callback_owners[(definition["script"]["name_hash"], value["prototype"])].append((definition, phase))
    marker_targets = {}
    dependencies = {}
    registrations = []
    layers = {layer["hash"]: layer for layer in spawns.spawn_layers}
    for call in calls:
        owners = callback_owners.get((call["script"]["name_hash"], call["prototype"]), [])
        call["objective_contexts"] = [{"objective_id": item["id"], "phase": phase} for item, phase in owners]
        # Keep original expressions. Resolving callback self.field is separately labeled.
        fields = owners[0][0]["fields"] if len(owners) == 1 else {}
        args = [_resolve_argument(value, fields) for value in call["arguments"]]
        call["resolved_arguments"] = args
        call["targets"] = []
        call["layer_references"] = []
        function = call["function"]
        if function in {"instance.StartLayer", "instance.StopLayer", "instance.RestartLayer", "instance.CullLayer"} and args and isinstance(args[0], str):
            layer_hash = _hex(native_name_hash(args[0]))
            layer = layers.get(layer_hash)
            call["layer_references"] = [{"name": args[0], "native_layer_hash": layer_hash,
                "bucket_index": layer["index"] if layer else None,
                "status": "resolved-native-layer" if layer else "unresolved-native-layer",
                "operation": function, "activity": "Script command reference, not proof of current layer activation."}]
        if function in {"mission.StartObjective", "mission.StopObjective"} and args and isinstance(args[0], str):
            call["objective_references"] = {"name": args[0], "operation": function,
                "definition_ids": sorted({identity for owner, _ in owners
                                          for identity in by_mission_name.get((owner["mission_table"], args[0]), ())}),
                "resolution_scope": "same mission table as the known callback owner; common/runtime objectives remain unresolved"}
        if function in {"mission.AddRegionMarker", "mission.RemoveRegionMarker", "hud.radar.AddRegion", "hud.radar.RemoveRegion"}:
            target_kind = "region"
        elif function in {"mission.AddTagMarker", "mission.RemoveTagMarker", "hud.radar.AddTag", "hud.radar.RemoveTag"}:
            target_kind = "tag"
        elif function == "mission.Call" and args and args[0] in {"SetRegion", "SetTag", "SetTag2"}:
            target_kind = "region" if args[0] == "SetRegion" else "tag"
            args = args[1:]
        else:
            target_kind = None
        if target_kind and args and isinstance(args[0], str):
            target = _resolved_target(target_kind, args[0], regions, tags, entities)
            call["targets"].append(target)
        if function == "mission.Register":
            original_args = call["resolved_arguments"]
            registration = {"id": call["id"], "call_id": call["id"], "arguments": original_args,
                            "status": call["status"], "source_path": call["source_path"], "targets": []}
            if len(original_args) >= 4 and all(isinstance(original_args[i], str) for i in (0, 2, 3)):
                registration.update(name=original_args[0], type=original_args[2], zone=original_args[3])
                registration["targets"] = [_resolved_target("region", original_args[3], regions, tags, entities)]
            registrations.append(registration)
        if function in {"mission.AddRegionMarker", "mission.AddTagMarker"} and call["complete_call"]:
            original_args = call["resolved_arguments"]
            if len(original_args) >= 2 and isinstance(original_args[1], str):
                name = original_args[1]
                resource = reader.resource_by_identity(0x1D9D48EC, native_name_hash(name))
                dependencies[name] = {"name": name, "name_hash": _hex(native_name_hash(name)),
                    "resource": _resource(reader, resource) if resource else None,
                    "status": "resolved-native-marker-archetype" if resource else "unresolved-native-marker-archetype"}
            for target in call["targets"]:
                if target["status"] == "resolved-native-region":
                    key = target["native_region_id"]
                    marker = marker_targets.setdefault(key, {"id": f"objective-{key}", "kind": "mission-objective-region",
                        "name": target["name"], "position": target["position"],
                        "matrix": np.eye(4, dtype=np.float32).tolist(),
                        "native_layer_hash": target["native_layer_hash"], "native_region_id": key,
                        "source": target, "call_ids": [], "activity": _ACTIVITY,
                        "placement_status": "native-region-center-inspection-marker; not a runtime projected marker"})
                    marker["matrix"][3][:3] = target["position"]
                    marker["call_ids"].append(call["id"])
        for definition, phase in owners:
            definition.setdefault("callback_call_ids", []).append(call["id"])
            if call["targets"]:
                definition.setdefault("callback_targets", []).append({"phase": phase, "call_id": call["id"],
                                                                       "targets": call["targets"]})
            if call["layer_references"]:
                definition.setdefault("layer_references", []).append({"phase": phase, "call_id": call["id"],
                                                                        "layers": call["layer_references"]})
    counts = {"authored_pickups": len(collectibles),
              "probes": sum(item["kind"] == "probe" for item in collectibles),
              "mission_debris": sum(item["kind"] == "mission-debris" for item in collectibles),
              "pickup_archetypes": len(archetypes), "objective_definitions": len(definitions),
              "objective_types": dict(sorted(Counter(item["type"] for item in definitions).items())),
              "mission_registrations": len(registrations), "script_calls": len(calls),
              "unresolved_call_expressions": sum(not item["complete_call"] for item in calls),
              "objective_region_markers": len(marker_targets)}
    return ObjectiveScene(tuple(collectibles), tuple(definitions), tuple(registrations),
                          tuple(marker_targets.values()), tuple(calls), tuple(dependencies.values()),
                          {"status": "read-only-native-and-script-evidence", "counts": counts,
                           "pickup_archetypes": list(archetypes.values()), "scripts": scripts,
                           "unresolved_scripts": unresolved,
                           "limitations": [
                               "All authored mission layers are catalogued; simultaneous runtime activation is not implied.",
                               "Collectibles reference existing world placements; no additional pickup objects are created.",
                               "Profile persistence, pickup animation and script control flow are not simulated.",
                               "Only bounded literal constructors and direct expression calls are decoded; unresolved calls remain explicit.",
                               "Entity tag members retain every matching authored placement; dynamic runtime tags may be absent.",
                               "Region centers are inspection markers, not engine-projected native marker transforms."]})


def validate_objective_report(manifest: dict) -> None:
    """Optional schema-4 extension: reconstruct definitions from preserved Lua bytes.

No golden count can substitute for this graph check. Older schema-4 scenes with
no objective report remain readable.
"""
    report = manifest.get("objective_validation")
    if report is None:
        return
    if not isinstance(report, dict):
        raise ValueError("objective validation must be an object")
    expected = {}
    scripts = {}
    for entry in report.get("scripts", []):
        resource = entry["script"]
        raw = bytes.fromhex(entry["raw_resource_hex"])
        if len(raw) != resource["size"] or hashlib.sha256(raw).hexdigest() != resource["sha256"]:
            raise ValueError("objective script size/hash mismatch")
        identity = resource["name_hash"]
        if identity in scripts:
            raise ValueError("duplicate objective script resource")
        root = parse_lua4(raw)
        if root.source != entry["source_path"]:
            raise ValueError("objective script source path differs from raw prototype")
        scripts[identity] = (entry, {path: child for path, child in _walk_lua(root)})
        if "\\blocks\\sites\\area42\\scripts\\" in (root.source or "").lower():
            for path, child in _walk_lua(root):
                for definition in objective_definitions(child, path):
                    expected[(identity, path, definition["instruction_span"]["pc"])] = definition
    actual = {}
    for definition in manifest.get("objective_definitions", []):
        key = (definition["script"]["name_hash"], definition["prototype"], definition["instruction_span"]["pc"])
        original = expected.get(key)
        if key in actual or original is None:
            raise ValueError("duplicate or unsupported objective definition")
        actual[key] = definition
        for field in ("mission_table", "name", "type", "fields", "field_provenance", "instruction_span"):
            if definition[field] != original[field]:
                raise ValueError(f"objective {field} differs from reconstructed Lua table")
        resource = scripts[key[0]][0]["script"]
        if definition["script"] != resource or definition["instruction_offset"] != resource["offset"] + original["instruction_span"]["code_offset"]:
            raise ValueError("objective resource/instruction provenance mismatch")
    if set(actual) != set(expected):
        raise ValueError("objective definitions do not cover the decoded raw script tables")
    objects = {entry["id"]: entry for entry in manifest.get("object_placements", [])}
    pickups = [entry for entry in objects.values() if entry.get("archetype", {}).get("native_class") == "PowerupType"]
    seen = set()
    for pickup in manifest.get("collectible_placements", []):
        identity = pickup["world_placement_id"]
        original = objects.get(identity)
        if identity in seen or original is None or original.get("archetype", {}).get("native_class") != "PowerupType":
            raise ValueError("collectible has duplicate or missing native world placement")
        seen.add(identity)
        if (pickup["position"] != original["position_f32"] or
                pickup["raw_header_hex"] != original["native"]["raw_header_hex"] or
                pickup["native_layer_hash"] != original["native"]["bucket_hash"] or
                pickup["asset_name"] != original["archetype"]["asset_name"] or
                pickup["actor_resource"]["name_hash"] != original["archetype"]["resource"]["name_hash"]):
            raise ValueError("collectible differs from its existing native placement")
        name = pickup["asset_name"]
        expected_kind = "probe" if name == "pu_probe" else "mission-debris" if name == "pu_ufo_debris" else "pickup"
        if pickup["kind"] != expected_kind or native_name_hash(name) != int(pickup["actor_resource"]["name_hash"], 16):
            raise ValueError("collectible kind/name identity mismatch")
    if seen != {entry["id"] for entry in pickups}:
        raise ValueError("collectible metadata does not cover native pickup placements")
    for definition in actual.values():
        for target in definition["targets"]:
            for link in target.get("placement_refs", []):
                original = objects.get(link["world_placement_id"])
                if original is None or link["raw_header_hex"] != original["native"]["raw_header_hex"] or link["position"] != original["position_f32"]:
                    raise ValueError("objective target differs from its referenced native placement")
            if "position" in target:
                if target["kind"] != "region" or target["position"] != list(struct.unpack_from("<3f", bytes.fromhex(target["raw_record_hex"]), 0x40)):
                    raise ValueError("objective region target differs from native center")
    counts = manifest["counts"]
    for key, count in (("objective_definitions", len(actual)), ("authored_pickups", len(seen))):
        if counts.get(key) != count or report["counts"].get(key) != count:
            raise ValueError("objective or pickup counts differ from decoded records")
