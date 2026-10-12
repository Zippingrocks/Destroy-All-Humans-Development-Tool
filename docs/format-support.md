# Format support matrix

| Family | Discovery | Container decode | Native scene | Native write/repack | Editor interface |
| --- | --- | --- | --- | --- | --- |
| DAH prototype | Planned | Unsupported | Unsupported | Unsupported | Deferred |
| DAH1 retail Xbox US | Build-pinned Area 42 fixture | Implemented and verified for Area 42 | Native terrain/LODs, 2,674 world records, 2,535 fixed actor previews, separate scatter and native landing visuals | Unchanged-save proof pending | Read-only CryptoEditor schema v4 inspection; v2/v3 legacy loading |
| DAH2 retail | Planned | Unsupported | Unsupported | Unsupported | Deferred |

`inspect` recognizes the generic Zero Engine map-directory shape. It does not
claim that every directory with a `.dir`, `resident.pkg`, `stream.pkg`, and
`discard.pkg` is DAH1 retail. Exact fixture hashes or edition-specific binary
evidence are required for that claim.

Area42 support means verified reconstruction of the listed native data, not full
runtime emulation: mission activation, animation, landing collision projection,
fog, shadows and same-camera retail image parity remain open. Prototype and DAH2
support is not implied by the retail Xbox reader. See the
[current interchange contract](editor-scene-v3.md#schema-v4-current).

## Profile identifiers

- `dah-prototype`: reserved for a build-pinned prototype profile.
- `dah1-retail-xbox-us`: initial research profile; exact Area 42 fixture is
  implemented.
- `dah2-retail`: reserved until platform/build variants are catalogued.

The remakes are not additional profiles in this backend. They require separate
backend projects until their engines, resource identities, packaging, and
compatibility goals are established.


