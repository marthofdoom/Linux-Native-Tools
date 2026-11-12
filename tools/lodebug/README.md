# lodebug: read-only load-order debugger (Linux native)

Answers "who raises this crime / who wins this record / which file wins" on an MO2 Skyrim SE modlist
without Wine, .NET, xEdit or houseCARL (see `../../housecarl-mcp-evaluation.md` for why not). Python 3.13,
stdlib plus `lz4` (SSE BSA v105). No network. Public domain (Unlicense), like the rest of this repo.

**Read-only.** Every modlist file is opened for reading only. Nothing is written, extracted or cached inside
an instance; output goes to stdout and the optional cache dir (`~/.cache/lodebug`, `LODEBUG_CACHE` to move it).
The tool refuses to start if the cache dir or `--out` resolves inside the instance. No list-specific data is
baked in: lists are inputs. (Measured: no index cache is needed, a full scan of a 3500-plugin list is seconds.)

## Commands

```
lodebug order        --instance DIR [--profile P] [--active-only]
lodebug vfs which    --instance DIR [--profile P] scripts/foo.pex
lodebug pex-calls    --instance DIR [--profile P] [--call crime|Name1,Name2] [--enabled-only]
lodebug pex-dump     PATH_OR_DATA_PATH [--instance DIR]
lodebug record       --instance DIR [--type GMST] (--edid NAME | --formid plugin.esp:0ABC12)
lodebug bsa          ls|cat ARCHIVE.bsa [inner/path] [--filter text]
lodebug skse-strings --instance DIR --pattern 'crime|steal'
lodebug grep-records --instance DIR --type QUST,PERK --edid-regex RX [--full-regex RX]
python3 selftest.py            # synthetic plugin/BSA/pex fixtures, no list files needed
```

* **order**: active plugin order with the MO2-style load index (`00-FD`, `FE:xxx` light), M(aster)/L(ight)
  flags from the TES4 header, and the owning mod folder that wins for that file.
* **pex-calls** (the main use): every function in every WINNING `.pex` (loose or in an active plugin's BSA)
  that calls one of the named methods. `crime` (default) = SendStealAlarm, SendTrespassAlarm, SendAssaultAlarm,
  SendMurderAlarm, ModCrimeGold, ModCrimeGoldViolent, SetCrimeGold, SetCrimeGoldViolent, PlayerPayCrimeGold,
  SendPlayerToJail. Add others (e.g. `crime,StartCombat`). Per hit: script, state, function, the object the
  call is made on and each argument traced back to its source (`game.GetPlayer()`, `param akActionRef`,
  `property X`, `variable`), and the ENTRY POINT(S) (event / fragment) that reach the function, found through
  calls on `self` inside the same script.
* **record**: every active plugin that defines or overrides a record, in load order, the winner, GMST values
  decoded by EDID prefix (i/f/b/s), compressed records handled, FormIDs resolved through each plugin's masters
  (origin plugin + local id). Other records: EDID, FULL, and a hex dump of every other subrecord marked
  `*CHANGED/NEW*` or `*REMOVED*` against the previous layer. GMSTs are looked up by name by the engine, so when
  different origins define the same EDID the last in load order is printed as the effective winner.
* **vfs which**: all providers of a Data path, winner first. **bsa ls/cat**: read SSE v105 (LZ4) and LE v104 (zlib).

## Resolution rules implemented (state of knowledge, see the uncertain list)

* MO2 `modlist.txt`: first line = highest priority, `+` enabled. Overwrite beats all mods; game `Data` is the base.
  Windows paths in `ModOrganizer.ini` (`Z:\mnt\...`, any number of backslashes) map to `/mnt/...`.
* Active plugin = in `loadorder.txt` and (`*` in `plugins.txt` or a base master / listed in `Skyrim.ccc`).
* Loose files always beat BSAs. A BSA is loaded when its plugin is active and its name is `<plugin>.bsa` or
  `<plugin> - *.bsa`; a later plugin's BSA beats an earlier one.
* Path lookups are case-insensitive (Wine semantics).

## Not supported (yet)

FO4/Starfield archive formats, v103 (Oblivion) BSAs, BA2; `archives.txt` / `sResourceArchiveList` ordering
beyond the plugin-name rule; plugins that are inactive but master-pulled; disabled-mod listings
(`--enabled-only` is the only mode); localized string tables (`.STRINGS`; lstring ids are printed raw);
record-field decoding other than GMST, EDID, FULL; flow analysis beyond a straight-line scan (branches are
not followed, so an argument assigned on two paths shows the last assignment before the call);
auto-property VALUES (a pex does not hold them: `property PlayerRef` says only that the plugin supplies it).

## Clean-room sources

Implemented from public format documentation only: UESP wiki "Skyrim Mod:Mod File Format" (TES4 header,
records, GRUPs, compression, XXXX), "Skyrim Mod:Archive File Format" (BSA v104/v105), and "Skyrim Mod:Compiled
Script File Format" (.pex). No GPL code (xEdit, Mutagen, Champollion, houseCARL, BSArch) was read or copied.
The record parser is `../esp_query.py`, imported unchanged.
