# Linux-Native Skyrim Modding Tools

Reference documentation for building Skyrim SE (1.6.1170 / AE) mods **entirely
from Linux**, without xEdit, the Creation Kit, or a Windows box. Distilled from
the marth overhaul projects (MRO/MEO/MAO/MFO), which each ship an ESP + Papyrus
and/or a native SKSE DLL, all produced on Linux.

The **reference implementations** are the public sibling repos:

| Repo | Local path (this machine) | Public URL | Covers |
|---|---|---|---|
| MRO | `../Requiem-modification/` | https://github.com/marthofdoom/MRO | pillars 1–4 (ESP, Papyrus, CI DLL, hooks) |
| MEO | `../marth-enchanting-overhaul/` | https://github.com/marthofdoom/MEO | pillar 5 (instance data / hook-free), pillar 8 (ImGui) |
| MAO | `../marth-alchemy-overhaul/` | https://github.com/marthofdoom/MAO | pillar 9 (load-order-agnostic ESP + Synthesis/Mutagen patching) |
| MFO | `../marth-follower-overhaul/` | https://github.com/marthofdoom/MFO | pillar 6 (actor AI / packages / casting / combat targets) |

Also in the ecosystem: **[skyrim-linux-toolchain](https://github.com/marthofdoom/skyrim-linux-toolchain)**
(private) — running the build/patch toolchain (Synthesis, Requtificator, …)
fully native on headless Linux, driven by pointing at a modlist folder.

When a doc says "see `tools/foo.py`", it means that file inside the relevant repo
(e.g. `../Requiem-modification/tools/foo.py`, i.e.
`github.com/marthofdoom/MRO/blob/main/tools/foo.py`). Read the real code; these
docs explain the *why* and the traps.

## The pillars

| # | Doc | What it lets you do without Windows tooling |
|---|-----|---------------------------------------------|
| 1 | [esp-without-xedit.md](esp-without-xedit.md) | Generate a valid ESP/ESL from Python by emitting raw records (incl. PACK, marker PERKs). No xEdit, no CK. |
| 2 | [papyrus-on-linux.md](papyrus-on-linux.md) | Compile `.psc` → `.pex` under Proton's wine. |
| 3 | [native-dll-via-github-actions.md](native-dll-via-github-actions.md) | Build a CommonLibSSE-NG SKSE DLL on GitHub Actions (Windows runner) driven from a Linux push. |
| 4 | [known-hooks.md](known-hooks.md) + [hook-site-verification.md](hook-site-verification.md) | Catalog of proven engine hook sites (call-site + vtable-index) + how to prove a site is safe before shipping. |
| 5 | [instance-data-and-events.md](instance-data-and-events.md) | Hook-free native mods: per-instance item data, event sinks, message boxes, co-save discipline, threading. (MEO) |
| 6 | [actor-ai-and-packages.md](actor-ai-and-packages.md) | Command an NPC you don't own: packages via quest aliases, casting, combat-target steering. (MFO) |
| 7 | [commonlibsse-ng-traps.md](commonlibsse-ng-traps.md) | CommonLibSSE-NG library bugs that compile into your DLL and crash at *your* offset. (cross-cutting) |
| 8 | [imgui-overlay-and-input.md](imgui-overlay-and-input.md) | In-process ImGui overlay + input inside the game's DX11 present. (MEO) |
| 9 | [load-order-agnostic-esp.md](load-order-agnostic-esp.md) | Ship a plugin that behaves on *any* load order: dynamic-or-drop + Synthesis/Mutagen patching. (MAO) |
| 10 | [resaver-headless-save-inspection.md](resaver-headless-save-inspection.md) | Read a `.ess` directly on Linux (ReSaver-as-library + own ACHR parse): dump any NPC's inventory / worn / phantom items headlessly. Tool: [tools/DumpNPCInventory.java](tools/DumpNPCInventory.java). |

## GUI tools (spun out of the stubs)

- **MCM editor** — graduated to its own repo:
  [marthofdoom/MCM-Editor](https://github.com/marthofdoom/MCM-Editor)
  (Linux-native GUI that patches compiled `.pex` string tables directly). Design
  stub: [stubs/mcm-pex-editor.md](stubs/mcm-pex-editor.md).
- **Perk-tree editor** — in progress (`../Perk-Tree-Editor/`); visual PERK/AVIF
  editor, ESP out via the pillar-1 approach. Design stub:
  [stubs/perk-tree-editor.md](stubs/perk-tree-editor.md).

## Core doctrine (applies everywhere)

1. **Never trust format docs — dump a working record.** The engine rejects
   malformed records *silently*. Dump a vanilla record that already does what
   you want and match the structure it reveals: which subrecords appear, in what
   order, and the byte layout the engine expects. This is reverse-engineering
   the *format the engine requires* from a live example — a factual spec, not
   the manual's (often wrong) description of it.
2. **Model technique on verified references, not memory or docs.** Build
   scaffolds, hook thunks, and record structures are worked out by studying
   known-good, openly-documented sources — the SKSE source for an engine call
   sequence, a maintained mod's *published* hook-site address, the
   CommonLibSSE-NG plugin template — then implemented and proven here. What you
   carry over is the verified *fact* (which hook site, which engine function,
   which byte layout the engine accepts), re-derived and checked against ground
   truth. The code is your own; you are not lifting anyone's mod.
3. **Verify against ground truth, not the manual.** Hook sites are checked
   against the *running* game's memory; the Address Library DB is validated
   against crash-log addresses; ESPs are audited against the scripts.
4. **ASCII only** in all Papyrus sources and user-facing strings.

## Portability warning

The MRO tools hardcode this machine's paths (the LoreRim MO2 instance, Proton
Hotfix, the Stock Game folder). A new project should lift the *logic* and
parameterize the paths (env vars / a config file) rather than copy the
constants. Each doc flags the machine-specific bits.
