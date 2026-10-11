# Repository guardrails

- Treat retail, prototype, and recomp game files as read-only evidence.
- Area 42 is the first golden reconstruction fixture. Do not replace or edit the
  recomp's Area 42 assets unless the user explicitly changes this rule after a
  documented hard wall.
- Import into a neutral, provenance-carrying scene model before adapting data to
  WorldEdit. Do not make WorldEdit's terrain model the definition of DAH maps.
- Build the foundation as three separable parts: DAH-native core, editor
  interface, and game/build adapters. Battlefront `.wld` and `.ter` files must
  never enter the native DAH save path.
- Preserve source hashes, offsets, raw transforms, load groups, and unknown data.
- Never describe a derived heightmap as game-exact. It cannot preserve vertical
  faces, overhangs, stacked surfaces, objects, materials, or collision.
- Add a golden-fixture test before generalizing a reader from DAH1 retail to the
  prototype or DAH2 retail.
- Do not copy WorldEdit source into this repository until its current upstream
  license and the intended fork/submodule strategy are recorded.
- Do not commit to a WorldEdit fork merely to obtain a viewport. Evaluate its
  reusable interface components against the DAH-native core first.
- Before allowing any native edit, prove that reading and saving an unchanged
  map preserves its data. Mutation tests must target a disposable game copy and
  write new packages, never overwrite the research fixture.
- Treat remakes as separate backend projects until their compatibility and
  packaging requirements are established.

