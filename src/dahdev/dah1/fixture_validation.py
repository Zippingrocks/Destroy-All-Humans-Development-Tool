"""Optional cross-reference gate for native building-fixture scene evidence."""
from __future__ import annotations

import struct


def _object(value, label):
    if not isinstance(value, dict):
        raise ValueError(f"fixture {label} must be an object")
    return value


def _array(value, label):
    if not isinstance(value, list):
        raise ValueError(f"fixture {label} must be an array")
    return value


def _uint(value, label):
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError(f"fixture {label} must be a nonnegative integer")
    return value


def _hash(value, label):
    try:
        result = int(value, 16) if isinstance(value, str) else _uint(value, label)
    except (ValueError, TypeError) as exc:
        raise ValueError(f"fixture {label} is not a native hash") from exc
    if not 0 <= result <= 0xFFFFFFFF:
        raise ValueError(f"fixture {label} exceeds uint32")
    return result


def _raw(value, label, size=None):
    try:
        result = bytes.fromhex(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"fixture {label} is not hexadecimal bytes") from exc
    if size is not None and len(result) != size:
        raise ValueError(f"fixture {label} has an incorrect byte length")
    return result


def _resource(value):
    value = _object(value, "resource")
    return (_hash(value.get("type_hash"), "resource type"),
            _hash(value.get("name_hash"), "resource name"),
            _uint(value.get("load_group"), "resource group"),
            _uint(value.get("offset"), "resource offset"))


