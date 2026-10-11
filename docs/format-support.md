# Format support matrix

| Family | Discovery | Container decode | Native scene | Native write/repack | Editor interface |
| --- | --- | --- | --- | --- | --- |
| DAH prototype | Planned | Unsupported | Unsupported | Unsupported | Deferred |
| DAH1 retail Xbox | Initial | Implemented and verified for Area 42 | Native terrain, both LODs, material slots, and bound BC1/BC3 textures | Unchanged-save proof pending | Read-only OBJ/MTL section inspection; WorldEdit evaluation planned |
| DAH2 retail | Planned | Unsupported | Unsupported | Unsupported | Deferred |

`inspect` recognizes the generic Zero Engine map-directory shape. It does not
claim that every directory with a `.dir`, `resident.pkg`, `stream.pkg`, and
`discard.pkg` is DAH1 retail. Exact fixture hashes or edition-specific binary
evidence are required for that claim.

## Profile identifiers

- `dah-prototype`: reserved for a build-pinned prototype profile.
- `dah1-retail-xbox-us`: initial research profile; exact Area 42 fixture is
  implemented.
- `dah2-retail`: reserved until platform/build variants are catalogued.

The remakes are not additional profiles in this backend. They require separate
backend projects until their engines, resource identities, packaging, and
compatibility goals are established.


