# STUB — Linux-native Perk Tree Editor (GUI)

Status: **not started** — design stub. Goal: author and edit Skyrim perk trees
visually on Linux, emitting a valid ESP, without the Creation Kit.

## Why
The CK's perk-tree UI is the only mainstream way to lay out perk node positions
and connections. It's Windows-only and heavy. Everything it produces is just
records we can already emit from Python ([../esp-without-xedit.md](../esp-without-xedit.md)).

## What it edits
- **PERK** records — the perks themselves (entries, conditions, effects, DATA).
- **AVIF** records — the actor-value/skill "trees" that host perk nodes; node
  layout lives here (per-perk grid position, and the connection lines between
  nodes).
- Optionally **FLST** perk lists for grouping.

## MVP scope
1. Parse an existing plugin's PERK + AVIF with the pillar-1 record walker
   (reuse `dump_record.py`'s parser).
2. Render the tree: nodes at their AVIF grid coordinates, connection edges.
3. Edit: drag nodes (writes grid coords), add/remove connections, edit perk
   name/description/rank count, edit entry point effects.
4. Emit ESP via the pillar-1 emitter. Run `audit_esp.py`-style checks on output.
5. Round-trip test: import a vanilla tree, export unchanged, diff bytes ==
   identical (proves the parser+emitter are lossless before trusting edits).

## Stack suggestion
- Python + a GUI toolkit already available on Linux: **PySide6/Qt** (mature node
  canvas via `QGraphicsScene`) or Dear PyGui. Avoid web/Electron.
- Reuse the sibling repo's record primitives; do **not** rewrite the byte layout
  from docs — dump vanilla PERK/AVIF and mimic.

## Known traps to carry over
- PERK: no trailing PRKF after the last section; `playable=1 hidden=0`.
- FormIDs: own-file prefix + ESL `0x800`–`0xFFF`; never renumber post-release.
- Silent loader rejection → always verify a generated perk shows in `help <edid>
  4` in-game, and diff against a vanilla twin when it doesn't.

## Open questions
- AVIF node-position subrecord layout: dump several vanilla skill trees and map
  the coordinate fields before building the canvas.
- Condition (CTDA) editing UI is the hard part — start read-only, edit later.
