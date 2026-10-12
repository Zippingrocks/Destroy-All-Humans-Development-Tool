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

## 2. Read-only terrain materials and textures — structurally complete

- [x] Resolve each wrapper's relocated material-slot table and prove the segment
  low byte indexes it for all 1,480 terrain segments.
- [x] Resolve material binding records to native texture resource identities.
- [x] Decode the bound BC1/DXT1 and BC3/DXT5 payloads from native dimensions,
  mip counts, and format words without size guessing.
- [x] Export one representative native terrain placement with its original
  geometry, native UV channel, and native material assignments.
- [x] Confirm stride 24 and the `+0x0C..+0x13` float2 UV interpretation in the
  pinned retail executable.
- [x] Confirm the retail terrain declaration is `FLOAT3, FLOAT2, D3DCOLOR`,
  preserve packed diffuse RGBA at `+0x14`, and trace native alpha and the basic
  RGB-times-two shader operation in the executable.
- [x] Recover per-axis wrap/clamp from material `+0x7C` flags and reproduce it in
  CryptoEditor.
- [x] Expand the inspection view across every placement and both LODs.
- [ ] Compare a representative section to the same in-game location and resolve
  any remaining orientation or render-state differences.
- [x] Trace the signed high-byte segment layer/sort key and forced blended pass,
  GREATER alpha test, LEQUAL depth and applicable reverse-subtract detail blend.
- [x] Preserve authoring UV matrices but use the runtime initial identity transform
  for compiled terrain UVs; do not apply that matrix twice.
- [ ] Establish remaining special shader/material states and fog/shadow parity;
  no claim of complete renderer reproduction follows from these base paths.

## 3. CryptoEditor read-only shell — current

- [x] Establish CryptoEditor as a maintained TrenchBroom GPLv3 fork in its own
  repository, with upstream attribution and a documented update strategy.
- [x] Define a versioned, read-only `.dahscene` boundary between CryptoEditor
  and `dahdev`, with payload hashes, provenance, and explicit unknown fields.
- [x] Load Area 42's native LOD0/LOD1 reference meshes and material assignments
  without converting them into brushes or a heightfield.
- [x] Parse both native world-instance lists: 41 buckets, 2,674 unique records,
  150 archetypes and all four packed quaternion components. Supersedes the
  incomplete 849-record v2/v3 reader.
- [x] Follow the verified placement -> actor archetype -> visual LOD/direct visual
  -> static mesh/material chain for 132 rigid families / 2,164 placements,
  including 79 building-group placements and 1,468 props. Decode the native
  signed-16 segment count and every segmented bone attachment, not just its first part.
- [x] Group native placements by DAH semantic class in the map-elements UI while
  retaining native bucket/record identity and unresolved records.
- [x] Decode native foliage/rock scatter: all 654 batches, 15,121 instances,
  five templates, full draw buffers, matrix/RGB records and base alpha/depth state.
- [x] Decode 295 explicit humans at three native LODs with selected clothing
  materials, raw skeletons, weights and batch palettes; display authored reference poses.
- [x] Resolve three landing sites from script registration calls to native regions.
- [x] Resolve `landing.SetZoneMarker` to its real 11-component mesh, 17 materials
  and five textures; retain authored-center versus runtime-projected-Z distinction.
- [x] Preview all 371 pedestrian skins, including cows/powersuits, alongside the
  2,164 rigid placements; only 139 RandomType records lack fixed actor visuals.
- [x] Decode 139 random-population positions, their three weighted definitions,
  four directed-spawn recipe tables, and layer/tag relationships for all 2,674 records.
- [ ] Reconstruct runtime human animation/attachments and scatter wind/fade.
- [ ] Evaluate mission activation and collision-adjusted landing marker height.
- [ ] Add explicit DAH terrain, reference mesh, object, and unknown-data node types.
- [ ] Keep brush construction available for new geometry without implying that a
  brush is a lossless representation of retail terrain.
- [ ] Implement terrain navigation and a first read-only heightfield overlay.

This milestone may progress alongside native writer research, but it cannot
write game packages.

## 4. Unchanged native save

- [x] Establish a safe byte-preserving passthrough baseline in a new output directory;
  Area 42 verifies four identical source files, 7,804 identical resource records,
  710 decoded-identical load groups, and 10 opaque groups covered by enclosing-file identity.
- [ ] Serialize the decoded native model to a new output directory without editing the scene.
- Require byte-identical output where the container permits it.
- For unavoidable layout changes, require a complete explained diff and an
  identical decoded resource graph after re-read.
- Preserve unknown fields and records rather than regenerating guessed values.

No mutating editor command is allowed to write packages before this milestone.
The completed passthrough check is deliberately not treated as a semantic writer.

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

- Reconstruct runtime segmented animation/visibility and destruction behavior;
  the decoded component-table keys are bone attachments, not destruction states.
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

