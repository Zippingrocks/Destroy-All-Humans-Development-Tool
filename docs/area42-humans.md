# DAH1 US Xbox retail Area 42: pedestrians and human reference meshes

`dahdev.dah1.humans.read_humans(world)` reconstructs the primary character
visuals from the retail world database. It returns native placements and
archetypes; it does not create live actors or select runtime spawn outcomes.

The fixture has **295 explicit human placements**, using 12 archetypes. Its
`ActorTypePedestrian` class also includes 70 cow placements and 6 powersuits,
for 371 pedestrian placements across 15 archetypes. The 139 `h_random_*`
`RandomType` placements belong to population/spawn definitions. A name prefix
must not override that native class. All 371 pedestrian placements now resolve
to three native visual LODs; those share 21 distinct skin meshes across seven
model families. Human models account for five of those families.

Schema v4 obtains these unchanged pedestrian counts from the corrected full
2,674-record database, not the historical 849-record subset. It renders all 371
pedestrian skins and 2,164 rigid placements separately; the latter use native
bone-attached components rather than the skin reference-mesh path described here.

## Primary visual and clothing selection

The primary actor-to-LOD link is a **relocated pointer** within the actor record.
Its relative offset varies with serialized actor data (for example `+0x6B0`
for `h_male_soldier_rifle`). The LOD resource is `0x6FC132F2`; its `+0xC0`
relocation addresses an ordered pointer array and `+0xC8` stores its count.
Each child is `0x7C6FF505`, size `0xD4`, with a `0x72C7D314` mesh name hash
at `+0xC8`. Mesh identity is resolved with both type and name hashes.

The actor `+0x408` shortcut found in earlier research is unsuitable for the
primary visible model: different human archetypes all name the same secondary
skin there. Using it would silently display the wrong geometry.

The primary skin's `+0xC0` relocation selects shared data. Its `+0x30`
relocation and `+0x34` count identify the default native material pointer array.
An LOD may override it through `+0xB4`, an array of `(variant hash, relocated
material-list pointer)` pairs, counted by `+0xB8`. The actor's **`+0x1C`
variant hash** selects one exact entry. The selected list has a pointer at
`+0` and material count at `+4`.

This distinction is necessary: labcoat and plaid-jacket scientists share their
mesh, as do orange-clothed and overall-clothed workers, but their selected
materials differ. The decoder rejects absent or ambiguous variant keys.

## Native skin layout

The 32-byte `0x72C7D314` descriptor has relocated vertex, index and batch
pointers at `+0`, `+4` and `+0x1C`; vertex/index counts at `+0x10/+0x14`; and
stride (low 16 bits) plus batch count (high 16 bits) at `+0x18`.

| 40-byte vertex offset | Native channel |
| --- | --- |
| `+0x00` | Three float32 authored model-space positions |
| `+0x0C` | Three float32 normals |
| `+0x18` | Four uint8 skin weights, retained without renormalizing |
| `+0x1C` | Four uint8 palette offsets, active values divided by six |
| `+0x20` | Two float32 original UVs, without flipping or scaling |

Each `0x8E`-byte batch begins with a uint16 palette count and storage for
32 `(skeleton index, local palette index)` uint16 pairs. Only the counted
entries are interpreted; unused bytes are retained. Six uint16 words at
`+0x82` are vertex start/count, index start/count, nondegenerate triangle
count, and material slot. This final-word order differs from static meshes.
The reader checks contiguous buffer coverage, strip winding, triangle counts,
material ranges and every nonzero-weight influence's palette reference.
Zero-weight index bytes remain uninterpreted even if they address no bone.

## Skeleton evidence and preview limits

The shared skin block's `+8` relocation addresses its skeleton; the uint16
count is at `+0x0C`. Bone records are `0x38` bytes: name, parent, child and
sibling hashes at `+4/+8/+0x0C/+0x10`; position at `+0x14`; quaternion at
`+0x20`; and scale at `+0x30`. Word `+0x34` carries native flags in the shared
pose-node loader; its raw value, unknown word `+0`, the upper half of
the shared count field and all original bytes are retained. Bone names are
not invented. Every hierarchy hash resolves within its own skeleton, parent
chains are checked for cycles, and quaternions are checked for near-unit length
with 0.001 tolerance (retail values around 0.9997 are retained, not normalized).
Human skeletons have 19–21 bones; the powersuit has 23 and cow has 38.

The preview uses the **unaltered authored reference mesh**. Positions already
form coherent model-space bodies: the human LOD0 bounds run from approximately
ground level to 1.85–1.92 native units in Z; the powersuit reaches 4.24. The
status is `native-reference-mesh; animation-not-applied`. It is not a captured
retail idle pose. Animation, runtime attachments/held weapons, mission
activation, skin deformation and in-game pose parity remain unverified. The
native skeleton, influence bytes and palettes are exposed for that later work.

## Reproduction

Run the standard unit suite. For the actual retail fixture, set
`DAH_AREA42_SOURCE` to the read-only directory containing `area42.dir` and
its packages, then run `python -m unittest discover -s tests -p test_humans.py`.
The golden check validates native model and material relationships, including
the distinct labcoat/plaid variants, rather than accepting source hashes alone.
