# idmap -- own id -> RVA table for SkyrimSE 1.7.104, by disassembly

Purpose: we do NOT use the Nexus Address Library for 1.7.104 (marth, 2026-10-04: "we are forking our own addresslib").
Every Address Library id our builds use (AE column = the 1.6.1170 ids) is mapped from the 1.6.1170 RVA to the 1.7.104
binary using only our own copies of both executables. There is no 1.7.104 install to field-test on, so each mapping carries
its own evidence and a crosscheck.

Clean room: alandtse CommonLibSSE-NG 7.x and its 1.7.99 fixture are NOT inputs. Symbol tables come from our MIT fork
(`_commonlib/mit-3.7-fork`, branch `mit-3.7`). The only address data read is the 1.6.1170.0 Address Library
(`versionlib-1-6-1170-0.bin`, NOT `-0-1`; the tool asserts id 38048 = 0x6A35F0) and the two executables.

## Files
- `collect_ids.py`  enumerates the ids (ours = MFO+APMF constructs, `cl` = CommonLib-internal reached by name match +
  transitive closure over the fork's src/ and inline include/RE bodies, `infra` = MemoryManager/BSFixedString/RTDynamicCast/
  logger, plus APMF's hand tables and raw AE RVA literals). Writes `ids.json`.
- `pe_index.py`     per-binary index: functions from `.pdata` (cold/split regions chained back to their head), capstone
  disassembly, masked-byte hash (rel32 branch targets and rip-relative displacements wildcarded), shape hash, call/ref lists.
- `idmap.py`        the mapper, crosschecks, ground-truth validation, self-test, outputs.
- `collect_fork_ids.py`  (full-fork run) enumerates EVERY id literal of the MIT CommonLib fork (`REL::ID`, `RELOCATION_ID`, `REL::RelocationID`,
                    `REL::VariantID` incl. the declaration form, and the `Offsets*.h` / `VTABLE_*` / `RTTI_*` / `NiRTTI_*` tables; AE column, ae==0 skipped),
                    optionally unioned with MFO/APMF sources (`--own`) and an earlier ids.json (`--merge`). Records the fork symbol and, for VTABLE_*,
                    the array index.
- `symname.py`      binds fork symbol names to TypeDescriptors by name (llvm-undname on our own executables, never an external table).
- `final_states.py` classifies every row MAPPED / REMOVED / INLINED / HARD.
- `se_bridge.py`    locates seats that only have 1.5.97 ids (APMF `EquipSink.Path.*`) in the 1.6.1170 and 1.7.104 images, starting from the 1.5.97 binary.
- `run.sh`          reproduces everything (about 8 minutes, one process; the full-fork run is `run-fork-full.sh`).

## Method ladder (first that holds wins)
1. EXACT       RTTI by exact mangled name (vtables are matched by class name PLUS the base-class subobject name found through
               the ClassHierarchyDescriptor displacement, never by raw this-offset, because 1.7.104 adds a base to
               PlayerCharacter); TypeDescriptors by name; import thunks by imported symbol name.
2. UNIQUE-SIG  the head region of the function, masked, hits exactly once in the 1.7.104 `.text` AND once in the 1.6.1170
               `.text` (raw regex scan of both images, independent of the function index). Short bodies (<24 fixed bytes)
               need a passing crosscheck; bodies with nothing to crosscheck need >=64 fixed bytes and >=16 insn.
               `sig:shape` (mnemonic+operand-kind sequence) and `sig:prefix` (first 10 insn) are accepted only with a passing
               crosscheck of >=3 independent data points and a size-ratio bound.
3. XREF        `xref:identical-caller` (a caller whose masked bytes, or instruction shape + call offsets, are identical in both
               images makes the same call, so its target is the counterpart; all such callers must agree), `xref:graph`
               (>=3 independent anchored callees/callers/strings point at one unique 1.7 function), `xref:global` (data globals:
               mapped functions that read the global at the same offset/ordinal agree on one target, same section),
               `thunk` (5-byte jmp stub, exactly one E9 stub per image), vtable-slot partner of an RTTI-exact pair.
4. UNRESOLVED  anything weaker, or a candidate whose crosscheck failed (the candidate is named in `evidence`).
Section/neighbour-delta is printed as `neighbour-delta hint` only and never decides anything.

## Methods added for the full-fork run (2026-10-04)
Every accepted row still needs a unique hit plus a passing crosscheck. Nothing below weakens that; each method is measured by a self-test.
- EXACT `rtti:typedescriptor(anon)` / vtable anon-fallback: anonymous-namespace classes carry a per-build hash (`?A0x74e2f8f3`); the
  hash-normalized key must be unique in both images. Class hierarchies are compared hash-normalized (names are read up to 1200 chars).
- Vtable slot layout changes (slot count differs, or anchored slots disagree) are reported as `SLOT LAYOUT CHANGE` / `SLOT CONTRADICTION`: the
  vtable identity rests on the exact class name + base subobject + identical hierarchy, so it stays EXACT/pass; a differing hierarchy still fails.
- EXACT `nirtti:name-thunk`: every `NiRTTI_X` object is built by a static-init thunk `lea r8,[base]; lea rdx,["Name"]; lea rcx,[this]; jmp ctor`
  (no .pdata, so invisible to the function index). The name string is unique per image; base-class name and ctor target must agree.
- NAME `name:symbol`: ids with no 1.6.1170 library row. The fork symbol (RTTI_/VTABLE_) must name exactly one TypeDescriptor per image; vtable
  array index = subobject number by this-offset (offset order == address order in both images, equal counts) and the RTTI vtable resolver must agree.
  Relies on the fork labelling the id correctly (self-test 4: 2 of 6630 library-known vtable ids have swapped symbols in the fork).
- UNIQUE-SIG `sig:L1-whole`: whole-function masked bytes (cold regions included) unique in both indexed images.
- UNIQUE-SIG `sig:L1-body+block`: unique body (>=32 fixed bytes, no callers/callees/strings) plus **block lock-step**: the nearest two L1-anchored
  functions on each side (flanks <= 0x2000 B apart) all moved by one delta and the candidate sits at exactly that delta. Self-test 6 measures the
  predictor alone (it is only ever a second fact).
- UNIQUE-SIG `sig:L1-body+slot`: second fact = the function sits in a vtable slot of an RTTI-exact pair with equal slot counts in which >=1 OTHER
  anchored slot agrees and none contradicts (the function never counts as its own evidence).
- UNIQUE-SIG `sig:L1-body+callee` / `+block+callee`: duplicate bodies (k copies in both images) paired through an anchored callee that only this
  copy calls (exactly one 1.7 copy calls its counterpart), and/or the block lock-step delta.
- XREF `xref:identical-caller` now also aligns call sites in callers that CHANGED, by a unique local code window (>=3 insns each side, >=20 fixed
  bytes, unique in the counterpart function); the same window alignment feeds `xref:global`.
- XREF `xref:global` voters: anchored functions first, up to 300 sites; a single voter is accepted only when it is an `l1` alignment (>=20 insn) or an
  `ord` alignment (>=30 insn) in a function mapped with a passing crosscheck. `xref:global+block` adds the data lock-step as the second fact.
- XREF `xref:graph` / `identical-caller` use a LENIENT crosscheck: a 1.7 body that ADDS anchored calls or strings is a changed function, not a
  contradiction, but only with >=3 forward data points (AE callees contained, callers still calling it, identical-caller sites, slot partner).
  Global operands are an extra vote kind (globals with <= 12 referencing sites).
- XREF `xref:fuzzy` / `xref:block+shape`: candidates from the votes + block lock-step; >=2 different kinds of independent evidence on one unique
  candidate, mnemonic-sequence similarity >= 0.75 (block+shape needs >= 0.90, size within [0.9,1.12], >=20 insn or one more vote kind), a margin over the
  runner-up and the lenient crosscheck.
- XREF `xref:table`: the function is a unique entry of a code-pointer table (CRT initializers, callback arrays); the nearest anchored entries on both
  sides are at the SAME gap in the 1.7 table (nothing inserted between), every slot between is a code pointer in both, and the 1.7 entry carries the
  same masked head.
- XREF `xref:getter-slot`: globals that code reaches only through leaf getters `lea|mov reg,[rip+X]; ret` (also no .pdata): the getter's vtable-slot partner
  in an RTTI-exact pair (>=3 anchored slots agree) is a getter of the same opcode reading the 1.7 address.
- XREF `xref:data-block`: no function voter exists; the globals that map from L1-anchored functions nearest on each side moved by one delta (adjacent
  flanks <= 0x400 B apart, >=3 of the 4 nearest agree), the target section is kept and the predicted 1.7 address has exactly as many referencing sites
  in functions with identical masked bodies.
- `xref_expand` walks callers/callees two levels from every still-unresolved function (synthetic leaf functions included) and retries.

## Final states (UNRESOLVED is no longer a final state)
`final_state` / `final_evidence` columns: MAPPED, REMOVED (older-library presence of the id, absence from the 1.6.1170 library and from the TypeDescriptor
tables of BOTH executables, exact and loose key; or class present in 1.6.1170 only), INLINED (the standalone body no longer exists; evidence = the
surviving referencers of the class vtable), HARD (methods tried listed; handed back).

## Crosscheck column (pass / fail / na)
For the mapped pair: anchored callees appear in order in the 1.7 callee list and the reverse, anchored callers still call
it, referenced strings are the same set, identical-caller sites agree, vtable-slot partner (RTTI-exact pair) agrees,
vtable class hierarchy is identical (or only grows, flagged `LAYOUT CHANGE`). `na` means there was nothing to compare
(no callees/callers/strings; mostly virtuals and TypeDescriptors) -- those rows rest on their method evidence alone.

## Validation (full-fork run)
`selftest-<tag>.md` holds seven measurements; every accept path of the ladder is exercised and **WRONG must be 0 everywhere**:
1. vtable-slot truth (slot i <-> slot i of RTTI-exact equal-count pairs, slot check disabled) -- original self-test.
2. anchor hold-out: 3000 L1-anchored functions removed from the anchors and re-derived by the xref methods only (own bytes unusable).
3. 2500 `.rdata` string globals; truth = identical string content (independent of the byte-alignment voting).
4. library-known RTTI/VTABLE ids re-derived from the fork symbol alone (`name:symbol` vs the library row).
5. leaf-getter route vs byte-voting route (5b: NiRTTI name-thunk vs both).
6. block lock-step predictor on every L1 anchor (flanks computed without the anchor).
7. data-block predictor + corroboration on globals the voting route resolves (each hidden from its own flanks).

1. Ground truth: every single-RVA row parsed from APMF `Docs/ADDRESS-TABLE-2026-09-15.md` (1.6.1170 -> 1.7.104) plus the
   explicit multi-value cells; the tool maps each AE RVA independently and compares. Report in
   `groundtruth-1.7.104.csv`. Any MISMATCH is a tool bug.
2. Self-test (`selftest-1.7.104.md`): AE functions that sit in RTTI-exact vtable pairs with equal slot counts form an
   independent truth set (slot i <-> slot i). The resolver is run on non-anchor ones with the slot check disabled.

## Outputs (in `_research/1.7.104-idmap/`)
`idmap-1.7.104.csv` columns: id, kind, scope (ours / cl / cl+ours / infra), name_use_site, rva_1_6_1170, rva_1_7_104,
confidence, method, evidence, crosscheck. `summary-1.7.104.md` has the counts, layout flags, mismatches and the unresolved
list. `ids.json` is the input list; `cache/` holds the function indexes.

## Known limits
- NAME rows trust the fork's symbol->id labelling (2 swapped pairs known: VTABLE_BSTDerivedCreator_MovementMessageFreezeDirection_MovementMessage_ and
  VTABLE_AutoRegisterCreator_..._BSTSmartPointerPathingFactoryManager_MovementMessage_64_ have each other's ids in the fork).
- 1.6.1170 library ids that the library no longer contains (retired when the 1.6.1130 library was regenerated) have no RVA to start from.
- The CommonLib-internal list is a name-match over-approximation (+-25%): some ids may never be called.
- `skyrim_cast` source types are not enumerated (only target RTTI); the RTTI ids are EXACT-by-name anyway, extend
  `collect_ids.py` if a specific source type is needed.
- Struct layouts are NOT proven here (only code addresses/vtables/globals). The `LAYOUT CHANGE` flag shows where RTTI
  already proves a layout moved (PlayerCharacter gains `BSTEventSink<BSSystemEvent>` on 1.7.104).
- Leaf functions without `.pdata` are handled by raw scan and library starts; ones lacking unique bytes AND identical callers
  stay UNRESOLVED.
