# Destroy All Humans! Pandemic Development Suite

This repository is the clean-room workspace for understanding and editing
Pandemic's native *Destroy All Humans!* map data. Area 42 from DAH1 retail is
the first golden map: it gives every reader, writer, and editor operation a
known source, known hashes, and independently preserved geometry to compare
against.

The foundation is **DAH-native**. Retail and recomp evidence stays read-only.
The tool imports DAH data into a lossless scene model, records provenance, and
eventually writes verified DAH packages. **CryptoEditor** is the standalone world
editor being built for the Pandemic Development Suite. It uses TrenchBroom as
its primary interface foundation while keeping Battlefront `.wld`/`.ter` and
other editor formats out of the native DAH save path.

## Project shape

1. **DAH-native core** preserves resource identities, relocations, load groups,
   original geometry, LOD relationships, unknown fields, and byte provenance.
2. **CryptoEditor** operates on that core. Its primary foundation is a
   maintained [TrenchBroom](https://github.com/TrenchBroom/TrenchBroom) fork so
   brush construction, texture alignment, selection, and familiar Radiant-like
   workflows are native editor concepts. The modern SWBF2
   [WorldEdit project](https://github.com/PrismaticFlower/WorldEdit) remains a
   feature/reference source for terrain and Pandemic-oriented workflows until
   its active [Codeberg](https://codeberg.org/PrismaticFlower/WorldEdit)
   license is verified.
3. **Game/build adapters** implement independently verified rules for DAH1
   retail, DAH2 retail, and prototype builds. Shared ancestry is a research lead,
   not proof of interchangeable formats.

Remakes are separate backend projects until their compatibility requirements
are established.

## Current checkpoint

- Source-directory inspection for Zero Engine `.dir` + `.pkg` maps.
- Exact verification of the DAH1 USA Xbox retail Area 42 source files.
- Native container decompression and relocation traversal.
- Independent reconstruction of all 92 Area 42 terrain placements, 184 LOD
  geometry buffers, and the 751-record scene chain.
- Byte-for-byte comparison of every reconstructed geometry/transform array
  against the corrected reference export.
- Native terrain segment-to-material-to-texture dependency reconstruction:
  91 unique materials and 67 bound BC1/BC3 textures.
- A read-only textured OBJ/MTL inspection export for individual terrain
  placements, with unresolved UV/render-state claims labeled in its manifest.
- Explicit support matrix for prototype, DAH1 retail, and DAH2 retail.
- Architecture and reconstruction acceptance gates.
- A milestone order that requires unchanged-map preservation before mutation.

No game files or generated meshes are stored in this repository.

## Quick start

Python 3.11 or newer is sufficient. Install the package to obtain its NumPy and
Pillow runtime dependencies.

```powershell
python -m dahdev profiles
python -m dahdev inspect "D:\path\to\blocks\sites\area42"
python -m dahdev verify `
  --source "D:\path\to\blocks\sites\area42" `
  --reference "D:\path\to\area42_heightmap_2026-10-11\lod_transform_verified_2026-10-11"
python -m dahdev terrain-validate `
  --source "D:\path\to\blocks\sites\area42" `
  --reference "D:\path\to\lod_transform_verified_2026-10-11" `
  --out ".\out\area42-validation"
python -m dahdev terrain-section-export `
  --source "D:\path\to\blocks\sites\area42" `
  --placement 45 --lod 0 `
  --out ".\out\area42-tile045-lod0"
python -m unittest discover -s tests -v
```

For a checkout that has not been installed, add `src` to `PYTHONPATH`:

```powershell
$env:PYTHONPATH = "src"
python -m dahdev inspect "D:\path\to\blocks\sites\area42"
```

See [the architecture](docs/architecture.md), [the roadmap](docs/roadmap.md),
[the CryptoEditor plan](docs/cryptoeditor.md),
[the WorldEdit feature evaluation](docs/worldedit-evaluation.md),
[the Area 42 golden-map notes](docs/area42-golden-map.md), and the
[format support matrix](docs/format-support.md).

