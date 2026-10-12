# DAH editor scene v2

Historical format: new exports use [schema v4](editor-scene-v3.md#schema-v4-current). CryptoEditor
continues to load v2 scenes.

Schema v2 is a read-only, provenance-carrying interchange between the DAH-native core and
CryptoEditor. It supersedes v1 without turning the interchange into a DAH package writer.

In addition to the verified v1 terrain, v2 contained a partial Area 42
`world.instance-database` reconstruction, native actor archetype metadata, and resolved static
building and PropType set-piece visuals. Geometry entries declare `kind` and their native vertex stride:
terrain uses stride 24 and static world objects use stride 28.

The `placements` array remains terrain-only. Historical `object_placements` contains 849
decoded records, not the full native database, and assigns a reversible `editor_collection`; the original bucket hash,
record offset, packed rotation, raw record header, actor resource, native class, and asset
name remain present. A placement may have no renderable LOD while its native identity is
still fully represented.

Historical v2 acceptance counts were 92 terrain placements, 184 terrain LODs, 184 terrain
geometries, 41 world buckets, 849 world placements, 18 renderable building placements,
eight unique building meshes, 23 renderable PropType placements, 11 unique prop meshes,
and 41 renderable structure/set-dressing placements in total.

Schema v4 corrects the omitted second counted list and spurious empty-bucket
inline record, yielding 2,674 native records. It also retains packed quaternion
X/Y and reconstructs all segmented bone attachments. The old counts must not be
used as a completeness target for new readers.

The format stays explicitly `read_only`. It is not proof that modified `.dahscene` data can
be written back to a game package.
