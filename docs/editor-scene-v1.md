# DAH editor scene v1

`dahdev editor-scene-export` produces a directory whose name ends in
`.dahscene`. It is the first narrow, versioned boundary between the DAH-native
reader and CryptoEditor. It is deliberately read-only and Area 42-specific.

## Files

- `scene.json` is the manifest and provenance record.
- `buffers.bin` contains 16-byte-aligned native vertex buffers, native triangle
  strips, decoded triangle lists, and native segment records.
- `textures/*.png` are deterministic RGBA previews decoded from bound native
  BC1/BC3 textures. They are inspection aids, not replacement source assets.

Every file and every geometry buffer view has a SHA-256 digest. A consumer must
reject an unsupported schema version, a package without `read_only: true`, a
path outside the scene directory, a digest or length mismatch, duplicate
geometry identifiers, or a placement that references missing geometry.

## Authority and losslessness

The native DAH packages remain authoritative. Version 1 preserves the complete
24-byte terrain vertex records, native 16-bit index strips, native segment
records and flags, placement transforms, resource identity, load group,
package offsets, and LOD relationships. Decoded 32-bit triangle lists and PNGs
are derived views included for efficient display and validation.

The manifest identifies the executable-verified UV channel and per-material U/V
wrap-or-clamp modes. It continues to label the packed vertex tail, segment flag
high byte, other material flag bits, and remaining render state as unresolved.
Collision, scenery, missions, destruction, and streaming are outside version 1.

The sampler object is optional for compatibility with earlier v1 exports. When
present, consumers require `status: "verified"` and apply `address_u` and
`address_v` independently. Older candidate-UV v1 exports remain readable, but
new exports label the same byte layout as verified.

## Area 42 acceptance gate

An Area 42 export is valid only when the native reader independently reproduces
all corrected reference arrays byte-for-byte and reports exactly:

| Record | Count |
| --- | ---: |
| Terrain placements | 92 |
| LOD records / geometry resources | 184 / 184 |
| LOD0 vertices / triangles | 122,956 / 103,634 |
| LOD1 vertices / triangles | 16,240 / 8,120 |

The exporter hashes the retail inputs before and after reading and fails if any
source digest changes. Output must be a new or empty directory outside the
retail source tree.

## Evolution rule

Schema changes that alter required meaning or binary layout increment the
integer version. Readers reject unknown versions rather than guessing. New
optional fields may be introduced only when older readers can safely ignore
them. Package-writing formats are not part of this interchange and require the
separate unchanged-save milestone.
