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

## 3. CryptoEditor read-only shell

- Establish CryptoEditor as a maintained TrenchBroom GPLv3 fork in its own
  repository, with upstream attribution and a documented update strategy.
- Define a versioned interchange/API boundary between CryptoEditor and `dahdev`.
- Load Area 42's native LOD0/LOD1 reference meshes and material assignments
  without converting them into brushes or a heightfield.
- Add explicit DAH terrain, reference mesh, object, and unknown-data node types.
- Keep brush construction available for new geometry without implying that a
  brush is a lossless representation of retail terrain.
- Implement terrain navigation and a first read-only heightfield overlay.

This milestone may progress alongside native writer research, but it cannot
write game packages.

## 4. Unchanged native save

- Serialize to a new output directory without editing the scene.
- Require byte-identical output where the container permits it.
- For unavoidable layout changes, require a complete explained diff and an
  identical decoded resource graph after re-read.
- Preserve unknown fields and records rather than regenerating guessed values.

No mutating editor command is allowed to write packages before this milestone.

## 5. First native edit

Use a disposable game copy:

1. Select one scenery object with well-understood placement and dependencies.
2. Change only its translation.
3. Rebuild the affected package set into new files.
4. Re-read and audit the output.
5. Verify placement, streaming, collision, destruction interactions, and mission
   behavior in game.

This is the decisive proof that the project edits DAH rather than displaying a
conversion.

## 6. CryptoEditor editing foundation

- Add terrain sculpt/paint operations as commands over an explicit editable
  terrain representation with deterministic conversion and loss reporting.
- Add brush construction and CSG for new authored geometry.
- Add native object placement, selection, transforms, properties, and
  dependency inspection.
- Keep the native writer and package rebuilder independent of the editor UI.
- Reimplement useful SWBF2 WorldEdit behaviors; port source only if its license
  is identified, GPLv3-compatible, and attribution is preserved.

## 7. Broader DAH1 map systems

- Objects and their mesh LODs.
- Collision, destruction, regions, paths, missions, and streaming.
- Resource registration and cross-package dependency validation.

## 8. Additional classic builds

- Give DAH2 and prototype builds independent profiles, evidence, and golden
  fixtures.
- Share code only where binary evidence proves compatibility.

## 9. New maps

Creating maps from scratch begins only after native editing and packaging are
reliable. It requires resource registration, streaming, collision, gameplay
setup, and packaging—not geometry alone.

Remakes remain separate backend projects until their requirements are known.

