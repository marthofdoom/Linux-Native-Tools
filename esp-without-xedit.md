# Pillar 1 — ESP/ESL from Python, no xEdit or CK

You do not need xEdit or the Creation Kit to author a plugin. A plugin file is
just nested records; you can emit the bytes directly from Python. MRO's
`MRO_GenerateESP.py` builds a complete ESL-flagged ESP (TES4 header, GMST, GLOB,
FLST, PERK, MGEF, SPEL, QUST with VMAD script bindings) this way.

Reference: `../Requiem-modification/MRO_GenerateESP.py` and
`../Requiem-modification/docs/MANUAL_MOD_CREATION_GUIDE.md`.

## Record model in ~30 lines

- A record is `TYPE(4) + dataSize(u32) + flags(u32) + formID(u32) + versionInfo(6) + body`.
- The body is a concatenation of subrecords: `TYPE(4) + size(u16) + bytes`.
- Strings are usually zero-terminated (`zstr`). Fixed structs (DATA, etc.) are
  packed little-endian.
- Groups (`GRUP`) wrap records of a type. Some record types must live in a GRUP;
  MRO nests QUST/PERK/etc. under their GRUPs.

Helper primitives to build first (MRO has these): `subrec(type, bytes)`,
`zstr(s)`, `record(type, formid, flags, body)`, `grup(...)`.

## The non-negotiable workflow: dump, then mimic

```
tools/dump_record.py <EDID> [--type PERK] [--plugin path.esp]
```

`dump_record.py` walks any plugin (handles zlib-compressed records), finds a
record by EditorID, and prints every subrecord with sizes and hex. **Before
creating any new record type, dump a *vanilla* (base-game) record that already
does what you want and mirror its subrecord list, order, and byte layout
exactly — that structure IS the format the engine requires.** You are
replicating the engine's expected format from Bethesda's own base-game data
(the authoritative spec) and then filling in your own values; where a specific
vanilla byte is reproduced it is because that byte is format-critical (an
archetype, a flag), i.e. a fact about the format, not borrowed authorship. The
engine silently drops records whose layout it doesn't like — there is no error.
The universal debugging method is: dump yours and the vanilla twin, diff every
subrecord (type, order, size, bytes).

## Traps that cost real time (all silent failures)

| Symptom | Cause | Fix |
|---|---|---|
| Record missing from `help <edid> 4` in-game but parses fine offline | Loader rejected the layout | Diff vs vanilla twin |
| Ability does nothing / not in Active Effects | SPEL `SPIT` spell type 3 (Lesser Power); must be **4** (Ability). Or missing OBND/ETYP/DESC | Copy a vanilla ability's subrecords |
| Fortify-AV ability applies but the value never moves | MGEF archetype 0 (Value Modifier) no-ops for fortify-from-ability | Use archetype **34 (Peak Value Modifier)** + Recover flag; copy a vanilla fortify MGEF's DATA byte-for-byte |
| PERK rejected by loader | trailing PRKF after the last section, or wrong playable/hidden flags | No trailing PRKF; `playable=1 hidden=0` |
| One record silently deletes a real record | own FormIDs collided with a master's FormID space | Use the own-file master-index prefix |
| Start-game quest never starts on existing save | non-Run-Once quest with no SEQ | Emit `SEQ/<plugin>.seq` listing start-game-enabled quests |

## FormID rules for an ESL-flagged plugin

- Own records use the **own-file master-index prefix**: with N masters the prefix
  is `0x<N>` in the high byte (MRO: 5 masters → `0x05000000 | localID`).
- ESL-legal local ID range is **`0x800`–`0xFFF`**. Stay inside it or the ESL
  flag is a lie and the game truncates.
- **Never change a FormID after release** — saved games bind to them; changing
  one orphans script instances and globals.

## Audit before every test

```
tools/audit_esp.py
```

Validates, against the `.psc` sources:
1. every VMAD script property has a matching `Property` in the script (orphans
   unfill silently at runtime),
2. every non-`AutoReadOnly` script `Property` is wired in the VMAD (unwired =
   `None` at runtime),
3. every shipped `.pex` is attached to a record and vice-versa,
4. all own records use the correct FormID prefix and sit in `0x800`–`0xFFF`.

A FAIL here is guaranteed runtime breakage. Run it after every generator or
script-property change. (MRO's audit reads a fixed path
`MRO-nofomod/MRO.esp` — parameterize this in a new project.)

## Machine-specific bits to parameterize

- `dump_record.py` hardcodes `.../Stock Game/Data/Skyrim.esm`.
- `audit_esp.py` hardcodes the ESP/scripts paths.
Lift the parsing logic; inject paths.
