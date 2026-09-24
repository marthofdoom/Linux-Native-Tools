# Procedure: MCM Helper config (build, validate, debug)

MCM Helper (Exit-9B) draws an MCM from a JSON file. When the file breaks a rule
it rejects the **whole config with no per-mod error**, and the mod just never
appears in the menu. Everything here is about catching that before the tester
does. Background notes: [instance-data-and-events.md](instance-data-and-events.md)
§19 (key renames) and §28 (config discipline). MFO's full pipeline:
`Docs/TOOLING.md` §(b) in the MFO repo.

## The four parts of a working menu

1. **An empty shim script:** `ScriptName MFO_MCM extends MCM_ConfigBase`,
   compiled to `Scripts/<Name>.pex` (see [papyrus-on-linux.md](papyrus-on-linux.md)).
   The body stays empty. MCM Helper renders from JSON, not Papyrus.
2. **A quest carrying it** through VMAD, start-game-enabled and **not
   run-once** (SkyUI cannot re-register a run-once quest). Emitted by the ESP
   generator.
3. **`Data/SEQ/<plugin>.seq`** listing that quest's FormID, so it starts on an
   existing save too.
4. **`Data/MCM/Config/<modName>/config.json`** plus the defaults file
   **`Data/MCM/Config/<modName>/settings.ini`**.

Ship all four. A config with no shim `.pex`, or a shim with no config, draws
nothing.

## Rules that fail silently

| Rule | What breaks it |
|---|---|
| `minMcmVersion` at the root | missing = whole config dropped. MFO and MEO use `9` |
| bind a control with a top-level `"id": "key:Section"` | `modSettingName` inside `valueOptions` is not a schema field and drops the whole config |
| enum choices go in `valueOptions.options` (optional `shortNames`) | a top-level `enumOptions` is invalid |
| an enum's `defaultValue` is an index inside `options` | out of range draws a broken control |
| `sourceType` matches the key prefix (`b` Bool, `i` Int, `f` Float) | MFO convention, enforced by `audit_mcm.py`, so the DLL's parser and the control agree on the type |
| ship `MCM/Config/<mod>/settings.ini` with every key | MCM Helper **registers** settings from this file. Without it a key added in an update never registers on an existing install and draws as a dead checkbox (MFO #55) |
| a key that changes meaning gets a new name | MCM Helper keeps the old value per key name forever (§19) |

**Where values live.** `ModSettingBool/Int/Float` persist to
`Data/MCM/Settings/<mod>.ini`. Under MO2 the live copy is in `overwrite/`, and
it shadows the seed copy the mod ships. A key missing from that live file reads
as OFF or zero, so MFO's DLL appends missing keys with defaults at
`kInputLoaded` (before MCM Helper reads them). `GlobalValue` controls bind a
`GLOB` record by `"sourceForm": "<Plugin>|0xLOCALID"` and need no INI at all.
MFO's progression add-on uses that.

## Validate before shipping

**MFO:** `tools/audit_mcm.py` checks the five places a ModSetting is wired
(config.json, the defaults `settings.ini`, the seed `MCM/Settings/MFO.ini`, and
`native/Config.cpp`'s default table and parse switch), type-aware default
agreement, `minMcmVersion`, enum ranges and orphan keys.
```bash
python3 tools/audit_mcm.py                                              # MFO's ModSetting menu
python3 tools/audit_mcm.py out/MCM/Config/MFO_Progression/config.json   # a GlobalValue menu
```
Both must print `PASS`. `release.sh` and `tools/package_test.sh` run them.

**Any repo, quick structural check:**
```bash
python3 -m json.tool out/MCM/Config/<mod>/config.json >/dev/null && echo JSON-OK
grep -n '"minMcmVersion"\|"modSettingName"\|"enumOptions"' out/MCM/Config/<mod>/config.json
```
Want `minMcmVersion` present and the other two absent.

**The upstream JSON schema is not a reliable gate.** The schema on MCM
Helper's `main` branch
(`raw.githubusercontent.com/Exit-9B/MCM-Helper/main/docs/config.schema.json`)
targets a newer MCM Helper than many modlists run. Checked 2026-09-23: MFO's
two configs, which register fine in game, fail it on `cursorFillMode` inside a
page and on `minMcmVersion` 9 being below 13. Use it only to spot typos, and
trust a diff against a sibling config that is known to register on the same
MCM Helper version.

## Verify in game

1. **Full restart.** MCM Helper scans configs at launch only.
2. `Data/MCM/Settings/<modName>.ini` appears (in MO2's `overwrite/`). No file
   means the config was rejected.
3. `MCMHelper.log` (in the Proton prefix's
   `My Games/Skyrim Special Edition/SKSE/`, see
   [deploy-and-logs.md](deploy-and-logs.md#read-logs)) reports
   `Registered N mod configs`. One short of the folder count means one was
   rejected.
4. A menu that is registered but missing from SkyUI's list: in the console,
   `setstage SKI_ConfigManagerInstance 1` forces a rescan.
