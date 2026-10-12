# DAH1 US Xbox Area 42: native foliage and static scatter

The retail resident scene contains **654 static-scatter batch placements**,
holding **15,121 instances** of **five native mesh templates**. Reconstructing
their triangles adds **240,610 triangles** to the scene. Four templates show
foliage in their decoded retail textures; the fifth shows rock material. The
decoder preserves native identifiers and does not manufacture authoring names.

This is the resident renderer's separate scatter system. These instances are
not `world.instance-database` actor placements, and the eight copies in each
GPU vertex buffer are draw batching slots rather than additional objects.

## Provenance and executable evidence

All offsets below are load-group offsets after applying the native relocation
table. Retail input is read-only. `read_scatter_scene()` accepts an existing
`TerrainScene`/`BlockReader`, or the source block directory. The scene's ordinary
typed sibling chain reaches every one of the 654 resources. No fixture count
is used to find resources, pick vertices, or construct transforms.

The executable evidence is pinned to DAH1 US Xbox retail SHA-256
`b9491b30eaee82af6c805d9b0036ec1119059b0660a0923803ebdb6219fa02eb`.
`verify_scatter_executable_evidence()` independently checks the bytes used for
the loader, draw batching, vertex stride, alpha state, and depth state.

| Retail code | Observed operation |
| --- | --- |
| `0xE1440` | Registers payload type `0x43BBDAB4` with constructor `0xE0CF0`. |
| `0xE0CF0` | Creates renderer with vtable `0x2340C8`. |
| `0xE8C40` | Vtable load routine reads native `+0xC0/+0xC4`, `+0xCC/+0xD0`, and the two 16-bit counts at `+0xC8/+0xCA`. |
| `0xE852E` | Resolves the material at native `+0xB4`; copies `+0xB8/+0xBC` for distance handling. |
| `0xE87C6` | Sets vertex stride to 24 bytes. |
| `0xE8940` | Limits each draw to eight instances. |
| `0xE89D8` | Multiplies instance index by 80 bytes. |
| `0xE8A49` | Uploads three float4 constants from each instance's matrix. |
| `0xE8A5C` | Uses the float4 constant at instance `+0x40` for color. RGB is retained; its final dword is not treated as alpha. |
| `0xE8B17` | Draw vertex count is `templateVertices * instances`; index count is `(templateIndices + 2) * instances - 2`. |

Directory type `0xA3C936AF` and its payload renderer type `0x43BBDAB4` are both
retained. Confusing them prevents finding the native renderer in the executable.

## Resource and vertex layouts

The batch descriptor is 228 bytes (`0xE4`). It retains its complete raw bytes,
directory identity, load group, source offsets, and source template hash.

| Resource field | Meaning |
| --- | --- |
| `+0x00` | Payload renderer type `0x43BBDAB4`. |
| `+0x04` | Source template name hash. Original strings remain unresolved. |
| `+0xB4` | Relocated pointer to a native `0x9709513E` material. |
| `+0xB8`, `+0xBC` | Distance/fade parameters. The fixture uses 30 and 40. |
| `+0xC0` | Number of instance records. |
| `+0xC4` | Relocated pointer to 80-byte instance records. |
| `+0xC8`, `+0xCA` | Template vertex count and template strip-index count, each u16. |
| `+0xCC` | Relocated vertex buffer for eight native draw copies. |
| `+0xD0` | Relocated strip buffer for eight copies with degenerate connectors. |
| `+0xD4..+0xE3` | Four retained floats with unresolved meaning. |

A template vertex is float3 position at `+0`, float2 UV at `+12`, and a float
constant selector at `+20`. The last value is **not a packed vertex color**.
Native draw copies contain identical positions and UVs, while their selector is
0, 4, 8, 12, 16, 20, 24, or 28. This selects each draw slot's three matrix
constants and one color constant. The decoder checks all copies independently
before exposing one template, and retains both full native draw buffers.

The first strip-index count belongs to the first template. Further draw copies
shift each index by the template vertex count and insert two degenerate joining
indices. Every native index is checked against this structure. The exporter can
reuse one template across thousands of instances without flattening the scene.

Each 80-byte instance record contains a column-vector affine 4x4 matrix at
`+0x00`, RGB floats at `+0x40`, and an unresolved dword at `+0x4C`. That dword
can look like a NaN when interpreted as a float; it is preserved as bits and is
not used as alpha. The matrix is transposed into the core's row-vector
convention and composed with the ordinary scene-node placement. The node's
position, quaternion, scale, flags, links, and complete 64-byte header are also
retained. Unverified parent hierarchies are rejected instead of silently ignored.

## Reconstructed templates

The visual descriptions below are observations of decoded source textures,
not recovered native asset names or species identifications.

