# Area 42 editor coverage

Area 42 is the golden DAH1 retail scene for CryptoEditor. The editor must present the
map as native DAH data, not as a converted Battlefront or brush map. A visible object is
not considered supported unless its identity, placement, geometry, material dependencies,
and provenance survive the interchange.

## What should be present in the scene

| Domain | Area 42 evidence currently carried by the scene | Editor presentation |
| --- | --- | --- |
| Terrain | 92 placements, both native LODs, original mesh topology, UVs, vertex color/alpha, segment materials | LOD0 visible; LOD1 switchable |
| Fixed world objects | All 2,674 authored records; all 2,535 records with decoded visuals are rendered | Buildings, props, vehicles, actors, defenses, pickups, and unresolved records remain separately searchable |
| Buildings and interiors | 79 building placements and 76 selected fixture/interior parts | Native parent placement with fixture subparts; no duplicate catalog buildings |
| Static scatter | 15,121 instances in 654 native batches and five decoded templates | Separate scatter hierarchy; wind and runtime distance fade are not simulated |
| Characters and creatures | 295 human placements plus robots, powersuits, animals, vehicles, and population definitions | Authored reference poses; animation and held attachments remain unresolved |
| Landing and spawn data | Three landing visuals, seven spawn definitions, and editor-only markers derived from native data | Visuals and annotations remain distinguishable |
| Mission content | 146 objective definitions, 264 authored pickups, nine mission registrations, and 997 objective-script calls | Read-only objective hierarchy; activation is never inferred |
| Spatial organization | 92 native region records with centers, world-to-region transforms, and executable-proven inclusive local bounds `[-1,+1]` | Searchable and camera-focusable; accurate translucent volume overlay is available and hidden by default |
| Runtime organization | 41 mission/load layers, 135 entity tags, stable native entity IDs, and placement/tag cross-references | Searchable native hierarchy; no simulated active mission state |
| Environment | First native sky visual, authored static/dynamic sun constants, original texture mip chains and native sampler state | Verified initial preview contract only |

## Support levels

- **Rendered and evidenced:** native geometry, fixed transforms, first sky, initial authored
  lighting constants, material/texture dependencies, static scatter, landing visuals, and
  reference-pose actor visuals.
- **Decoded and inspectable:** region volumes, mission/load layers, entity tags, spawn definitions,
  script registrations, objectives, population definitions, raw native identities, load groups,
  source offsets, hashes, and unknown records.
- **Preserved but not yet simulated:** mission activation, actor animation and attachments,
  scatter wind/fade, landing collision projection, second sky, dynamic UV behavior, fog,
  reflections, local lights, shadows, and mission-driven effects.
- **Not yet an editable native system:** collision, navigation and traffic networks,
  destruction graphs/states, runtime streaming, audio emitters/music regions, camera/cinematic
  execution, and native package writes.
- **Fully inventoried:** every one of the 7,804 directory resources is assigned to one of
  42 type families. Unnamed families remain `preserved-unknown`; the inventory does not
  guess that they are collision, navigation, destruction, audio, or cinematic data.
- **Unchanged baseline proven:** a safe passthrough command copies all four block files to
  a new directory byte-for-byte, re-parses the identical 7,804-record directory, and compares
  every currently decodable load group. This is not yet semantic reserialization or a writer.

## Editor rules

1. Keep annotations, native visuals, alternate LODs, and future collision/navigation overlays
   on independent layers. Dense diagnostic overlays should open hidden.
2. Use stable native IDs for links between placements, tags, regions, layers, objectives, and
   future destruction/collision data. Never create a duplicate visual merely to express a link.
3. Show explicit unknowns and unsupported runtime behavior in the inspector. A pleasant preview
   must not imply round-trip or gameplay support.
4. Treat terrain brushes and heightmaps as optional authoring tools. Native terrain meshes remain
   the authoritative imported representation.
5. Do not enable native mutation until unchanged read/write equivalence is proven and a controlled
   edit succeeds on a disposable game copy.

## Next research priorities

1. Decode collision ownership and shapes and cross-link them to native placements.
2. Decode navigation, pedestrian, traffic, patrol, and camera path networks.
3. Decode destruction stages, damage volumes, fixture state changes, debris, and physics links.
4. Complete retail render-state parity: special shader paths, animated UVs, fog, reflections,
   local lights, effects, shadows, and same-camera comparisons.
5. Model mission-layer activation without flattening mutually exclusive states into one scene.
6. Turn the byte-identical passthrough baseline into semantic reserialization without losing
   unknown bytes, offsets, relocations, identities, or load-group ownership.
