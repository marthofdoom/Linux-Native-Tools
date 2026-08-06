# Pillar 9 — Load-order-agnostic mods: dynamic-or-drop + Synthesis/Mutagen patching

How to ship a plugin that behaves correctly on **any** load order, not just the
one it was generated against. Distilled from MAO (aims for wide compatibility,
not just Requiem). Reference: `../marth-alchemy-overhaul/Docs/DYNAMIC_OR_DROP.md`,
`Docs/PERK_TREE_RECON.md`, `Docs/ARCHITECTURE.md` §9, and the shared
`installer/` C#.

---

## 1. The dynamic-or-drop rule

**Anything whose value was scanned from *this machine's* load order at
ESP-generation time must become dynamic (computed in-game at runtime) or be
dropped before release.** A plugin that bakes a load-order-derived value
silently misbehaves elsewhere, and the failure is invisible to the author who
only tests their own setup. Three worked resolutions:

- **Made dynamic (DLL):** vendor-gold doubling was a baked LVLI override scanned
  from the winning plugin → replaced by a native DLL that doubles the 13 vanilla
  `VendorGold*` lists in memory at `kDataLoaded` (see
  [known-hooks.md](known-hooks.md)).
- **Made dynamic (live GMST):** a DR curve kink baked from the load order → read
  live from `fMaxArmorRating` / `fArmorScalingFactor` each update, with an MCM
  slider as the authority.
- **Dropped:** a boss-readiness heuristic that couldn't be made truly
  per-load-order was *removed* rather than shipped half-accurate.

Generation-derived **defaults** are acceptable only when an MCM slider is the
runtime authority. Emit *static* records only against guaranteed base-game
(`Skyrim.esm`) forms.

## 2. Patch the WINNING override; classify by BEHAVIOR, not name

To modify a record other mods also edit, don't ship a from-scratch ESP — emit a
pure-override ESL written by a **Mutagen/Synthesis patcher** that reads the
*winning*, conflict-resolved record at install time and rewrites it. Only the
winner is what the engine actually uses.

**Classify records by their functional fields, never by EditorID/name.** MAO
strips craft perks by the presence of specific perk-entry-point types
(`ModAlchemyEffectiveness`, `ModPotionsCreated`,
`ModInitialIngredientEffectsLearned`, `PurifyAlchemyIngredients`,
`ModPoisonDoseCount`) or effect conditions requiring
`GetIsObjectType == Ingredient`. A name list dies instantly (LoreRim's winning
tree is 20 Requiem+Ordinator nodes). When walking a linked-record chain (NNAM
rank chains), carry a **seen-set cycle guard**; give kept perks with dangling
`HasPerk` conditions overrides; reparent orphaned kept perks to root.

## 3. One codebase = standalone CLI *and* Synthesis patcher

Ship the patch logic once and compile it into both a standalone C# CLI (verbs
like `stats`/`tree`/`write-patch`, resolving the load order from an MO2 profile
**or** a plain game root) and a `Synthesis` patcher, via MSBuild
`<Compile Include>` — no fork, byte-identical output. A Synthesis-only tool can't
be run or debugged standalone; a fork drifts.

- **Always exclude your own output ESP from the read** — reading
  `MyMod - Patch.esp` compounds the patch on every re-run.
- **Never trust the host tool's load order.** Synthesis's supplied order **omits
  Creation Club plugins** (MEO measured ~24% content loss) — build your **own**
  tier load order that explicitly includes `Skyrim.ccc`.

## 4. Shipping a user-runnable Windows tool on Wine/Deck

- **All setup/filesystem code must sit inside a top-level try/catch.** A Wine
  double-click console vanishes with **no message** when setup code (e.g. a
  load-order file read) throws *outside* the handler — an un-diagnosable bug
  report. Wrap the very first line of any interactive path in a try/catch that
  prints and pauses.
- **Two files that must reference the same project string must be asserted equal
  in CI.** The `.sln` `SelectedProject` and `assets/*.synth` `SelectedProject`
  must match verbatim (backslashes included) or Synthesis silently fails to find
  the project — a backslash mismatch shipped a sibling's install broken for three
  versions. Build *through the solution* in CI exactly as Synthesis does.

## 5. Own-flag marker PERKs (DLL-driven perks, optional tree patch)

Instead of editing vanilla perks, ship your **own** effect-less PERK records
(frozen FormID band) carrying only a `CTDA GetBaseActorValue(Skill) >= req` gate;
the DLL reads `HasPerk` and applies all effects itself. Two modes from one build:
with the tree-patch ESP present, perks are bought in the rebuilt constellation;
without it, the DLL auto-grants each perk at the same threshold the CTDA encodes
(the CTDA and the DLL threshold are one contract).

- **Format trap:** a ranked-perk NNAM chain must declare `numRanks` matching the
  vanilla convention (byte-dumped from `Alchemist00` = 5). With `numRanks=1` the
  Skills UI advertises an already-held rank ("3/3" at rank 3).
- The perk FormID band is a frozen **generator ↔ DLL ↔ installer contract** —
  reordering silently rebinds perks to wrong effects; forms are only ever
  *added*.

## 6. FOMOD structural traps (silent failure / MO2 crash)

- Every `<group>` needs a `<plugins order="Explicit">` wrapper — omitting it
  makes MO2 throw an opaque "invalid vector subscript" dialog on install.
- Every no-op option (installs nothing) needs an explicit empty `<files/>`
  element or it misbehaves.

---

## Mutagen-on-Linux notes

- **Mutagen decodes embedded plugin strings as Windows-1252 by default** — a
  UTF-8/CJK plugin renders mojibake. Pass
  `NonLocalizedEncodingOverride = MutagenEncoding._utf8_1252` so genuine UTF-8
  decodes and legacy Western still falls back. Mojibake in a Synthesis log is an
  **encoding** signal, never a structural failure — but the same strings bake
  into any record NAMEs the patcher emits, so it is silent data corruption for
  non-Western orders if left unfixed.
- Don't set `IsSmallMaster` (ESL flag) on the shared `state.PatchMod`.
- **Presence is not integrity** — resolve a frozen/winning record; never gate on
  a plugin's *name*.