| Source hash | Texture hash | Observed texture | Vertices / strip indices / triangles | Batches | Instances |
| --- | --- | --- | --- | ---: | ---: |
| `D799A82D` | `F1778599` | Grass-like foliage | 18 / 24 / 10 | 313 | 10,973 |
| `1CFE5FB9` | `B45CD522` | Branching shrub | 12 / 16 / 6 | 67 | 205 |
| `77FF9FDF` | `45D79D83` | Rock | 36 / 80 / 35 | 162 | 3,606 |
| `CD07E78A` | `6F4B7DB5` | Branching shrub | 14 / 18 / 8 | 75 | 213 |
| `53A937A8` | `FD51B2AF` | Spiky foliage | 21 / 32 / 14 | 37 | 124 |

That is 11,515 foliage instances and 3,606 rock instances. The resource type
remains static scatter for both; foliage is not incorrectly inferred from every
resource of this type.

## Materials, cutouts, blending, and depth

`material_render_state()` returns the executable-backed base state along with
the raw texture alpha mode and opacity. It marks exceptional blend/shadow flags
as unresolved rather than assigning them a conventional alpha material.

- Material `+0x7C` bit `0x4000` disables face culling (`0xE5C07` calls `0x1D8520`,
  writing NV097 cull enable `0x308`). It is a double-sided flag, not a mask flag.
- Texture binding `+0x10` value 2 selects masking (`0xE6310`); value 3 selects
  alpha blending (`0xE62E0`, routed by `0xEA010`).
- `0xE6224` sets the alpha comparison to `GREATER` (`0x204`).
- For masked textures, `0xE5D65` computes the reference as
  `clamp(256 - trunc(float32(opacity * 255)), 0, 255)`, from material `+0x68`.
- For normal blended materials, draw mode 6 enables blending and alpha testing
  and uses alpha reference 1 (`0xEAAE0` / `0xEABE3`). `0xEB438` uses the ADD
  equation with source-alpha and one-minus-source-alpha factors (ordinary alpha
  compositing, not ONE/ONE additive glow).
- Draw mode 6 sets the flags at draw-record `+0x1C` to 2 (`0xE9FAD` and its table
  at `0xEA000`). `0xEAF19` turns that into depth-write disabled. Opaque and masked
  draw mode 1 uses flags 0 and keeps depth writes enabled. Depth testing remains
  active with LEQUAL comparison (`0xE61C7`) in this ordinary path.

The v4 terrain renderer has additional segment-specific evidence, including the
signed high-byte layer and ONE/ONE reverse-subtract detail path. Those terrain
semantics are not inferred for the scatter descriptor's unrelated fields.

The NV097 constants above can also be checked against the local
`third_party/xboxrecomp/src/nv2a/nv2a_regs.h`; no different-game material behavior
is used as an assumption.

| Texture | Native alpha mode | Opacity | Alpha reference | Blending | Double-sided | Depth write |
| --- | ---: | ---: | ---: | --- | --- | --- |
| `F1778599` | 3 | 1.0 | 1 | src alpha / one minus src alpha | Yes | No |
| `B45CD522` | 2 | 1.0 | 1 | No | Yes | Yes |
| `45D79D83` | 0 | 1.0 | 64 | No | No | Yes |
| `6F4B7DB5` | 2 | 1.0 | 1 | No | Yes | Yes |
| `FD51B2AF` | 2 | 0.8 | 52 | No | Yes | Yes |

The normalized cutout threshold is the native reference divided by 255, with
an inclusive discard for `alpha <= reference`. A fixed 0.5 threshold is not
supported by this evidence. Texture decoding preserves the original BC3 alpha.

## Validation and remaining work

The focused tests cover native draw-copy deduplication, independent matrix
composition with rotation and translation, missing relocations, corrupt draw
selectors and index joins, invalid affine transforms, trailing unknown words,
and native alpha/depth state. Enable the optional retail test with
`DAHDEV_AREA42_SOURCE` pointing at the read-only block directory, then run:

```text
python -m unittest discover -s tests -p test_scatter.py -v
```

The fixture test confirms all 654 batches, 15,121 instances, five templates,
240,610 triangles, and five texture dependencies after performing the structural
checks. `scatter_report()` includes source-buffer digests and unresolved items.

The base geometry, original UVs, material dependencies, placement matrices,
instance RGB, and render state are decoded. Original authoring names, the
instance's trailing dword, and the descriptor's four final floats remain
unresolved. The runtime generates wind motion and computes camera-distance
fading; those effects are not recreated by a static terrain inspection scene.
Photometric parity with the complete native lighting/shader pipeline still
requires same-location retail comparisons. This work does not enable native
package mutation or prove a writer's unchanged-save round trip.
