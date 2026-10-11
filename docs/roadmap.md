# Roadmap

## 0. Evidence baseline — complete

- Pin the DAH1 USA Xbox Area 42 source by size and SHA-256.
- Preserve corrected LOD0/LOD1 geometry and retail transform evidence.
- Keep retail, recomp, and research fixtures read-only.

## 1. Read-only DAH1 terrain reader — complete for Area 42

- [x] Port container decompression and relocation traversal into the native core.
- [x] Represent resource identity, type, package, load group, source offset, and raw
  unknown spans.
- [x] Reproduce Area 42's 751-record scene chain, 92 terrain placements, and 184
  ordered geometry buffers from source packages.
- [x] Compare every transformed vertex/topology array byte-for-byte against the
  golden reference.

## 2. Read-only terrain materials and textures — current

- [x] Resolve each wrapper's relocated material-slot table and prove the segment
  low byte indexes it for all 1,480 terrain segments.
- [x] Resolve material binding records to native texture resource identities.
- [x] Decode the bound BC1/DXT1 and BC3/DXT5 payloads from native dimensions,
  mip counts, and format words without size guessing.
- [x] Export one representative native terrain placement with its original
  geometry, candidate UV channel, and native material assignments.
- [ ] Confirm the stride-24 `+0x0C..+0x13` UV interpretation in retail executable
  code and compare the representative section to the same in-game location.
- [ ] Identify texture addressing, render-state fields, and the high byte of the
  segment flags before calling the textured view accurate.
- [ ] Expand the proven inspection view across every placement and both LODs.

## 3. Unchanged native save

- Serialize to a new output directory without editing the scene.
- Require byte-identical output where the container permits it.
- For unavoidable layout changes, require a complete explained diff and an
  identical decoded resource graph after re-read.
- Preserve unknown fields and records rather than regenerating guessed values.

No mutating editor command is allowed to write packages before this milestone.

## 4. First native edit

Use a disposable game copy:

1. Select one scenery object with well-understood placement and dependencies.
2. Change only its translation.
3. Rebuild the affected package set into new files.
4. Re-read and audit the output.
5. Verify placement, streaming, collision, destruction interactions, and mission
   behavior in game.

This is the decisive proof that the project edits DAH rather than displaying a
conversion.

## 5. Editor interface evaluation

- Expose the native scene through a stable editor API.
- Evaluate WorldEdit's viewport, selection, transform tools, and undo/redo.
- Keep the native writer and package rebuilder independent of the interface.
- Choose thin adapter, component reuse, or maintained fork only after measuring
  integration coupling and license obligations.

## 6. Broader DAH1 map systems

- Objects and their mesh LODs.
- Collision, destruction, regions, paths, missions, and streaming.
- Resource registration and cross-package dependency validation.

## 7. Additional classic builds

- Give DAH2 and prototype builds independent profiles, evidence, and golden
  fixtures.
- Share code only where binary evidence proves compatibility.

## 8. New maps

Creating maps from scratch begins only after native editing and packaging are
reliable. It requires resource registration, streaming, collision, gameplay
setup, and packaging—not geometry alone.

Remakes remain separate backend projects until their requirements are known.

