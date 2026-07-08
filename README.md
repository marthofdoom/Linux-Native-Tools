# Linux-Native Skyrim Modding Tools

Reference documentation for building Skyrim SE (1.6.1170 / AE) mods **entirely
from Linux**, without xEdit, the Creation Kit, or a Windows box. Distilled from
the `marth Requiem Overhaul` (MRO) project, which ships an ESP + Papyrus + a
native SKSE DLL, all produced on Linux.

The **reference implementations** are the public sibling repos:

| Repo | Local path (this machine) | Public URL | Covers |
|---|---|---|---|
| MRO | `../Requiem-modification/` | https://github.com/marthofdoom/MRO | pillars 1–4 |
| MEO | `../marth-enchanting-overhaul/` | https://github.com/marthofdoom/MEO | pillar 5 (instance data / hook-free) |
| MAO | `../marth-alchemy-overhaul/` | https://github.com/marthofdoom/MAO | load-order-agnostic ESP + native (in progress) |

When a doc says "see `tools/foo.py`", it means that file inside the relevant repo
(e.g. `../Requiem-modification/tools/foo.py`, i.e.
`github.com/marthofdoom/MRO/blob/main/tools/foo.py`). Read the real code; these
docs explain the *why* and the traps.

## The four pillars

| # | Doc | What it lets you do without Windows tooling |
|---|-----|---------------------------------------------|
| 1 | [esp-without-xedit.md](esp-without-xedit.md) | Generate a valid ESP/ESL from Python by emitting raw records. No xEdit, no CK. |
| 2 | [papyrus-on-linux.md](papyrus-on-linux.md) | Compile `.psc` → `.pex` under Proton's wine. |
| 3 | [native-dll-via-github-actions.md](native-dll-via-github-actions.md) | Build a CommonLibSSE-NG SKSE DLL on GitHub Actions (Windows runner) driven from a Linux push. |
| 4 | [known-hooks.md](known-hooks.md) + [hook-site-verification.md](hook-site-verification.md) | Catalog of proven engine hook sites + how to prove a site is safe before shipping. |
| 5 | [instance-data-and-events.md](instance-data-and-events.md) | Hook-free native mods: per-instance item data (rename/enchant that persists in the .ess), event sinks, native message boxes, co-save discipline. From the MEO project. |

## Future GUI tools (stubs)

- [stubs/perk-tree-editor.md](stubs/perk-tree-editor.md) — visual PERK/perk-tree editor, ESP out via the pillar-1 approach.
- [stubs/mcm-pex-editor.md](stubs/mcm-pex-editor.md) — SkyUI MCM editor that patches compiled `.pex` directly.

## Core doctrine (applies everywhere)

1. **Never trust format docs — diff a working record.** The engine rejects
   malformed records *silently*. Dump a vanilla record that already does what
   you want, copy its subrecord list / order / byte layout exactly.
2. **Adapt working sources, don't write from memory.** Build systems, hook
   thunks, and record layouts are copied from known-good templates and mods,
   then verified — not reconstructed from documentation.
3. **Verify against ground truth, not the manual.** Hook sites are checked
   against the *running* game's memory; the Address Library DB is validated
   against crash-log addresses; ESPs are audited against the scripts.
4. **ASCII only** in all Papyrus sources and user-facing strings.

## Portability warning

The MRO tools hardcode this machine's paths (the LoreRim MO2 instance, Proton
Hotfix, the Stock Game folder). A new project should lift the *logic* and
parameterize the paths (env vars / a config file) rather than copy the
constants. Each doc flags the machine-specific bits.
