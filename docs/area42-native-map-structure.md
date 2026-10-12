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
original authoring transform again. Initial actor and sky materials have a different
contract: their loader at `0xE60A0` resolves texture handles without rewriting the
matrix, and `0xE5F30` copies it when cloning. Their original eight UV-transform
floats therefore remain effective at initial load. Animation and script updates
(`0xE5970`), shared-material changes and shader paths outside the verified basic
subset remain explicit unknowns; this is not a claim that UVs never change in-game.

## Original textures, mip levels and material dependencies

The preview uses original retail texture bytes, not AI-generated assets or
upscaled replacements. Every supported mip level is decoded independently from
its authored byte span; the highest native level is never replaced by a reduced
preview. PNG conversion does not resize pixels or generate missing mip levels.
Supported formats are BC1, BC3, swizzled ARGB8, linear ARGB8 and linear ARGB4444.
Alpha is preserved at each level. Partial native chains remain partial.

A read-only audit of all material resources in the Area 42 load groups found
624 unique primary textures and 14 additional secondary-only textures: 638 in
total. Slots 0 and 1 have 1,948 and 166 bound occurrences respectively; slots 2
and 3 are unbound. These are full resource-inventory counts, not a claim that
every asset is drawn simultaneously or used by the exported scene. The largest
ordinary game textures observed are 256 by 256; six 640 by 480 linear images
are separate full-screen assets, not higher-resolution terrain replacements.

All four material pointers at `+0x6C/+0x70/+0x74/+0x78` are resolved and retained
in `materials[].texture_stages`, including null slots, pointer-field offsets and
binding/texture provenance. `texture_dependency_report` counts the dependencies
actually included in each export. Secondary textures are preserved even where
their reflective/special shader sampling is not yet reproduced; resolving an
asset is distinct from implementing its renderer.

`textures[].preview.native_mips` records each PNG's level, dimensions and hash,
alongside its original byte offset, length and hash. Level zero is the same file
as the legacy `preview.file`. Validation checks the authored level count,
dimension progression, contiguous byte spans and output hashes. The UI uploads
these levels directly without automatic mip generation.

`materials[].native_sampler` carries the verified stage-zero sampler: linear
minification, normally linear magnification and linear mip filtering, with the
original binding's LOD bias. Flag `0x100000` instead selects nearest magnification
and disables mip filtering; single-level textures also disable mip filtering.
The native U/V clamp flags remain distinct from wrapping. These contracts are
traced through `0xE6692`, `0xE66A7` and `0xE5CDC`, not chosen as a visual fix.

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

## Environment: authored preview and runtime limits

The following read-only resource and executable evidence now supports the first
native sky visual and a restricted authored-sun preview for basic rigid shaders.
It does not establish same-camera retail parity or a complete atmosphere model.
Fog remains decoded evidence, not a generic radial effect applied by the editor.
All addresses are for the pinned DAH1 US Xbox retail executable; resident offsets
refer to decompressed Area 42 load group 0. The fog/sky sibling nodes are not
ordinary scenery placements.

### Native ownership and authored values

World record `0x7FF9A4` (type `0x59CE7BCB`, name `0xB31C6654`) has these
relocated fields. The loader at `0xE5670` follows the same references:

| World field | Target | Role |
| --- | --- | --- |
| `+0x0C` | node `0x7FFB14` -> payload `0x8D1324` | `sun_shadow` |
| `+0x10` | node `0x7FFA94` -> payload `0x8D14DC` | `sun_static` |
| `+0x14` | node `0x7FFAD4` -> payload `0x8D1400` | `sun_dynamic` |
| `+0x18` | payload `0x8D1680`, type/name `0x6FF6291D` | fog |
| `+0x1C` | controller `0x8D15B8`, type/name `0x958378DE` | sky |

