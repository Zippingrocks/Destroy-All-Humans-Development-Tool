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
6. **WorldEdit inspection gate:** any generated inspection project reopens
   without repair and emits a deterministic loss report.
7. **Visual gate:** WorldEdit views are compared from documented cameras against
   corrected mesh renders, not only the derived heightmap.
8. **First native-edit gate:** on a disposable game copy, move exactly one
   scenery object, rebuild only the affected package set, and verify its
   placement, collision, streaming, and behavior in game.

Creating a new map is not an early acceptance gate. It follows proven resource
registration, streaming, collision, gameplay setup, and packaging.

