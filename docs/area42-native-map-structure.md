# Area 42 native map structure

This document records evidence from the pinned DAH1 US Xbox retail build. It is the
foundation for CryptoEditor's document model: editor conveniences may be layered on top,
but the authoritative scene remains Pandemic's native resources and relationships.

## World placement database

Area 42 contains resource `(type 0xFE55FFAF, name 0x59CE7BCB)`, identified by the
prototype linker report as `world.instance-database`. The retail resource contains 41
buckets and 849 placement records.

Each bucket preserves its native type hash and points to an inline list of placement
records. A placement record has these structurally verified fields:

| Field | Meaning | Evidence |
| --- | --- | --- |
| `+0x10` | position X/Y/Z, float32 | Coherent map-space positions; buildings sit at the Area 42 ground elevation |
| `+0x20` | packed Z/W yaw quaternion, signed 16-bit pair | Identity `0x80000000`, half-turn `0x00008000`, and other values follow the quaternion sin/cos circle |
| `+0x24` | uniform scale, float32 | Area 42 placements resolve to the expected unit scale |
| `+0x3C` | relocated actor-archetype pointer | Resolves to type `0x1D9D48EC` for all decoded placements |

The reader preserves the complete first 64 bytes of every record, its source offset,
bucket identity, and hashes. Fields without proven semantics remain native data rather
than being flattened into editor properties.

Actor archetype records carry their native class and asset name as embedded strings. The
849 Area 42 placements resolve to 48 archetypes, including pedestrians, vehicles, props,
mines, random population definitions, and world objects. CryptoEditor derives friendly
collections from these names while retaining the original class and resource identity.

## Buildings in the retail map

The instance database contains 18 placed building-like world objects:

| Archetype | Count |
| --- | ---: |
| `b_lrg_comms_tower` | 1 |
| `b_lrg_control_tower` | 1 |
| `b_sml_barracks_small` | 5 |
| `b_sml_demountable_office_day` | 6 |
| `b_sml_fuel_dump` | 1 |
| `b_sml_satellite_dish` | 1 |
| `b_sml_storage_tank_day` | 2 |
| `e_guard_tower` | 1 |

Seventeen placements currently resolve through the complete render chain. The guard
tower is a segmented/destructible visual whose placement and archetype are preserved,
but whose shared material indirection is not yet promoted to renderable geometry.

The proven render dependency chain is:

`instance placement -> actor archetype -> LOD -> visual -> static mesh -> segments -> material slots -> textures`

Static meshes use a 32-byte descriptor (`0x5B99558D`), stride-28 vertices, uint16
triangle strips, and the same 12-byte segment record shape seen in terrain. The vertex
layout is position float3 at `+0x00`, UV float2 at `+0x0C`, and signed-normal components
at `+0x14`. Material tables (`0x48C2FD5F`) preserve their complete slot order even when a
mesh does not consume every slot.

## Terrain color and texture appearance

The terrain's stride-24 vertex tail is no longer unknown. Retail executable routine
`0xECA10` creates this vertex declaration:

1. float3 position,
2. float2 texture coordinate,
3. D3DCOLOR.

This exactly accounts for bytes `0x00..0x17`, proving that terrain bytes `+0x14..+0x17`
are a packed `0xAARRGGBB` diffuse vertex color. CryptoEditor's earlier preview discarded
that channel and therefore could not reproduce the game's per-vertex modulation. Schema
v2 preserves and renders RGB modulation. Alpha remains preserved but is not treated as
opacity until the remaining retail blend-state path is decoded.

This is modulation of one native material binding per terrain segment, not evidence for a
guessed multi-texture splat system. The 91 terrain material records contain 87 direct
texture bindings at `+0x6C` and four without bindings; no additional direct texture
pointers were found in those records. Texture stages 0, 2, and 3 observed in sampler setup
are render-state evidence, not proof of three independent terrain texture layers.

## CryptoEditor model

Schema v2 separates:

- 92 terrain placements and their two native LODs;
- 849 world-instance placements in 41 native buckets;
- actor archetype metadata for every placement;
- renderable building meshes, materials, textures, and native transforms;
- friendly editor collections that do not replace native class identity.

Folders, layers, brush construction, procedural placement, and other authoring tools may
be added above this model. Export adapters must deliberately compile those conveniences
back to native structures. They must not silently substitute a heightmap, brush model, or
TrenchBroom entity for a Pandemic resource whose runtime contract is different.

## Remaining research gates

- Decode the guard tower's segmented visual material ownership and all destruction states.
- Promote prop, vehicle, human, gameplay, and population visual families independently.
- Resolve collision ownership and links instead of deriving collision from render meshes.
- Decode remaining material blend, alpha-test, lighting, and texture-transform states.
- Map mission, region, navigation, roads, streaming, and load-group relationships.
- Prove an unchanged native save before enabling package mutation.