These payloads are not independently indexed directory resources. Bounds and
relocations must still be checked. The `Fog` and `skybox` sibling nodes have no
relocated visual at `+0x14`; dropping them as empty objects misses World's direct
environment ownership. The three sun payloads have type `0x438B6A74`;
`0xE9800` registers its factory and `0xE4DB0` copies payload `+0xB4..+0xD8`
into runtime `+0x1C..+0x40`. The consumer at `0xF7C90` and constant uploads at
`0xEB0C3..0xEB10C` identify payload `+0xB4` as directional diffuse color and
`+0xCC` as ambient color. Payload `+0xC0` is not the effective direction: the
sun-node update replaces it with the node's transformed positive Z basis.
The `+0xD8` tail remains uninterpreted.

`environment.lighting_contract` preserves separate static and dynamic authored
lights. The owning visual's `+0x9C` bit 4 selects the dynamic role; otherwise
the native instance loader selects static. Attached components inherit that
selection. For the verified basic rigid shader and rigid unit-scale bases, the
preview uses `max(dot(normal,direction),0) * diffuse + ambient`, followed by
material diffuse/additive colors and the basic pixel shader. It does not add
directional lighting to terrain's already-authored packed vertex colors.

The default untinted draw has shader constants `c20=(1,1,1,1)` and
`c19=(0,0,0,0)`; per-object effects can replace them. Camera updates also
overwrite the sun direction using an interpolated camera-light quaternion.
Thus authored sun data is a reproducible preview basis, not the game's live
lighting state. Skin, non-rigid and reflective/special shader paths retain
explicit preview fallbacks.

Fog loader `0xF3630` establishes the following fields:

| Payload field | Area 42 value | Runtime field |
| --- | --- | --- |
| `+0xE0..+0xE8` | RGB `(0.568627, 0.541176, 0.478431)` | `+0x1C..+0x24` |
| `+0xB4` | extent `150` | `+0x90` |
| `+0xB8..+0xC0` | position `(0,-150,0)` | `+0x28..+0x30` |
| `+0xC4..+0xD0` | quaternion `(-0.7071067,-0,-0,0.7071068)` | `+0x34..+0x40` |
| `+0xD4..+0xDC` | scale `(1,1,1)` | `+0x44..+0x4C` |

The color meaning is independently supported by the registered
`graphics.fog.SetColor` function at `0xE3CB0`, which writes the same runtime RGB
fields. The full 236-byte fog payload has SHA-256
`6c95a62d902b582ffc7672c6489ebe5206c8b77b43211d47917a8ebdbb24bb99`.

### Fog is a transformed linear plane, not radial distance

`0xF3710` builds the fog matrix from its quaternion, position and scale,
multiplies its Z basis by the extent, and composes it with the supplied object's
matrix. `0xF55E0` then computes a per-draw plane in the rendered object's local
space. With row-vector notation, `T = fog_matrix * inverse(object_matrix)`,
`z = T[2,0:3]`, and `o = T[3,0:3]`, the calculation is:

`n = z / dot(z,z); c56 = (n.x,n.y,n.z,-dot(o,n))`

The helper at `0xD4EA0` returns **squared** length; there is no square root in
this calculation. Terrain calls it at `0xECB15`, `0xEB1D2` uploads constant 56,
and the native terrain vertex program writes `oFog = dot(position,c56)`.
This is an affine plane coordinate, not `length(position-camera_position)`.
With the authored fog matrix and identity parent/object transforms, points at
Y `0,-75,-150` produce approximately `1,0.5,0` respectively. This numerical
check is not a claim that gameplay keeps that identity basis or authored extent.

The normal renderer initialization sets the D3D fog mode field to zero
(`0xE620A`). For that mode, the pinned D3D routine `0x1E2ACF..0x1E2B99` emits
NV2A LINEAR (`0x2601`) and parameters `(1,1,0)`. The linear hardware equation
`parameter0 + oFog * parameter1 - 1` therefore passes the plane coordinate
through. Do not apply a second generic distance-fog curve. Exact per-pass
combiner behavior, camera attachment/update selection and framebuffer parity
remain separate validation gates.

### Camera and script overrides

Area 42's `input/blocks/sites/area42/site_localsettings.lua` (Lua resource
index 37, load group 1, offset `190916`, size `421`) overrides the shared
defaults with:

| Camera configuration | Fog start | Fog end | Far plane |
| --- | ---: | ---: | ---: |
| Alien/on foot | 0 | 250 | 1000 |
| UFO | 60 | 250 | 250 |

