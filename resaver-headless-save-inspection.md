# Headless save inspection: dump an NPC's inventory from a .ess on Linux

Read a Skyrim SE savegame directly — no game, no GUI, no Windows — and answer
questions like *"is item X actually in follower Y's inventory, at what count,
and is it worn?"*. This removes the game's runtime (Papyrus/native getters) as
the only witness: you verify what the **save file itself** says.

Working reference: `tools/DumpNPCInventory.java` (in this repo). Proven on a
Tuxborn save against Inigo (14-entry inventory, custom framework follower) and
the player (72 entries, full + light + mid-order plugins all resolving).

## TL;DR — the working command

```bash
# one-time: pull the save off the Deck (space in profile path breaks scp; use cat-over-ssh)
ssh deck@marthdeck "cat '/home/deck/Games/Tuxbornrc1/profiles/Tuxborn MCO - Low-Deck/saves/<name>.ess'" > local.ess

# dump: <save> <placed-actor RefID in hex> [-v]
java -cp /mnt/gaming/modlists/ReSaver/ReSaver.jar \
     /mnt/gaming/modlists/Projects/Linux-Native-Tools/tools/DumpNPCInventory.java \
     local.ess 6E008AE9
```

Sample output (Inigo, placed ref `6E008AE9`):

```
INVENTORY: 14 changed entries (array offset 198)
  FormID    count  flags        plugin                       name/extras
  0001FD77  2      WORN         Skyrim.esm                    extras=[OutfitItem, Worn, OutfitItem]
  6E000803  1      WORN         Inigo.esp                     extras=[Worn]
  0000000F  50                  Skyrim.esm
  000139B1  0      WORN PHANTOM Skyrim.esm                   "Frost I Ebony Sword" ench=FF001BAD charge=333 ...
  6E0AA092  0      WORN PHANTOM Inigo.esp                     extras=[Worn]
  ...
```

- `count` is the **delta/override vs. the base record's container** — negative
  counts (base items removed) and zero counts are normal.
- `WORN` / `WORN-L` = the save carries an ExtraWorn/ExtraWornLeft marker.
- `PHANTOM` = count <= 0 **and** worn: the actor renders the item but the
  inventory holds zero of it.
- Source plugin is resolved through the save's own plugin table (handles
  `FE`-prefixed light plugins correctly — better than eyeballing `FormID>>24`).

## Environment constraints (Steam Deck dev box)

- Only a **JRE** (OpenJDK 21) is installed — no `javac`. But the JRE ships the
  `jdk.compiler` module, so the **single-file source launcher** works:
  `java Foo.java args` compiles + runs in memory. Everything here is therefore
  a *single* `.java` file; no jar building, no multi-file compilation.
- ReSaver (FallrimTools) lives at `/mnt/gaming/modlists/ReSaver/ReSaver.jar`
  (+ `ReSaver-sources.jar` next to it — unzip it when you need to read the
  parser internals). We run it **headless as a library**: `-cp ReSaver.jar`
  gives the source-launcher file access to `resaver.ess.*` (ESS, ChangeForm,
  RefID, VSVal, GeneralElement...). The Swing GUI is never touched.
- `GeneralElement`'s `read*` helpers are `public` but its constructor is
  `protected` — a one-line subclass (`class G extends GeneralElement {}`)
  unlocks the whole low-level reader toolkit.
- `ChangeFormInitialData` is package-private; its few fixed layouts are
  trivially replicated (see the header section of the tool).

## Why ReSaver's own parser can't do this (the ACHR EXTRADATA trap)

The obvious route —

```java
ESS ess = ESS.readESS(path, new ModelBuilder(new ProgressModel(1))).ESS;
ChangeFormACHR achr = (ChangeFormACHR) cf.getData(Optional.empty(), ess.getContext(), true);
achr.INVENTORY  // <-- null
```

— fails on real modded saves with `Failed to read ACHR <refid>`. Root cause:
in `ChangeFormACHR`'s constructor the **EXTRADATA block is read before the
INVENTORY array**, and ReSaver's per-type extra-data lengths
(`ChangeFormExtraDataData`) are wrong/missing for several types that occur in
practice. The parse either throws (unknown type → `INVENTORY` stays null) or —
worse — *silently desyncs* and reads garbage as the inventory count.

Concrete gaps found (Tuxborn save, SE 1.6.x):

