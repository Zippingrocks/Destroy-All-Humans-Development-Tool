# Architecture: DAH-native core first

## Boundary first

The DAH map and a WorldEdit world are different data models. The project keeps
a hard boundary between native data, the editor interface, and optional
conversion views:

```text
read-only game files
        |
        v
edition-specific container reader
        |
        v
lossless, provenance-carrying DAH scene IR
        |
        +--> audit / compare / golden-fixture validation
        |
        +--> build-specific DAH writer / package rebuilder
        |
        v
editor interface (WorldEdit components are one candidate)
        |
        v
native edits or explicitly lossy inspection projections
```

WorldEdit is an inspection tool and possible source of interface infrastructure,
not the canonical definition of a DAH map. This prevents WorldEdit limitations
from silently deleting DAH data.

## Three independently testable parts

### DAH-native core

The core owns resource identity, package membership, load groups, relocations,
raw transforms, mesh buffers, ordered LOD relationships, object links, collision,
mission data, destruction state, streaming metadata, and unknown byte spans. It
must be usable without WorldEdit or any Battlefront format.

### Editor interface

The interface presents the core's data and issues edits against stable native
identities. Viewport, selection, movement tools, undo/redo, and inspectors are
interface concerns. They do not decide how DAH packages are serialized.

### Game/build adapters

Adapters own build-pinned layouts, compression, relocation rules, resource
registration, streaming, and packaging. DAH1 retail is first because Area 42 is
verified. DAH2 and prototypes receive separate adapters and golden fixtures.

## Layers

1. **Source discovery** identifies the container family and map name without
   guessing an exact game edition from filenames alone.
2. **Edition profile** supplies type layouts, compression rules, relocation
   behavior, coordinate conventions, and executable evidence for one build.
3. **Container reader** exposes byte ranges and relocated object graphs while
   preserving original offsets and hashes.
4. **DAH scene IR** represents transforms, mesh LODs, materials, instances,
   collision, regions, paths, scripts, and unknown records. Every value carries
   source provenance.
5. **Native writer** first proves that an unchanged read/save operation
   preserves all data, including unknown regions and package structure.
6. **Editor command layer** performs native operations with undo/redo and records
   which resources became dirty.
7. **Package rebuilder** writes new output packages for the affected build.
8. **Inspection adapters** may project the scene into WorldEdit or other tools,
   always with an explicit loss report.

## Source profiles

Profiles are build-specific, not merely game-name-specific. The initial exact
profile is `dah1-retail-xbox-us`; Area 42's hashes pin the input more narrowly.
Prototype and DAH2 profiles are listed now so shared code is not accidentally
branded as universal. Their readers remain unsupported until each has its own
evidence and golden fixture.

## Area 42 representation strategy

The corrected LOD0 triangle mesh is the authoritative visual terrain reference.
The 16-bit inspection heightmap is useful for navigation and a coarse editable
terrain surface, but it is lossy and must not replace the mesh evidence.

Any WorldEdit inspection projection should therefore use both:

- mesh tiles for geometry that a heightfield cannot represent;
- an optional generated terrain surface for coarse sculpting and editor tools;
- a loss report identifying holes, overlaps, skipped vertical triangles, and
  any scene records not yet translated.

Objects, materials, collision, regions, paths, missions, and destruction data
are separate native systems. A visually plausible heightfield is not a
completed map import and cannot be used as native save input.

## WorldEdit integration

WorldEdit is a modern SWBF2 editor, not Pandemic's internal DAH editor. Its
viewport, selection, transform tools, and undo/redo are worth evaluating. Its
Battlefront world, terrain, object, and munge assumptions are not DAH-native.

The active upstream is maintained outside this repository and points to
Codeberg. Until reusable boundaries, license obligations, and maintenance costs
are understood, do not vendor its source or commit to a full fork. Start with a
thin inspection adapter or a minimal interface experiment against the DAH core.

See [WorldEdit evaluation](worldedit-evaluation.md).

## Writer invariants

The first writer milestone is not an edit. It is an unchanged round trip:

- source files are opened read-only;
- decoded and unknown data retain byte provenance;
- saving targets a new directory;
- output differences are zero, or every unavoidable container difference is
  explained and the decoded resource graph remains identical;
- re-reading the output produces the same identities, relocations, load groups,
  meshes, transforms, and unknown spans.

Only after that gate passes may the tool move one scenery object on a disposable
game copy, rebuild the affected package, and verify placement and behavior in
game.

## Safety and reversibility

- Readers open inputs without write access.
- Generated data goes to a user-selected output directory.
- Every conversion writes a manifest with source hashes and tool version.
- Unsupported records remain represented as raw spans with provenance.
- Export is opt-in and targets a new directory; in-place package mutation is
  outside the project scope.

