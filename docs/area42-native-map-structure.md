# Area 42 native map structure

This document records evidence from the pinned DAH1 US Xbox retail build. It is the
foundation for CryptoEditor's document model: editor conveniences may be layered on top,
but the authoritative scene remains Pandemic's native resources and relationships.

## World placement database

Area 42 contains resource `(type 0xFE55FFAF, name 0x59CE7BCB)`, identified by the
prototype linker report as `world.instance-database`. The retail resource contains 41
buckets and 2,674 placement records across 150 archetypes.

Each bucket preserves its native layer hash and has two independently relocated
count-prefixed pointer lists, at `+0x0C` and `+0x10`. They contain 808 and 1,866
records respectively. Their runtime purposes are not inferred from editor categories.
The historical v2/v3 reader followed only one list and treated some empty buckets
as inline records, producing an incomplete 849-record scene. Empty lists are now
empty; both populated lists are traversed with unique-record and bounds checks.
A placement record has these structurally verified fields:

| Field | Meaning | Evidence |
| --- | --- | --- |
| `+0x10` | position X/Y/Z, float32 | Coherent map-space positions; buildings sit at the Area 42 ground elevation |
| `+0x1C` / `+0x20` | packed X/Y and Z/W quaternion, signed 16-bit pairs | All four components retained; pitched/rolled props cannot be reduced to yaw |
| `+0x24` | uniform scale, float32 | Native per-record value retained |
| `+0x3C` | relocated actor-archetype pointer | Resolves to type `0x1D9D48EC` for all decoded placements |

The reader preserves the complete first 64 bytes of every record, its source offset,
bucket identity, list field/offset/index, and hashes. Fields without proven semantics remain native data rather
than being flattened into editor properties.

Actor archetype records carry their native class and asset name as embedded strings. The
2,674 Area 42 placements resolve to 150 archetypes, including pedestrians, vehicles, props,
mines, random population definitions, and world objects. CryptoEditor derives friendly
collections from these names while retaining the original class and resource identity.

## Buildings in the retail map

The instance database contains 79 placements in the reversible building collection:

| Archetype | Count |
| --- | ---: |
| `b_lrg_comms_tower` | 1 |
| `b_lrg_control_tower` | 1 |
| `b_lrg_radar_shack` | 1 |
| `b_lrg_watertower` | 1 |
| `b_sml_barracks_small` | 18 |
| `b_sml_barracks_large` | 7 |
| `b_sml_demountable_office_day` | 11 |
| `b_sml_fuel_dump` | 3 |
| `b_sml_hanger` | 3 |
| `b_sml_military_hq` | 1 |
| `b_sml_majestic_hq_01` | 1 |
| `b_sml_majestic_hq_02` | 2 |
| `b_sml_majestic_hq_03` | 1 |
| `b_sml_rockpillar_01` | 2 |
| `b_sml_rockpillar_05` | 4 |
| `b_sml_satellite_dish` | 4 |
| `b_sml_science_tent_l_01` | 2 |
| `b_sml_storage_tank_day` | 2 |
| `e_guard_tower` | 14 |

This grouping follows native `b_` names plus guard towers, so it includes the
six rock-pillar placements; it is not a claim that every entry is a human building.
All 79 building-group placements and 1,468 props resolve to native visual previews.
The missing Tesla coils, cages and UFO-remains records were in the omitted native
list, not evidence that the editor should invent script-spawn positions.

Across all actor classes, 132 rigid visual families account for 2,164 placements.
Another 15 skin archetypes account for 371 pedestrians, including 295 humans, 70 cows
and six powersuits. The remaining 139 records are RandomType population definitions,
not fixed models. These totals cover every world record without duplicate actors.

The proven render dependency chain is:

`instance placement -> actor archetype -> LOD -> visual -> static mesh -> segments -> material slots -> textures`

Static meshes use a 32-byte descriptor (`0x5B99558D`), stride-28 vertices, uint16
triangle strips, and the same 12-byte segment record shape seen in terrain. The vertex
layout is position float3 at `+0x00`, UV float2 at `+0x0C`, and signed-normal components
at `+0x14`. Material tables (`0x48C2FD5F`) preserve their complete slot order even when a
mesh does not consume every slot.

Descriptor `+0x18` is a signed 16-bit segment count; its neighboring upper word
is not part of that count. Reading a 32-bit count incorrectly rejected legitimate
assets. Negative counts and invalid buffer spans remain errors.