def validate_building_fixture_report(manifest: dict) -> None:
    """Validate optional fixture evidence; older v4 manifests remain compatible.

The source archetype's counted fixture table drives expected record identities.
Inventory links those definitions to native parent records; every parent LOD
must carry exactly the selected/rendered fixture set. Multiple native visual
parts per fixture remain allowed. This checks preservation/ownership, not native
distance-LOD selection, dynamic state, or graphical parity.
"""
    manifest = _object(manifest, "manifest")
    if "building_fixture_validation" not in manifest:
        if "selected_building_fixtures" in manifest.get("counts", {}):
            raise ValueError("fixture count is present but native fixture report is missing")
        return
    report = _object(manifest["building_fixture_validation"], "report")
    definitions = {}
    for archetype in _array(report.get("archetypes"), "archetypes"):
        archetype = _object(archetype, "archetype")
        identity = _resource(archetype.get("resource"))
        if identity in definitions:
            raise ValueError("duplicate fixture archetype identity")
        owner_offset = identity[3]
        raw = _raw(archetype.get("raw_actor_hex"), "raw actor")
        declared_size = _uint(archetype["resource"].get("size"), "actor byte length")
        if len(raw) != declared_size or len(raw) < 0x88:
            raise ValueError("fixture actor bytes do not match resource size")
        pointers = {}
        for pair in _array(archetype.get("relocations"), "relocations"):
            if not isinstance(pair, (list, tuple)) or len(pair) != 2:
                raise ValueError("fixture relocation must be a source/target pair")
            source, target = (_uint(pair[0], "relocation source"), _uint(pair[1], "relocation target"))
            if source in pointers or not owner_offset <= source < owner_offset + len(raw):
                raise ValueError("duplicate or out-of-owner fixture relocation")
            pointers[source] = target
        table = archetype.get("fixture_table_offset")
        if table != pointers.get(owner_offset + 0x84):
            raise ValueError("fixture table does not match its native relocation")
        records = []
        if table is not None:
            table = _uint(table, "table offset")
            local = table - owner_offset
            if local < 0 or local + 4 > len(raw):
                raise ValueError("fixture table is outside preserved actor bytes")
            count = struct.unpack_from("<I", raw, local)[0]
            if local + 4 + count * 0x64 > len(raw):
                raise ValueError("fixture records exceed preserved actor bytes")
            for index in range(count):
                start = local + 4 + index * 0x64
                record = raw[start:start + 0x64]
                tag, visual = struct.unpack_from("<II", record)
                records.append((owner_offset + start, tag, visual, record))
            if len({r[1] for r in records}) != len(records):
                raise ValueError("fixture archetype contains duplicate tags")
        definitions[identity] = records

    owners = {}
    for owner in _array(manifest.get("object_placements"), "world placements"):
        owner = _object(owner, "world placement")
        native = _object(owner.get("native"), "native placement")
        offset = _uint(native.get("record_offset"), "parent record offset")
        if offset in owners:
            raise ValueError("duplicate native fixture parent record offset")
        owners[offset] = owner

    inventory = {}
    expected = {}
    for entry in _array(report.get("inventory"), "inventory"):
        entry = _object(entry, "inventory entry")
        parent_offset = _uint(entry.get("parent_offset"), "inventory parent offset")
        index = _uint(entry.get("fixture_index"), "inventory index")
        key = parent_offset, index
        if key in inventory or parent_offset not in owners:
            raise ValueError("duplicate or unknown fixture inventory parent/index")
        owner = owners[parent_offset]
        actor = _object(owner.get("archetype"), "parent archetype")
        identity = _resource(actor.get("resource"))
        if identity not in definitions or index >= len(definitions[identity]):
            raise ValueError("fixture inventory references an unknown native definition")
        if _hash(entry.get("actor_hash"), "inventory actor") != identity[1]:
            raise ValueError("fixture inventory actor does not match its parent")
        record = definitions[identity][index]
        if _hash(entry.get("tag_hash"), "inventory tag") != record[1]:
            raise ValueError("fixture inventory tag does not match its raw definition")
        selected, rendered = entry.get("selected"), entry.get("rendered")
        if not isinstance(selected, bool) or not isinstance(rendered, bool) or rendered and not selected:
            raise ValueError("fixture selected/rendered flags are inconsistent")
        if rendered and entry.get("status") != "decoded":
            raise ValueError("rendered fixture inventory is not decoded")
        inventory[key] = entry
        if rendered:
            expected.setdefault(parent_offset, set()).add(index)
    for offset, owner in owners.items():
        actor = owner.get("archetype")
        identity = _resource(actor.get("resource")) if isinstance(actor, dict) else None
        if identity in definitions:
            required = {(offset, index) for index in range(len(definitions[identity]))}
            if not required <= inventory.keys():
                raise ValueError("native fixture instance is missing from inventory")

    for offset, owner in owners.items():
        required = expected.get(offset, set())
        lods = _array(owner.get("lods"), "parent LODs")
        if required and not lods:
            raise ValueError("selected fixture parent has no LOD")
        for lod in lods:
            lod = _object(lod, "parent LOD")
            seen, part_keys = set(), set()
            for part in _array(lod.get("parts", []), "visual parts"):
                part = _object(part, "visual part")
                if "building_fixture" not in part:
                    continue
                fixture = _object(part["building_fixture"], "part evidence")
                index = _uint(fixture.get("index"), "part index")
                if index not in required:
                    raise ValueError("unexpected or unselected fixture visual part")
                entry = inventory[(offset, index)]
                identity = _resource(owner["archetype"]["resource"])
                record_offset, tag, visual, raw = definitions[identity][index]
                if (_uint(fixture.get("record_offset"), "part record offset") != record_offset
                    or _hash(fixture.get("tag_hash"), "part tag") != tag
                    or _hash(fixture.get("visual_hash"), "part visual") != visual
                    or _raw(fixture.get("raw_record_hex"), "part raw record", 0x64) != raw):
                    raise ValueError("fixture part identity does not match preserved native record")
                selection = _raw(fixture.get("raw_selection_table_hex"), "selection table")
                if len(selection) < 4:
                    raise ValueError("fixture selection table is truncated")
                count = struct.unpack_from("<I", selection)[0]
                if len(selection) != 4 + count * 4:
                    raise ValueError("fixture selection table byte count is inconsistent")
                tags = struct.unpack_from(f"<{count}I", selection, 4)
                if len(set(tags)) != len(tags) or tag not in tags:
                    raise ValueError("rendered fixture tag is absent or duplicated in native selection")
                authored_selected = {i["tag_hash"] for (p, _), i in inventory.items() if p == offset and i["selected"]}
                if set(tags) != authored_selected:
                    raise ValueError("fixture selection table and selected inventory disagree")
                _uint(fixture.get("selection_pointer_field"), "selection pointer field")
                _uint(fixture.get("selection_table_offset"), "selection table offset")
                _uint(fixture.get("selected_lod_index"), "selected LOD index")
                part_key = (index, part.get("geometry"), part.get("bone_index"), part.get("table_offset"))
                if part_key in part_keys:
                    raise ValueError("duplicate fixture visual part in parent LOD")
                part_keys.add(part_key)
                seen.add(index)
            if seen != required:
                raise ValueError("parent LOD is missing a selected fixture visual")
    rendered_count = sum(len(indices) for indices in expected.values())
    if _uint(report.get("selected_fixture_count"), "reported selected count") != rendered_count:
        raise ValueError("fixture selected count does not match rendered inventory")
    counts = _object(manifest.get("counts"), "manifest counts")
    if _uint(counts.get("selected_building_fixtures"), "manifest selected count") != rendered_count:
        raise ValueError("manifest fixture count does not match rendered inventory")
    if (manifest.get("profile_id") == "dah1-retail-xbox-us" and manifest.get("map_name") == "area42"
        and rendered_count != 76):
        raise ValueError("Area42 native selected fixture count must be 76")
