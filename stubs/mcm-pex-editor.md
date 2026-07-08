# STUB — SkyUI MCM Editor, direct .pex patching (GUI)

Status: **not started** — design stub. Goal: edit a SkyUI MCM (title, pages, row
labels, slider ranges, info text) on Linux via a GUI that **patches the compiled
`.pex` directly**, skipping the psc→wine-compile round-trip for simple text/layout
tweaks.

## Why "edit pex directly"
The `.pex` format keeps a **plaintext string table** — you can see every label,
page name, and ModName with `grep -a`. Many MCM edits (rename a page, fix casing,
change a row label, tweak an info string, adjust a slider's min/max/step
literal) are just string/constant swaps. Patching the pex avoids needing the
Papyrus compiler for those, and makes a GUI feasible.

Full logic changes (new options, new handlers) still need pillar-2 compilation —
this tool is for the text/layout layer, not rewriting `OnOptionSelect`.

## What we already know about .pex (from MRO work)
- The string table is plaintext and greppable; SkyUI reads `ModName` + the
  `Pages` array from `OnConfigInit`.
- **SkyUI caches `ModName`/`Pages` per save**; after patching, the game needs a
  cold load + `setstage SKI_ConfigManagerInstance 1` to re-register title/tabs.
  Rendered rows regenerate on page open. Document this in the tool's "apply"
  help text so users aren't confused by stale UI.

## MVP scope
1. Parse `.pex` header + string table + object/function tables (the format is
   documented in the Champollion decompiler and the SKSE `pex` spec — mimic a
   known-good parser, don't guess).
2. Surface editable strings safely: replacing a string means updating the string
   table and any length prefixes; if a replacement changes length, the table and
   offsets must be rebuilt consistently.
3. GUI: list MCM pages/rows/labels; edit fields; preview the diff.
4. Write back a valid `.pex`; verify by `grep -a` for the new strings and by
   loading in-game.
5. Round-trip test: parse→emit unchanged == byte-identical before trusting edits.

## Stack suggestion
- Python + **PySide6/Qt**. A `.pex` reader/writer library (or port Champollion's
  read side) is the core dependency; build the GUI on top.
- Cross-check every write by recompiling the equivalent `.psc` once and diffing,
  to validate the emitter against the real compiler's output.

## Traps
- Constants like slider ranges may be encoded in function bytecode, not the
  string table — those are harder than label swaps; scope the MVP to string-table
  edits first.
- ASCII only for any new strings (same rule as pillar 2).
- Never change a script's name/Properties this way — that desyncs the VMAD
  bindings in the ESP (`audit_esp.py` would catch it).