The shared `site_camera.lua` resource (load group 1, offset `908868`, size
`1488`) maps these values in `siteCamera.Init` to `player.SetParam` keys
`crFogStart`, `crFogEnd`, `shipFogStart`, `shipFogEnd`, `crCamFarDist` and
`shipCamFarDist`. The independently identified `graphics.fog.SetRange` at
`0xE3D00` stores position `(0,-end,0)` and extent `end-start`.
The player setters at `0x828C5/0x82AF8/0x82972/0x831E2` feed camera config
`+0x58/+0x5C`; `0x7BCBC` transfers these to interpolators `+0x13C/+0x154`.
The camera update at `0x7C08F` writes current interpolated endpoints into fog
position/extent. `World.Update` at `0xE5120` supplies the graphics-global
`+0xEC` parent matrix. These endpoints are not unconditional editor settings:
live parent selection, transitions, cinematics and scripted overrides remain
unimplemented. `dah1/environment.py` decodes the actual Lua instructions and
native resources, with pinned executable spans and changed-input tests.

Area 42 ignition also calls `environment.SetParam(0,"LightLevel",1)` at Lua
PCs 438-443. The XBE dispatcher at `0x5A6A0` recognizes hash `0x53C40C0E`
and stores that state's light level. This is evidence of an additional runtime
control, not permission to multiply every preview material by a guessed light.

### Sky geometry has a dedicated draw contract

The sky controller loader `0xF38C0` resolves payload `+0xB4/+0xB8` into
runtime `+0x1C/+0x2C`. Both targets are native static visuals (`0x9805A0A6`):

| Visual name / resident offset | Mesh name | Geometry |
| --- | --- | --- |
| `0x741C438A` / `0x7E0378` | `0x7AD90718` | 106 vertices, 128 triangles, two segments/materials |
| `0x0C9DF14A` / `0x7E0610` | `0xFC7BD8FF` | four vertices, two triangles, one segment/material |

These counts were independently decoded through the native mesh/material
reader, not inferred from a picture. The sky controller's vtable at `0x234890`
selects `0xE7150` for drawing. That routine copies the view matrix, zeroes its
translation (`0xE720B..0xE721D`), iterates the first visual's mesh segments and
material slots (`0xE7372..0xE74DE`), then conditionally draws the second visual
through `0xEF890` at `0xE77E6` with a separately constructed transform. Its
placement depends on runtime camera values and the controller's vector; it is
not a second ordinary static placement. Exact second-pass scaling, occlusion,
material state and compositing remain to be validated before preview support.

The first visual is now exported in `sky_placements` with its original mesh,
materials and UV matrix, including the native 4x scale / -1.5 offset where
present. `environment.sky.draw_contract` records its camera-relative draw:
remove view translation, retain the real projected depth, use LEQUAL with depth
writes disabled, and draw before the native world geometry. This is not a
replacement sphere or an artificial far-plane projection. The first visual's
zero diffuse color reduces its initial vertex color to the native material's
additive color; shader 0 then applies `clamp(2 * texture.rgb * vertex.rgb)`.
Opaque/additive pass state and GREATER alpha tests are retained per segment.

The second visual is preserved as a dependency, not fabricated as another sky
placement. Its camera-far scaling and occlusion-driven opacity remain open.
Matched retail-camera tests in alien and UFO modes are still required to validate
live sky, lighting and fog together. Useful executable span SHA-256 pins:

| Start / byte count | Evidence | SHA-256 |
| --- | --- | --- |
| `0xF55E0` / 288 | fog plane calculation | `dfe0935d7f14f4ee2246ebc842dc971a192b3d5ff92e63f9c2af40873389657d` |
| `0xF3710` / 240 | fog matrix update | `c3408be9b72303f5caaa33466aba6bbc832928bbcaf0c4b7771d20dd4762afa5` |
| `0xF38C0` / 82 | sky dependency loader | `f494c062bf5b530919890e54afb036231562ff830aa4b77e0bb974115dfb4e80` |
| `0xE7150` / 1724 | dedicated sky draw | `392939c5bcc47a72261132779bfd011d786c8c04d24ddf9741aeffa04af36d61` |

