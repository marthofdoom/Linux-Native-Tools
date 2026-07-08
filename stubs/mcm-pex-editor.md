# SkyUI MCM Editor, direct .pex patching (GUI)

Status: **first iteration shipped** — https://github.com/marthofdoom/MCM-Editor
(local: `../MCM-Editor/`). Goal: edit a SkyUI MCM (title, pages, row labels,
slider ranges, info text) on Linux via a GUI that **patches the compiled `.pex`
directly**, skipping the psc→wine-compile round-trip for simple text/layout
tweaks.

## First-iteration notes (from ground zero to pushed repo)

Built in a single focused session of roughly half an hour: empty folder →
byte-exact `.pex` parser/writer → SkyUI semantic layer → PySide6 GUI + headless
CLI → tests → public repo. **This doc set is what made that pace possible.**
Concretely, the repo saved the trial-and-error that would otherwise dominate:

- The one load-bearing insight — *every string reference is a `u16` index into
  a plaintext table, never an offset* — was stated up front, so the whole safe
  editing model (edit the table, leave the bytecode tail byte-identical) was
  obvious from minute one instead of discovered by corrupting files.
- Doctrine #1/#3 ("dump a working record; verify against ground truth, not the
  manual") pointed straight at the winning test: round-trip `parse→emit ==
  identical` over the **51,564** real `.pex` in the local modlists. That is the
  parser's correctness proof — it passed 51,564/51,564 — and it caught format
  mistakes far faster than reading a spec would have.
- The pillar-2 doc's SkyUI caching gotcha (`setstage SKI_ConfigManagerInstance
  1`, cold-load, per-save `ModName`/`Pages`) was lifted verbatim into the
  tool's apply-help text — no in-game rediscovery needed.
- The ASCII-only rule carried over directly as an input guard.

What still had to be *derived* (docs got us to the doorstep, not through it):
the exact debug-info / instruction operand layout and the opcode arg-count
table were modelled on Orvid/Champollion's reader, then proven by the
round-trip. And a real-world finding the docs didn't call out: ~90% of MCMs in
a load order (222/247 here) are legacy script-driven and editable this way; the
other ~10% are MCM Helper (JSON-driven) with no display text in the pex.

## Second iteration — one interface, every MCM style (shipped)

Grew the tool from a pex editor into an all-styles MCM text editor, again in a
single session. A `McmDocument` layer sniffs the file and hides where the text
lives:

- **Legacy pex** — patches the string table (iteration 1).
- **MCM Helper** — edits `config.json` (displayName, pageDisplayName, item
  text/help, enum options).
- **Translation-driven** — edits `Interface/Translations/<name>_english.txt`.

The load-order survey drove the design: **~80% of Skyrim MCM configs use
`$translation_key` tokens** (63/125 all-tokens, 38/125 mixed) whose real words
live in a translation `.txt`, so editing the pex/JSON alone would edit
invisible keys. Pex and JSON documents now auto-locate the mod's English
translation, resolve every token to its display text, and route edits back to
the translation file (the pex/JSON keeps the token). Page (tab) names — which
aren't handed to a display call — are found via `cmp_eq` in `OnPageReset`.

The doctrine that paid off again: **decide from a corpus scan, not intuition.**
The translation-token prevalence, the legacy-vs-MCM-Helper split, and the
page-name `cmp_eq` signal were all read off the 50k-file local corpus rather
than guessed, and the pex round-trip stayed 51,564/51,564 through the changes.

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
