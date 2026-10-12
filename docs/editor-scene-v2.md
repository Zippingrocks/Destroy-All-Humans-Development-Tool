# DAH editor scene v2

Schema v2 is a read-only, provenance-carrying interchange between the DAH-native core and
CryptoEditor. It supersedes v1 without turning the interchange into a DAH package writer.

In addition to the verified v1 terrain, v2 contains the complete Area 42
`world.instance-database`, native actor archetype metadata, and the currently resolved
static building visuals. Geometry entries declare `kind` and their native vertex stride:
terrain uses stride 24 and static world objects use stride 28.

The `placements` array remains terrain-only. `object_placements` contains all 849 native
world records and assigns a reversible `editor_collection`; the original bucket hash,
record offset, packed rotation, raw record header, actor resource, native class, and asset
name remain present. A placement may have no renderable LOD while its native identity is
still fully represented.

Area 42 acceptance counts are 92 terrain placements, 184 terrain LODs, 184 terrain
geometries, 41 world buckets, 849 world placements, 18 building placements, 17 currently
renderable building placements, and seven unique building meshes.

The format stays explicitly `read_only`. It is not proof that modified `.dahscene` data can
be written back to a game package.