1. **Wrapper types beyond 12.** Extra types `4, 8, 12` are "wrapper" entries
   carrying `type/4` sub-entries (ReSaver: `ExtraExtraData1..3`). The pattern
   continues: **16 → 4 subs, 24 → 6 subs**. ReSaver has `//case 16:` commented
   out and misdefines 24. (28 is *not* a wrapper — it really is
   ReferenceHandle, verified in the same data.)
2. **Type 35 (Rank) is 4 bytes** — RefID (3) + one rank byte. ReSaver reads
   only the RefID, leaving a stray byte that desyncs everything after it.
3. The top-level (actor-level) EXTRADATA contains further mis-lengthed types
   (e.g. ReSaver's type-135 "TEST" guess), so even with 1–2 fixed you cannot
   trust the stream position where EXTRADATA "ends".

## How the tool gets past it: parse what you need, scan-validate the start

The tool uses ReSaver only for what it's good at (LZ4 decompression, the
form-ID table, `RefID` resolution to FormID + plugin, VSVal decoding) and
parses the inventory itself:

1. **Replay the fixed header** exactly as `ChangeFormACHR` does: INITIAL
   (layout picked by change-flag bits: CREATED→5, PROMOTED/CELL_CHANGED→6,
   HAVOK_MOVE/MOVE→4, else 0), optional HAVOK, 8 unknown bytes, optional
   form-flags/base-object/scale.
2. **Do NOT walk EXTRADATA.** Instead, brute-force the inventory array start:
   try *every* offset after the header, and accept only offsets where a
   **strict full parse succeeds** — vsval count `N` (1..4096), then exactly
   `N` items of `[RefID(3)] [int32 count] [vsval M] [M extra entries]` with
   sane values (valid non-zero RefID, |count| bounded, every extra type
   either known or cleanly parsed). A wrong offset dies within an item or
   two; the real array parses end-to-end. If several offsets survive, the one
   with the most items wins (the others are tiny coincidences — on the Inigo
   save: the real hit `[198, 14 items]` vs. noise like `[233, 1]`).
3. **Extra-data reader with the fixes** from the previous section, falling
   back to ReSaver's `ChangeFormExtraDataData` for types we don't handle.
   Worn detection recurses into wrappers. TextDisplayData (153) yields custom
   display names ("Frost I Ebony Sword"), Enchantment (155) the enchantment
   FormID + charge.

This "strict-parse-as-validator" scan is the transferable trick: any time a
save structure sits *behind* a block ReSaver can't length, you can still
recover it if the structure itself is self-checking enough (counted arrays of
RefID-led records are).

### Verifying a finding (worked example)

Question: does Inigo (`6E008AE9`) really hold vanilla `Ancient Nord Helmet`
(`0001FD77`), or is the game's `GetWornArmor` reporting a ghost?

Save says: `0001FD77 count=2 WORN` with extras `[OutfitItem, Worn, OutfitItem]`
— present, two copies, one worn-as-outfit-item. Not a ghost. Meanwhile the same
dump shows what real phantoms look like: `000139B1` ("Frost I Ebony Sword",
enchanted, count **0**, worn) and three `Inigo.esp` count-0 worn entries.
That contrast is exactly what this tool exists to establish.

## Finding the RefID to ask about

- A placed actor from a mod: load-order-prefixed form ID of the ACHR record
  (e.g. Inigo's placed ref `xx008AE9` with `Inigo.esp` at index `6E` →
  `6E008AE9`). Get the index from the modlist's `plugins.txt` / MO2, or from
  an in-game console click at any earlier point.
- The player is always `14`.
- No ChangeForm for the ref = the reference was never modified — its inventory
  is exactly the plugin-defined default (that itself is a finding).

## Extending

- Other change-form types (NPC_, container REFRs...) follow the same recipe:
  replicate the fixed prefix from the matching `ChangeForm*` source, then
  scan-validate the counted array you care about.
- When you hit a new unknown extra type, do what cracked 16/24/35: hexdump the
  region (`cf.getBodyData()` is the decompressed little-endian body), find a
  *repeating* record you can anchor on (item cadence, a known string, the
  actor's own RefID embedded in UniqueId payloads), and diff two instances of
  the pattern to get the length. Then add a case to `readExtra`.
- ReSaver sources: `unzip ReSaver-sources.jar -d resaver-src`, read
  `resaver/ess/ChangeFormACHR.java` (read order), `ChangeFormExtraDataData.java`
  (the per-type table — treat lengths as *claims*, not facts),
  `RefID.java` (3-byte refid → FormID/plugin resolution), `VSVal.java`.
