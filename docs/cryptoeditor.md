# CryptoEditor plan

**CryptoEditor** is the standalone world and level editor in the Pandemic
Development Suite. It has its own product identity while remaining a
frontend for the suite's native readers, compilers, and game/build adapters.

## Product boundary

| Component | Responsibility |
| --- | --- |
| Pandemic Development Suite | Umbrella project and game/build research suite |
| `dahdev` | DAH-native readers, provenance, validation, writers, and packaging |
| CryptoEditor | Interactive map and world editor |
| TrenchBroom | Primary open-source editor foundation |
| SWBF2 WorldEdit | Terrain/workflow reference; source reuse is license-gated |

CryptoEditor edits a neutral, provenance-carrying scene through a versioned API.
It never treats a `.map`, `.wld`, `.ter`, heightmap, or viewport mesh as the
authoritative DAH package representation.

## Upstream strategy

CryptoEditor will be maintained as a visible TrenchBroom fork. TrenchBroom is
GPLv3, so CryptoEditor retains its copyright notices, license, source availability,
and modification history. Upstream changes should be merged deliberately rather
than erasing the fork's DAH-specific node and command layers.

The SWBF2 WorldEdit GitHub mirror directs development to Codeberg but does not
currently expose an obvious license in the mirror. Do not copy or link its
source until the active tree's license is recorded and verified compatible with
GPLv3. We can study public behavior, file interoperability, and user-facing
workflows and implement the required capabilities independently.

This is a feature integration project, not a mechanical source-tree merge.

## Editor document model

The first DAH-specific editor nodes are:

- `DahTerrainNode`: authoritative native terrain placements and both mesh LODs;
- `DahHeightfieldNode`: an explicitly derived editable terrain surface;
- `DahReferenceMeshNode`: native or imported geometry that is not brush CSG;
- `DahObjectNode`: a placed resource with stable native identity;
- `DahCollisionNode`: collision geometry and flags;
- `DahRegionNode` and `DahPathNode`: gameplay volumes and navigation/path data;
- `DahUnknownNode`: preserved data that can be inspected but not mutated safely;
- TrenchBroom brush/entity nodes: new authoring geometry and editor helpers.

Derived terrain always carries its source, resolution, transform, conversion
parameters, and loss report. Brushes can coexist with native terrain without
pretending to be the retail mesh.

## First vertical slice

The local fork is established on TrenchBroom revision
`30dd5b6189487f666cea6f1b6502dcabd13809a5`. Its `main` branch retains
`upstream` as the TrenchBroom remote, and `CRYPTOEDITOR.md` records the build
and architectural boundary. The Windows release build produces
`CryptoEditor.exe`; all 132 TrenchBroom model/editor regression tests pass.
Publishing the standalone remote is still pending.

1. Keep the maintained CryptoEditor fork building and tested on Windows.
2. Define the first versioned scene manifest/API between `dahdev` and CryptoEditor.
3. Open Area 42 read-only with all 92 placements and both LODs.
4. Render original material assignments and expose provenance in an inspector.
5. Navigate, select, hide, and inspect native terrain nodes with undoable
   editor-only commands.
6. Add a derived heightfield overlay and a terrain brush prototype without
   enabling native package save.
7. Add ordinary brush construction for new geometry and keep it in an editor
   layer until a proven compiler can lower it into supported DAH resources.

The slice succeeds when Area 42 is reproducibly loaded from native evidence,
native mesh and derived heightfield are visibly distinct, and no retail or
recomp file is modified.

## Feature order

1. Native reference meshes, materials, selection, and provenance inspection.
2. Brush creation, clipping, vertex editing, texture alignment, and grouping.
3. Derived heightfield sculpting, painting, layer controls, and deterministic
   regeneration.
4. Native objects, collision, regions, paths, destruction, and mission data.
5. Unchanged native save, followed by one controlled object move on a
   disposable game copy.
6. New-map compilation only after registration, streaming, collision, gameplay,
   and packaging requirements are proven.

## Repository plan

- Keep this repository as the DAH-native core and Pandemic Development Suite
  integration point.
- Host CryptoEditor in its own repository so the TrenchBroom-derived GPLv3 code and
  upstream history remain clear.
- Track the pinned CryptoEditor revision and interchange schema here; do not vendor
  the full editor tree into this Python project.
- Do not create a WorldEdit-derived repository until its current license is
  verified. If compatible source is eventually used, record exact commits,
  copied components, modifications, and attribution.
