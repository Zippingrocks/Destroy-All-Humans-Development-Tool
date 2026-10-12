# DAH editor scene v3

Historical v3 contract, with the current v4 additions below. New exports write
schema v4; CryptoEditor retains v2/v3 loading. The historical 849-object export
was not the full retail world database.

Schema v3 extends the read-only native interchange with static scatter, human
reference meshes and landing/spawn evidence. The exporter writes a new directory;
the retail packages and prior exports remain intact.

## Collections and identities

`placements` contains the same 92 terrain placements and both native LODs.
Historical `object_placements` contains 849 decoded world records. Human visuals attach
to the existing 295 explicit human records; population spawns do not become humans.
Buildings and PropType set pieces remain 18 and 23 renderable placements respectively.

`scatter_batches` contains 654 native resources, original descriptor/scene-node
bytes and hash-verified 80-byte instance buffers. `scatter_placements` addresses
each of their 15,121 native records exactly once. Each placement has the native
RGB and a row-vector world matrix derived by transposing the native instance
matrix then composing it with the containing scene node. Five shared templates
preserve the complete eight-copy native GPU buffers as well as the deduplicated
preview template. There are 11,515 foliage instances and 3,606 rocks, classified
by observed source textures; original authoring names remain unresolved.

`human_archetypes` preserves each selected clothing variant, LOD pointer and
skeleton's raw shared header and 56-byte bones. Geometry entries retain all three
LODs (15 unique human meshes), original 40-byte vertices, 0x8E-byte batches, bone
palettes, weights and indices. `preview_status` states that animation is not applied.

`scene_markers` contains 516 editor annotations: three landing-region centers,
139 population spawn positions and 374 other authored entities without a mesh
preview. Each points to native provenance; world markers reference existing world
records, not extra entities. Landing sites are linked through actual Lua calls,
native hashes and region-table entries. Runtime landing height is terrain queried
and is not substituted for the authored region center.

`spawn_definitions` retains three cumulative-probability RandomType definitions
and four directed-spawn tables. `spawn_layers`, `regions`, `entity_tags`, and
`script_calls` retain all 41 layers, 92 regions, 135 tag arrays and Lua call evidence.
Authored data does not imply that all mission layers are active simultaneously.

## Geometry and materials

| Geometry kind | Stride | Position | UV | Normal/color |
| --- | ---: | ---: | ---: | --- |
| `terrain` | 24 | 0 | 12 | native D3DCOLOR at 20 |
| `static-world-object` | 28 | 0 | 12 | snorm16x4 at 20 |
| `static-scatter` | 24 | 0 | 12 | per-instance RGB; float constant selector at 20 |
| `human-reference-pose` | 40 | 0 | 32 | float3 normal at 12; weight/index bytes at 24/28 |

All geometries carry native buffer views and hashes; derived triangle lists are
decoded from checked strip/batch ranges. Scatter's synthetic single segment is
explicitly labeled because its native material belongs to the batch resource.

Material `render_state` records texture alpha mode, opacity, culling flag,
alpha comparison/reference and blend/depth state with executable addresses.
CryptoEditor applies supported verified alpha states; exceptional paths remain
explicitly unresolved. Native culling flags are retained, but culling stays disabled
until front-face parity is verified. Wind, distance fading, individual translucent
instance sorting, human animation and held attachments remain preview limitations.

## Verification

The historical v3 export contains 223 geometries, 227 materials and 153 textures. Existing
terrain reference arrays must remain byte-identical; source package hashes are
checked before and after export. Validation additionally checks scatter instance
coverage and buffers, finite transforms/markers, human skeleton/raw-byte agreement,
resource links, geometry references and collection counts. Fixture counts are
acceptance checks applied after structural decoding, never parsing instructions.

Native save/edit remains gated by unchanged-package round-trip verification.

## Schema v4 (current)

Version 4 preserves both native counted instance lists at bucket `+0x0C` and
`+0x10`, and all four packed signed-16 quaternion components. Empty lists do not
imply an inline placement. There are 2,674 unique world records across 41 buckets
and 150 archetypes: 808 records from `+0x0C`, 1,866 from `+0x10`.

- `object_placements[].native` adds `list_pointer_field`, `list_offset`,
  `list_index` and `packed_quaternion_xy`. Native record offsets and raw headers
  remain authoritative; `staging.native_lists` accounts for bucket/list coverage.
- Rigid `lods[].parts` lists every component, each with geometry, material slots,
  component-to-actor matrix, bone index and raw attachment-table provenance.
  The legacy LOD-level geometry/material pair repeats the first part for
  compatibility; it is not an extra component. Segmented keys are bone indices,
  not destruction-state choices. `actor_visual_skeletons` preserves their bones.
- `actor_visual_inventory` covers all 150 placed archetypes: 132 decoded rigid
  families / 2,164 placements, 15 pedestrian skin archetypes / 371 placements,
  and three RandomType definitions / 139 population positions. Exactly 2,535
  fixed actor records have previews, including 79 building-group placements and
  1,468 props. The building grouping includes native `b_` rock-pillar assets.
- `landing_placements` contains three script-selected MarkerType visuals, each
  with the genuine 11-part mesh, 17 materials and five textures. It links existing
  region markers rather than adding world-database actors. `landing_skeletons`
  and `landing_visual_validation` retain the chain and runtime limitations.
- `scene_markers` now contains 142 annotations: three landing regions and 139
  RandomType positions. Rendered actor records do not receive duplicate markers.
  The lower-level `spawn_validation` reports candidates before that filtering.

The verified full fixture export contains 592 geometries, 695 materials and 331
textures. Static mesh descriptor `+0x18` is a signed 16-bit segment count;
the adjacent upper word is preserved, not combined into a 32-bit count.

Terrain segment render state now retains the signed high-byte layer/sort value,
native vertex alpha, verified basic RGB-times-two shader operation, LEQUAL depth
and applicable ONE/ONE reverse-subtraction (`dst - src`) for detail materials.
Compiled terrain UVs use the executable's initial identity transform; retained
authoring matrices must not be applied again. Material evidence is per path,
not a claim that every native shader has been reproduced.

`validate_complete_scene()` checks unique native offsets, raw-header hashes,
list coverage, inventory status/count agreement, component matrices and all
geometry/material references, buffer bounds/hashes, vertex/triangle ranges and
landing-region linkage. Area42-specific counts are acceptance gates after
structural decoding, never instructions for manufacturing missing data.

The scene intentionally exposes all authored mission content. Runtime activation,
component animation/visibility, collision-projected landing Z, fog and full
shadow/lighting parity remain unresolved. Render-code evidence and a textured
viewport do not establish same-camera retail fidelity or native save support.
