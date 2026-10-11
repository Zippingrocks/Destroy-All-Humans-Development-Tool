# Roadmap

## 0. Evidence baseline — current

- Pin the DAH1 USA Xbox Area 42 source by size and SHA-256.
- Preserve corrected LOD0/LOD1 geometry and retail transform evidence.
- Keep retail, recomp, and research fixtures read-only.

## 1. Lossless DAH1 reader

- Port container decompression and relocation traversal into the native core.
- Represent resource identity, type, package, load group, source offset, and raw
  unknown spans.
- Reproduce Area 42's 751-record scene chain, 92 terrain placements, and 184
  ordered geometry buffers from source packages.
- Compare transformed vertices and topology against the golden reference.

## 2. Unchanged native save

- Serialize to a new output directory without editing the scene.
- Require byte-identical output where the container permits it.
- For unavoidable layout changes, require a complete explained diff and an
  identical decoded resource graph after re-read.
- Preserve unknown fields and records rather than regenerating guessed values.

No mutating editor command is allowed to write packages before this milestone.

## 3. First native edit

Use a disposable game copy:

1. Select one scenery object with well-understood placement and dependencies.
2. Change only its translation.
3. Rebuild the affected package set into new files.
4. Re-read and audit the output.
5. Verify placement, streaming, collision, destruction interactions, and mission
   behavior in game.

This is the decisive proof that the project edits DAH rather than displaying a
conversion.

## 4. Editor interface evaluation

- Expose the native scene through a stable editor API.
- Evaluate WorldEdit's viewport, selection, transform tools, and undo/redo.
- Keep the native writer and package rebuilder independent of the interface.
- Choose thin adapter, component reuse, or maintained fork only after measuring
  integration coupling and license obligations.

## 5. Broader DAH1 map systems

- Materials, textures, objects, and both mesh LODs.
- Collision, destruction, regions, paths, missions, and streaming.
- Resource registration and cross-package dependency validation.

## 6. Additional classic builds

- Give DAH2 and prototype builds independent profiles, evidence, and golden
  fixtures.
- Share code only where binary evidence proves compatibility.

## 7. New maps

Creating maps from scratch begins only after native editing and packaging are
reliable. It requires resource registration, streaming, collision, gameplay
setup, and packaging—not geometry alone.

Remakes remain separate backend projects until their requirements are known.