## Selected building fixtures and laboratory interior

The primary building visual is not the whole intact building. Retail `0x446E0`
walks `WorldObjectType+0x84` as a counted array of 100-byte fixture records.
Each record contains a selection tag, a **hashed** visual reference, local
position/quaternion and a block attachment. These are not relocation pointers
to the primary actor visual. `0x4B390` resolves the visual hash; `0x4B1C0`
composes its local transform with its owning block. `0x4A9E0` initializes the
selected fixture to its undamaged state.

Placement `+0x4C` bit `0x400` enables a selection list reached through variable
parameters at `+0x50` and `0x10DCA0`. `0x447C0` filters fixture tags against that
list. Exporting every archetype fixture would incorrectly add unselected
hangar/tent variants. The reader preserves raw records, variable-parameter
flags/offsets, selection bytes, actor bytes and relocations.

The retail reconstruction yields 18 building archetypes, 33 fixture definitions,
81 potential fixture instances, and **76 selected fixtures in 61 placed
buildings**. Five instances are unselected; every selected fixture resolves.
All current fixture owners have a single identity base block, separate visuals
have bone index `-1`, and placed owner scales are one. Unsupported attachment
cases are reported rather than guessed.

For `b_sml_majestic_hq_03`, placement `world-placement:00:0624` selects both:

| Fixture tag | Native visual hash | LOD0 mesh | Triangles |
| --- | --- | --- | ---: |
| `0xCCC50364` | `0x595EF2B4` | `0x93AC167F` | 1,424 |
| `0xB46D85C5` | `0xACF94D6F` | `0x5A5B80C5` | 230 |

The second hash matches `b_sml_majestic_hq_03_fixture`; no source name is
invented for the first. Both are attached under the existing building using
`component_matrix * fixture_local_matrix * placed_building_matrix`.
Block alternate-state visual `0x800E7FC1` is preserved as destroyed-state
evidence and **not rendered** as intact architecture.

Fixtures add 48 unique meshes and 13,789 LOD0 triangles across Area 42. They
remain child components, not fabricated extra world-database placements.
Higher inspection LOD indices clamp each fixture's independent chain; this
is explicitly not the game's distance-threshold selection. Interiors still
need a matched retail-camera comparison, and destruction is not simulated.

## Viewport overlay and actor raster parity

The map-wide grid was the editor's surface-grid overlay, not texture data.
Native reference documents now start with that overlay hidden. The normal
Show Grid action remains available, grid snapping is unchanged, and ordinary
brush documents retain their existing defaults.

Ordinary actor pass routing (`0xEA050`) and raster setup establish additive and
subtractive material flags, GREATER alpha testing (including opaque actors),
LEQUAL depth testing and disabled depth writes for blended draws. This pass
state is now carried separately as `actor_render_state`; it is applied to
rigid/skin actor geometry, never substituted for terrain or scatter state.
The autopsy table, Tesla coils and Crypto cages include native additive glow
materials. In the pre-fixture actor inventory, 46 special-blend materials
affect 330 placements, and ten non-unit-opacity materials affect 100 placements.
Basic rigid shader opacity is applied without assuming the same shader path
for skins or the reflective material family.

`actor_material` now carries initial effective UVs, original material colors,
opacity and pixel-shader identity independently from raster state. For the
verified rigid subset, authored lighting is evaluated before interpolation and
the native basic shader's RGB factor of two is applied after texture sampling;
it is not a global brightness multiplier on white vertices. Blended actor
batches are sorted by placement/batch center from the current camera, not by
every transparent triangle or the game's complete draw queue.

Runtime UV/color animation, camera-driven sun changes, reflective/special
shaders and their secondary texture sampling, per-draw cull orientation,
transparent intersections, gameplay fades, fog application and shadow passes
remain unresolved. Landing visuals still lack runtime collision projection,
and authored entities do not imply mission activation. The first sky and
authored-sun preview narrow the gap, but a textured reference remains a research
preview, not verified framebuffer or gameplay parity.

