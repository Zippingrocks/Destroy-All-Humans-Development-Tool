# WorldEdit evaluation

[WorldEdit](https://github.com/PrismaticFlower/WorldEdit) is a modern editor for
Star Wars Battlefront II (2005), not Pandemic's original internal DAH editor.
The project now directs active development to
[Codeberg](https://codeberg.org/PrismaticFlower/WorldEdit).

Area 42 proves that converted terrain can be displayed. It does not prove that
WorldEdit can preserve, edit, serialize, or package a playable DAH map.

| Requirement | Evaluation |
| --- | --- |
| Viewport, selection, movement, undo/redo | Useful infrastructure to investigate reusing |
| Inspecting converted terrain | Already demonstrated |
| Preserving original terrain and both LODs | Requires native DAH mesh support |
| Editing objects, missions, destruction, collision | Requires DAH-specific systems |
| Saving maps that DAH loads | Requires a verified DAH writer and repacker |
| Multiple games and builds | Requires independent format adapters |

## Evaluation rules

- The DAH-native core must run and test without WorldEdit.
- WorldEdit may consume a read-only scene view or issue commands through a
  narrow editor API; it must not own native serialization.
- Battlefront `.wld` and `.ter` structures never become the DAH canonical model.
- Any conversion project carries a deterministic loss report.
- Do not fork or vendor WorldEdit until a focused experiment identifies the
  reusable components, their coupling, and the applicable license obligations.

## Focused experiment

After the native scene can expose Area 42 objects and meshes, evaluate the
smallest viable interface reuse:

1. Render native DAH LOD0 and LOD1 geometry without converting it to a
   heightfield.
2. Select one scenery object by its native resource identity.
3. Preview a transform command with undo/redo, without saving.
4. Confirm the command dirties only the expected native record.
5. Measure how much WorldEdit code would need to depend on DAH-specific types.

That evidence decides between a thin adapter, extracted interface components,
or a maintained fork. The project does not choose one in advance.

