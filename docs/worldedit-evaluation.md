# SWBF2 WorldEdit feature evaluation

[WorldEdit](https://github.com/PrismaticFlower/WorldEdit) is a modern editor for
Star Wars Battlefront II (2005), not Pandemic's original internal DAH editor.
The project now directs active development to
[Codeberg](https://codeberg.org/PrismaticFlower/WorldEdit).

Area 42 proves that converted terrain can be displayed. It does not prove that
WorldEdit can preserve, edit, serialize, or package a playable DAH map.
CryptoEditor now uses TrenchBroom as its primary editor foundation; WorldEdit is a
secondary source of terrain and Pandemic-workflow ideas.

| Requirement | Evaluation |
| --- | --- |
| Viewport, selection, movement, undo/redo | Covered by the TrenchBroom foundation; compare behavior |
| Inspecting converted terrain | Already demonstrated |
| Preserving original terrain and both LODs | Requires native DAH mesh support |
| Editing objects, missions, destruction, collision | Requires DAH-specific systems |
| Saving maps that DAH loads | Requires a verified DAH writer and repacker |
| Multiple games and builds | Requires independent format adapters |

## Evaluation rules

- The DAH-native core must run and test without WorldEdit.
- CryptoEditor consumes a read-only scene view and later issues commands through a
  narrow editor API; no upstream editor owns native serialization.
- Battlefront `.wld` and `.ter` structures never become the DAH canonical model.
- Any conversion project carries a deterministic loss report.
- Do not fork, vendor, or copy WorldEdit source until the active upstream's
  license is identified and its GPLv3 compatibility is documented.
- Reimplement documented behavior when source reuse is unnecessary or legally
  unclear.

## Focused experiment

After the native scene can expose Area 42 objects and meshes, evaluate the
smallest useful WorldEdit-inspired feature set inside CryptoEditor:

1. Render native DAH LOD0 and LOD1 geometry without converting it to a
   heightfield.
2. Select one scenery object by its native resource identity.
3. Preview a transform command with undo/redo, without saving.
4. Confirm the command dirties only the expected native record.
5. Record the interaction behavior independently of the WorldEdit source tree.

That evidence decides which behaviors CryptoEditor should implement. Direct source
porting remains a separate licensing decision.