Segmented visual type `0x2C84BEFF` contains `(bone index, relocated static visual)`
pairs at `+0xC4`, counted at `+0xC8`. They are simultaneous skeleton attachments,
not destruction-state alternatives. The shared block at `+0xC0` supplies skeleton
and materials (`+0x30/+0x34`). The retail draw path at `0xF7436` indexes the pose
node by that bone index; local transforms are composed with parents. Static visual
matrices at `+0x08` describe bounds and must not be applied as extra mesh transforms.
Runtime animation/component visibility and actual destruction behavior remain separate.

## Terrain color and texture appearance

The terrain's stride-24 vertex tail is no longer unknown. Retail executable routine
`0xECA10` creates this vertex declaration:

1. float3 position,
2. float2 texture coordinate,
3. D3DCOLOR.

This exactly accounts for bytes `0x00..0x17`, proving that terrain bytes `+0x14..+0x17`
are a packed `0xAARRGGBB` diffuse vertex color. CryptoEditor's earlier preview discarded
that channel and therefore could not reproduce the game's per-vertex modulation. Schema
v2 preserved RGB modulation. The v4 evidence now also establishes native vertex
alpha and the basic textured pixel equation `clamp(2 * texture.rgb * vertex.rgb)`;
alpha is `texture.a * vertex.a`. These basic shaders do not add an arbitrary
material-opacity multiplier to the pixel result.

This is modulation of one native material binding per terrain segment, not evidence for a
guessed multi-texture splat system. The 91 terrain material records contain 87 direct
texture bindings at `+0x6C` and four without bindings; no additional direct texture
pointers were found in those records. Texture stages 0, 2, and 3 observed in sampler setup
are render-state evidence, not proof of three independent terrain texture layers.

The segment low byte selects a material; its signed high byte participates in
layer sorting and a nonzero value forces a blended pass (`0xECB92`, `0xECC10`).
Alpha testing uses GREATER with its native reference; depth comparison is LEQUAL
(`0xE61C7`). The blend path at `0xEB340` distinguishes ADD, REVERSE_SUBTRACT and
SUBTRACT; the `0x200` detail/shadow-material case uses ONE/ONE, `dst - src`.
The applicable state belongs to each segment, not merely its material.

The initial terrain material walk resets effective UV transforms to identity
(`0xE5790 -> 0xE52C0 -> 0xE5A40`, scale 1,1). Compiled UVs must not receive the
original authoring transform again. Non-terrain matrix updates, runtime scale
callbacks and shader paths outside the verified basic subset remain explicit unknowns.

## CryptoEditor model

Schema v4 separates:

- 92 terrain placements and their two native LODs;
- 2,674 world-instance placements in both lists of 41 native buckets;
- actor archetype metadata for every placement;
- 2,535 fixed actor previews, preserving every decoded rigid component and skin;
- friendly editor collections that do not replace native class identity.
- 654 native scatter batches / 15,121 instances outside the world database;
- 295 authored human placements with their three LODs and reference-pose skin data;
- three landing-region visuals with their original 11 components, 17 materials
  and five textures; runtime collision-adjusted height is not yet reproduced;
- random population definitions and directed spawn recipes, with 139 population
  markers and explicit mission activation uncertainty.

The `ActorTypePedestrian` class includes cows and powersuits. The `h_` prefix
also occurs on `RandomType` definitions. Classification now respects the native
class before applying friendly editor groupings; random choices are never turned
into fixed human placements. See [humans](area42-humans.md),
[scatter](area42-scatter.md), and [spawn evidence](area42-spawns.md).

Folders, layers, brush construction, procedural placement, and other authoring tools may
be added above this model. Export adapters must deliberately compile those conveniences
back to native structures. They must not silently substitute a heightmap, brush model, or
TrenchBroom entity for a Pandemic resource whose runtime contract is different.

## Remaining research gates

- Reconstruct runtime component visibility, destruction transitions, human animation
  and held attachments beyond the current authored reference meshes.
- Resolve collision ownership and links instead of deriving collision from render meshes.
- Verify the remaining special material, fog, shadow and lighting paths against
  same-camera retail images; decoded base states alone do not establish visual parity.
- Extend decoded mission-layer, region and tag links to mission execution,
  navigation, roads, streaming, and load-group behavior.
- Prove an unchanged native save before enabling package mutation.

