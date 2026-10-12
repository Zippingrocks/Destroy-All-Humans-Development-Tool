# Area 42 landing sites, population and entity spawn evidence

Scope: DAH1 USA Xbox retail, using the pinned Area 42 `.dir` and packages.
`dahdev.dah1.spawns.read_spawn_scene` reconstructs the native relationships and
exports inspection markers. It does not run scripts, choose a random character,
or assume that every mission layer is active simultaneously.

## Verified reconstruction

| Data | Retail result |
| --- | ---: |
| Lua 4 resources parsed to their exact end | 223 |
| Native region records | 92 |
| Native tag membership arrays | 135 |
| Instance database layers / existing entity records | 41 / 2,674 |
| Layer hashes with matching retail script name constants | 31 |
| Direct literal start/stop/restart/cull calls linked to layers | 50 |
| RandomType definitions / authored random population positions | 3 / 139 |
| Directed spawn tables / levels / recipe rows | 4 / 16 / 47 |
| Unique landing regions / registration call sites | 3 / 8 |
| Exported v4 landing / population / other unrendered entity markers | 3 / 139 / 0 |

The v4 exporter removes candidate markers for all 2,535 records with native mesh
previews. Its 142 annotations retain three landing regions and 139 RandomType
positions. The lower-level `SpawnScene`/`spawn_validation` report still describes
candidate annotations before visual-coverage filtering. A world marker refers
back to its bucket/index in the 2,674-record database; it is not an extra entity.
The historical v3 scene had 374 other unrendered markers and only 849 records;
those figures are not the current completeness target.

## Landing sites come from script calls and native region lookups

The parser recognizes the actual Lua instruction sequence `GETGLOBAL landing`,
`GETDOTTED RegisterZone`, literal arguments, `CALL`. Merely finding a landing-like
string in a package never creates a landing marker.

| Registered name | Native region key | Region offset in resident group 0 | Authored center (X, Y, Z) |
| --- | --- | --- | --- |
| `m1_zone` | `0xA7692E96` | `0x794AE0` | 185.858368, 82.949692, 10.750364 |
| `lz_majestic` | `0x7D6FF704` | `0x793C80` | 645.647644, 317.264130, 5.034252 |
| `m2_zone` | `0x8A11391E` | `0x793E60` | 203.848099, 390.890381, 30.293135 |

Retail `area42_ignition.lua` registers `m1_zone`, registers `lz_majestic` behind
`progress.FindKey("area42.mission.b1.completed")`, and registers `m2_zone` behind
`progress.FindKey("area42.mission.m2.completed")`. B1 ignition/objectives and M1
objectives contain the other registration calls. Every exported call includes
the native Lua resource identity, SHA-256, source path, nested prototype path,
prototype declaration line, program counter and absolute instruction offset.
Debug line tables contain encoded deltas and are not mislabeled as PC-to-line
arrays.

The retail executable confirms the data path:

- VA `0x6AD50` is the landing registration entry point identified through the
  script binding and its region lookup; prototype symbol matching is supporting
  evidence, not the sole proof.
- VA `0xD54A0` hashes the supplied region name. Its table is non-reflected CRC-32
  polynomial `0x04C11DB7`, initial and final XOR `0xFFFFFFFF`, with ASCII uppercase
  converted to lowercase. Known retail `world`, `regions`, `type` and `lua`
  identities independently match this function.
- VA `0x110570` looks up resource `(0xD0894AB3, 0x59CE7BCB)`, `regions:world`,
  then finds the hash-keyed region in its native table.
- VA `0x6ADE4` reads the resolved record's center at `+0x40/+0x44/+0x48`.
  The subsequent call to `0x110650` performs a vertical world query before the
  displayed landing object is constructed. Thus the authored center is useful
  editor data but is not claimed as the final collision-adjusted marker height.

`regions:world` is at resident `0x7933DC`, size `0x804`. Its initial word is
the hash-table mask `0xFF`, followed by 256 `(key, relocated region pointer)`
slots. Each resolved region resource is `0x50` bytes: a 4x4 world-to-region
matrix, a float3 center, and a native layer hash at `+0x4C`. All 92 centers map
back to their local origin within `1.43e-5` using the stored matrix. Region raw
bytes, source identities, slot pointer offsets and matrices are retained.

## Genuine landing visual, not a substituted decal

Retail `input\shared\scripts\site\site_landing.lua` makes the verified literal
call `landing.SetZoneMarker("marker_landing_zone")`. The Lua resource is
`42EF1FD7:75970646`, SHA-256
`fa3175824371fe47abf5d18c2707aed0542d57f6a2a9202c97db15b6aa88b1c9`.
The setter at `0x6ABC0` resolves actor type `0x1D9D48EC`, checks native MarkerType
class `0x247E4E97`, and stores that exact actor for landing construction.

The actor `E57E86D3` has a relocated counted visual-reference list at `+0x18`;
its single LOD is `6FC132F2:A40AAA5B`. That selects segmented visual
`2C84BEFF:A40AAA5B`, with 13 skeleton bones and 11 static-mesh attachments.
All 17 material slots and five texture dependencies are resolved from the retail
pointer graph. No generic quad, guessed texture assignment or invented scale is used.

