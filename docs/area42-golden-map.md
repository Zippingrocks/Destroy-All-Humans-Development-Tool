# Area 42 golden map

Area 42 from the USA Xbox retail release of DAH1 is the first end-to-end test
map. The source directory contains:

| File | Bytes | SHA-256 |
| --- | ---: | --- |
| `area42.dir` | 431,188 | `15111978ec1d0d7770fe83ed6a3a6e55fff83363433d74354c849e0cd9832297` |
| `discard.pkg` | 303,112 | `ec108df4970b04e0d4645a636809c962869726dd272a5e87aa170eb333c53c20` |
| `resident.pkg` | 5,677,064 | `45f2e31ada7f17a55fdbf0db876c2f833992fe8c03425a89dc5d5609a9441a18` |
| `stream.pkg` | 27,367,432 | `7b75a2d0e816c26ab46c40ae3f41dcac5e5595f4f21d1956b26fe2aaf79d19d3` |

These hashes identify the fixture used for current research; they are not a
general disc-authenticity claim.

## Verified terrain facts

The corrected 2026-10-11 reference extraction establishes:

- 92 terrain placements in a 751-record scene sibling chain;
- 184 terrain geometry buffers, two ordered LOD children per placement;
- LOD0: 122,956 vertices and 103,634 triangles;
- LOD1: 16,240 vertices and 8,120 triangles;
- LOD0 bounds from `(-88, -72, -0.3925361633)` to
  `(912, 928, 86.0105743408)`;
- all selected terrain parent pointers are null;
- quaternion order is XYZW and the retail routine does not normalize it;
- row-vector matrix layout with translation in row 3 and scale applied to the
  upper 3x3 before translation;
- child 0 is the near LOD and child 1 is the far LOD.

The retail pointer graph and executable code support these facts. They are more
authoritative than the earlier hash-only selection and approximate OBJ export.

The repository's native reader now reconstructs every preserved reference array
byte-for-byte. It additionally establishes the following read-only dependency
facts from relocation and resource identity evidence:

- 1,257 material-slot occurrences across the 184 terrain LOD wrappers;
- the low byte of every segment's preserved flag word selects an in-range slot;
- every slot resolves to resource type `0x9709513E`;
- material relocation field `+0x6C` resolves to a 52-byte texture binding;
- binding field `+0x18` is the name hash of a `0x3BE99284` texture resource;
- Area 42 terrain uses 91 unique materials and 67 unique bound textures;
- binding format words 8 and 9 match BC1/DXT1 and BC3/DXT5 respectively for
  every native width, height, mip count, and payload length.

The pinned retail executable independently supports that traversal. Routine
`0xF0490` reads the wrapper's `+0xB8` count and indexes its `+0xB4` table;
routine `0xE67C1` reads binding `+0x18` and requests resource type
`0x3BE99284`; routine `0xE64F0` uses binding `+0x00` as a format-table index and
passes `+0x04`, `+0x08`, and `+0x0C` into texture creation. Those fields match
mip count, width, and height across all terrain bindings. The evidence XBE is
SHA-256 `b9491b30eaee82af6c805d9b0036ec1119059b0660a0923803ebdb6219fa02eb`.

The reproducible `dahdev executable-evidence` probe additionally verifies:

- the native terrain draw path writes stream-0 stride `0x18` (24 bytes);
- the terrain texture-scale path applies a float2 affine transform and terrain
  vertex construction stores that pair at byte `+0x0C`, confirming the native
  `+0x0C..+0x13` field as UV;
- material field `+0x7C` bit `0x1000` selects U clamp and bit `0x2000` selects
  V clamp; each unset axis wraps;
- the engine applies those address modes to texture stages 0, 2, and 3, using
  native values 1 for wrap and 3 for clamp.

Across the 91 unique Area 42 terrain materials, 69 wrap both axes, one clamps U
only, four clamp V only, and 17 clamp both axes. Unknown material flag bits remain
preserved. Later executable-backed render evidence is summarized below.

Four terrain materials have no relocated texture binding. They remain explicit
in the report rather than receiving a guessed fallback texture.

## Expanded read-only scene and render evidence

Schema v4 traverses both native world-instance lists, recovering 2,674 records
and 150 archetypes rather than the incomplete historical 849-record scene.
All 79 building-group placements and 1,468 props have native visuals. In total,
132 rigid visual families cover 2,164 placements, 15 skin archetypes cover 371
pedestrians, and 139 RandomType positions retain choices without invented actors.
The three script-registered landing sites use their genuine 11-part visual with
17 materials and five textures, at authored centers pending runtime projection.

`material_evidence.py` pins the signed terrain segment high-byte layer/sort
path, native vertex alpha, the basic RGB-times-two pixel operation, GREATER alpha
testing, LEQUAL depth and applicable reverse-subtract detail blending. It also
verifies that initial terrain loading resets effective UV transforms to identity;
retained authoring transforms are not applied again to compiled mesh UVs.

These are specific verified paths, not full renderer parity. Runtime mission
activation, landing collision height, animation, fog, special shaders and shadow
behavior still require independent work and same-camera retail comparisons.
Native data, source hashes and terrain reference arrays remain unchanged.

## Heightmap limits

The inspection heightmap covers about 67.74% of its 1024 x 1024 raster. It skips
2,106 triangles that are vertical or degenerate in XY, chooses the highest
surface where multiple surfaces overlap, and fills uncovered PNG pixels with
the minimum height. It cannot encode overhangs or stacked surfaces.

Accordingly:

- use the corrected LOD0 mesh for geometry comparisons;
- use the coverage mask whenever inspecting the heightmap;
- never score reconstruction accuracy from the filled PNG alone;
- do not call the heightmap a native retail asset.

## Acceptance gates

1. **Source gate:** all four source hashes and lengths match.
2. **Graph gate:** the reader finds 92 placements and two ordered LOD children
   for each, accounting for all 184 terrain buffers.
3. **Transform gate:** imported matrices and transformed vertices match the
   preserved float32 reference arrays.
4. **Topology gate:** LOD vertex/triangle counts and tile boundaries match.
5. **Unchanged-save gate:** a native read/save operation preserves the complete
   resource graph and unknown data; any byte differences are explained.
6. **CryptoEditor inspection gate:** any generated editor project reopens without
   repair and emits a deterministic loss report.
7. **Visual gate:** CryptoEditor views are compared from documented cameras against
   corrected mesh renders, not only the derived heightmap.
8. **First native-edit gate:** on a disposable game copy, move exactly one
   scenery object, rebuild only the affected package set, and verify its
   placement, collision, streaming, and behavior in game.

The current inspection export does not yet pass the visual gate. UV location and
per-axis wrap/clamp are now executable-verified and CryptoEditor consumes them,
but a same-location retail-game comparison is still required to validate image
orientation and the remaining render states.

Creating a new map is not an early acceptance gate. It follows proven resource
registration, streaming, collision, gameplay setup, and packaging.

