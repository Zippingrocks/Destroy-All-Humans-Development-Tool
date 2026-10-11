# Repository guardrails

- Treat retail, prototype, and recomp game files as read-only evidence.
- Area 42 is the first golden reconstruction fixture. Do not replace or edit the
  recomp's Area 42 assets unless the user explicitly changes this rule after a
  documented hard wall.
- Import into a neutral, provenance-carrying scene model before exposing data to
  CryptoEditor. Do not make either upstream editor's terrain model the definition
  of DAH maps.
- Build the foundation as three separable parts: DAH-native core, editor
  interface, and game/build adapters. Battlefront `.wld` and `.ter` files must
  never enter the native DAH save path.
- Preserve source hashes, offsets, raw transforms, load groups, and unknown data.
- Never describe a derived heightmap as game-exact. It cannot preserve vertical
  faces, overhangs, stacked surfaces, objects, materials, or collision.
- Add a golden-fixture test before generalizing a reader from DAH1 retail to the
  prototype or DAH2 retail.
- CryptoEditor is the standalone editor product. Its primary interface foundation
  is a maintained TrenchBroom fork; preserve TrenchBroom's GPLv3 notices and
  keep editor code separated from the DAH-native core by a documented API.
- Do not copy SWBF2 WorldEdit source into CryptoEditor or this repository until its
  current upstream license and compatibility with GPLv3 are recorded. Until
  then, use it only as a behavioral and design reference.
- Combine capabilities deliberately; do not attempt a mechanical merge of the
  TrenchBroom and SWBF2 WorldEdit trees.
- Before allowing any native edit, prove that reading and saving an unchanged
  map preserves its data. Mutation tests must target a disposable game copy and
  write new packages, never overwrite the research fixture.
- Treat remakes as separate backend projects until their compatibility and
  packaging requirements are established.