The ground component is attachment bone 8, mesh `5B99558D:03DCA99B`: nine
vertices, eight triangles and original UVs. Its material slot 6 is
`9709513E:E7923997`, bound to the native 64x64 BC1 texture `3BE99284:C8444C72`.
The other components include the authored beam/ring geometry. The complete visual
uses native skeleton reference transforms; it is not a captured animated frame.

`0x6ADE4` constructs the marker with identity quaternion and unit actor scale
after the vertical collision query. `read_landing_visual_scene()` preserves that
identity/scale but currently places the preview at each authored region center.
Collision-projected Z, rise/fall/pulse animation and mission activation are
explicitly not reproduced. All three displayed sites need not be active together.

## Random population keeps its real alternatives

The native archetype class discriminator is `0x07066922` (`RandomType`). Its
relocated field `+0x70` points to a count followed by eight-byte records: an actor
archetype pointer and a float cumulative probability. The retail chooser at VA
`0x119400` compares its random sample against those increasing thresholds and
stores the selected actor at `+0x74`. The reader requires valid actor targets,
strictly increasing finite thresholds and a terminal threshold of exactly 1.

| Definition | Native cumulative thresholds | Choices |
| --- | --- | --- |
| `h_random_soldier_gman` | .75, 1 | soldier rifle; gman pistol |
| `h_random_ambience_area42` | .80, .90, .95, 1 | soldier rifle; gman pistol; airforce general; actor `0x3770790D` |
| `h_random_ambience_area42_majestic` | .65, .90, 1 | orange worker; labcoat scientist; gman pistol |

The raw float values are preserved (including float32 rounding), and each
choice carries its actual resource identity. The viewport shows a population
spawn marker rather than making a random selection and presenting it as a
retail-authored human.

## Directed spawns, layers and tags

`site_directedspawn_local.lua`, resource `0x50C3CC29` in discard group 1 at
`0x2DAB4`, constructs four `area42spawn` tables: `defaults`,
`m1_spawn_majestic`, `b1_spawn_majestic`, and `m2_armybase`. Each has four levels.
A restricted constructor decoder handles literal pushes, tables, field/list
writes and globals; it rejects calls, loops and other executable instructions.
It reconstructs all 47 six-field recipe rows from instructions. The eight
distinct referenced actor types all resolve in the retail directory.

The six fields are retained as actor type, behavior name, three native numeric
parameters, and a mode or list of modes such as `DIRECTED`, `RANDOM`, or
`["directed", "road"]`. Timing/count meanings for the three numeric fields are
left unassigned pending separate runtime proof. These recipes are not fixed
spawn positions. Road/traffic selection and runtime conditions determine their
eventual locations.

The existing world database is joined to the script evidence using the exact
native layer hashes. `tags:world` at resident `0x83088` contains a mask `0x1FF`,
512 key/pointer slots and count-prefixed signed entity-ID arrays. All 135 arrays
are preserved; their IDs join 320 of the authored records using record `+0x04`
after both instance lists are included (the historical partial reader found 182).
Unknown tag/layer names retain their numeric keys. Matching script strings label
an existing key; they do not prove activation. Direct start/stop/restart/cull
calls separately provide script relationships for the corresponding layer.

Dynamic `instance.CreateObject` expressions occur in pickup creation callbacks.
Their function references and literal actor-name prefixes are reported as
dynamic arguments; no coordinates are invented from a function's string table.
The reader also preserves the raw 0x40-byte entity header. Metadata after that
header and complete runtime mission/traffic behavior remain further work.

## Validation and API

`SpawnScene` exposes `scene_markers`, `spawn_definitions`, `spawn_layers`,
`regions`, `tags`, `script_calls` and a `report`. Markers contain
`id/kind/label/status/position_f32`, native evidence, and existing-world-record
references where applicable. All package sources remain read-only.

`verify_spawn_executable_evidence(path)` pins the known retail executable SHA-256
and checks the precise bytes of the hash, region lookup, landing center access,
and random-choice routines. The marker report distinguishes decoded records from
unevaluated runtime state.

`LandingVisualScene` separately exposes the selected actor/LOD, native component
LODs, region-linked placements and a dependency/limitation report.
`verify_landing_executable_evidence()` checks the setter, projection/construction
path and identity quaternion in the pinned executable. Schema v4 exports this
under `landing_placements`, not as extra `world.instance-database` records.

Set `DAH_AREA42_SOURCE` to the retail Area 42 package directory and optionally
`DAH_RETAIL_XBE` to the pinned executable, then run
`python -m unittest discover -s tests -p test_spawns.py -v`.
The 15 tests cover malformed/truncated data, actual instruction-based calls,
literal table reconstruction, relocation and threshold errors, real package
reconstruction, exact landing centers, and executable evidence. All 15 pass with
the pinned Area 42 fixture and retail XBE. `test_mission_visuals.py` adds nine
tests for the native landing chain, real ground geometry/texture, provenance,
malformed references, authored centers and executable construction evidence.
