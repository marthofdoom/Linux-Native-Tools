# Pillar 7 — CommonLibSSE-NG library traps (bugs that crash at *your* offset)

CommonLibSSE-NG is header-heavy: its reimplementations and struct layouts
compile **into your DLL**. When one is wrong, the fault lands at an address in
*your* module with no library frame on the stack — every crash log reads like
your bug. These are the ones that cost real time (pinned NG: vcpkg
`commonlibsse-ng` 3.7.0, CharmedBaryon `c4ab853`). Sources:
`../marth-enchanting-overhaul/Docs/ENGINE_NOTES.md`,
`../marth-follower-overhaul/Docs/ENGINE_NOTES.md`.

**Standing doctrine (from pillar 5):** distrust NG's C++ reimplementations for
engine-visible state; replicate the engine's own flow (read the SKSE64 source
for the equivalent Papyrus native). The items below are the concrete casualties.

---

## 1. `ExtraDataList::RemoveByType` null-derefs when it empties the list

NG's head-removal loop
(`while (GetData()->GetType()==type){ GetData()=GetData()->next; }`) re-derefs
the new (null) head after unlinking the last node, and the trailing prev/cur walk
derefs `GetData()->next` on the emptied list. Disasm-proven CTD (`MEO.dll+0x7C4EB`,
`r14=0x99`). Latent for a project's whole life because every strip target had
always carried a surviving node (`ExtraUniqueID`/`ExtraWorn`); it detonates the
first time a `{kEnchantment, kTextDisplayData}`-only xlist reaches the strip
site.

**Cure — never call NG `RemoveByType`.** Write `SafeRemoveAllByType` =
`GetByType` + `Remove` + `delete` (all null-safe; `Remove` takes the engine
`BSReadWriteLock`, `delete` goes through the virtual deleting dtor).
One-node-per-type is an engine invariant (the presence bitfield can only encode
presence), so a single `Remove` per type is complete. Assume an xlist can arrive
with exactly the two nodes you're about to strip.

## 2. `CombatController` AE layout is silently wrong — members ≥ 0x68 are +8

`CombatController.h` guards its AE-only member (`aimControllerLock`, `BSSpinLock`
at 0x68) behind `#ifdef SKYRIM_SUPPORT_AE` — a macro **NG never defines** (its
build uses `ENABLE_SKYRIM_AE`). So the struct always compiles with the **SE
layout**, and on the 1.6.1170 (AE) runtime every member at offset ≥ 0x68 is
shifted **+8**. Concrete crash: `cachedAttacker` compiles to `0xC8`, but `0xC8`
at runtime holds `handleCount` (= 1 when fighting one enemy) → read as `Actor*`,
passes the null check, faults reading `formID` at `1+0x14`.

**Safe below 0x68:** `combatGroup` 0x00, `attackerHandle` 0x28, `targetHandle`
0x2C, `previousTargetHandle` 0x30. For anything at/after 0x68
(`aimControllers`, `currentAimController`, `areas`, `targetSelectors`,
`cachedAttacker`, `cachedTarget`) read via `REL::Module::IsAE()`-gated manual
offsets, not the header member.

## 3. `LookupByEditorID<T>` resolves only ~15 form types — and po3 Tweaks masks it

On a vanilla runtime only these override the editorID getter: `BGSKeyword`
(+derived), `TESGlobal`, `TESQuest`, `TESRace`, `TESTopic`, `TESObjectCELL`,
`TESWorldSpace`, `TESIdleForm`, `TESObjectANIO`, `TESSound`,
`TESImageSpaceModifier`, `BGSHeadPart`, `BGSSoundDescriptorForm`, `BGSVoiceType`,
`BGSMusicType`. Everything else — **`ActorValueInfo` included** — returns
nullptr. **po3 Tweaks' editorID caching populates the map for all forms**, so a
lookup that works on your (po3-equipped) test deck nullptrs for a Nexus user
without po3 Tweaks — and reads as "another mod overrode us."

**Only trust `LookupByEditorID` for keywords / the 15 listed types.** For AVIFs
call the engine's own table:
`RE::ActorValueList::GetSingleton()->GetActorValue(RE::ActorValue::kEnchanting)`.
Never validate an editorID lookup only on a po3-Tweaks order.

## 4. A Papyrus native is NOT a C++ binding — verify the binding, not the mechanism

Reading SKSE64 / PapyrusUtil / po3 sources tells you the engine flow a Papyrus
native names, not that NG *exposes* it. Field-established absences at the pinned
rev:

- `KeepOffsetFromActor`, `ClearKeepOffsetFromActor`, `SetDontMove`,
  `DoCombatSpellApply` — **do not exist** in NG or po3's fork (Papyrus-only).
- `StartCombat` — exists **only in po3's fork** as a relocation thunk
  `RelocationID(37608, 38561)` (SE/AE; no sourced VR id) — and even that
  field-failed to take.

Reaching a Papyrus-only native from C++ means VM dispatch or a self-sourced
relocation. **Grep the pinned headers before a design depends on a call.**

### Banned: library calls whose contract is wider than your intent
- `ExtraDataList::RemoveByType` — §1 (and removes more than one type-match in
  other framings).
- `ClearPackageOverride` — **removes overrides added by *other* mods** (same
  wider-than-intent contract).
- `PathToReference` (Papyrus) — **latent**: blocks until the walk finishes; banned
  from any tick / main-thread caller.
- PapyrusUtil **package overrides persist through saves** — an unledgered one
  outlives the mod.

## 5. `unordered_map` insert-then-`erase(it)` is UB across a rehash

`map[newKey] = std::move(it->second); map.erase(it);` is UB the moment the
insert crosses a load-factor boundary — insertion can rehash and invalidate all
iterators, including `it`. Latent for a year in a socket-rekey path; bites only
on long playthroughs with thousands of records. **Correct order:** move the value
into a local, let the iterator go, *then* insert. Applies to any long-lived
native map keyed by runtime state.

## 6. A declared vfunc signature is **not** an ABI — the hidden `sret` out-slot

NG declares `CombatMagicCaster::GetMagicTarget` (vtable slot `0x0A`) as an
ordinary function returning a handle. The compiled engine function does not have
that shape: it returns a **16-byte aggregate through a hidden out-pointer**
(`sret`), which the MSVC ABI passes in the FIRST argument register and shifts
every declared argument one register to the right. A thunk written against NG's
declaration therefore reads `this` out of the out-pointer slot and writes the
return value over an argument.

The cost of trusting the header: a probe that was **read-only, chained the
original, and changed no behaviour** — as passive as a hook can be — crashed the
game on the first cast. Passivity does not protect you from a wrong signature.
Nothing about "I only observe" survives a mis-shaped call frame.

**Cure, and it is not optional for any vtable hook:**

1. **Disassemble the target's own implementation** and read what it actually does
   with `rcx`/`rdx`/`r8`/`r9` before you write the `decltype`. If the first thing
   it does is store a pointer you did not expect, you have an `sret`.
2. **Prefer reading a `static_assert`ed FIELD over calling a vfunc.** A wrong
   offset gives you a wrong value (bad, recoverable); a wrong signature corrupts
   the frame (fatal, and it lands at *your* address). For an observation probe
   this hardens into a rule: **a probe reads fields, it never calls engine
   vfuncs.** If a value is only reachable through a vfunc, get it from
   disassembly instead of instrumenting it.
3. **A vtable symbol is not a class.** `VTABLE_CombatMagicCasterArmor` exists as a
   symbol with nothing deriving `CombatMagicCaster` behind it, so its slot 6 is a
   different function entirely — hooking it derefs a garbage `this`. Match the
   RTTI **type**, never the symbol name (see
   [hook-site-verification.md](hook-site-verification.md)).

**The counter-example matters just as much:** `EffectSetting::data` at `0x068`,
with `associatedSkill` at data+0x10, `minimumSkill` +0x40, `archetype` +0x58,
`primaryAV` +0x5C, `delivery` +0x74, **is exactly right** — an earlier note in
this project claiming otherwise was wrong and was struck. The rule is therefore
"**verify each one**", not "distrust the library wholesale". Header-shaped
paranoia costs as much time as header-shaped trust; disassembly is the only thing
that settles it either way.

---

## The meta-rule

When a crash log points at your module with a plausible-but-wrong pointer (a
small integer like `1` read as a `Actor*`/`TESForm*`), suspect a **layout or
reimplementation bug in an NG header** before your own logic — disasm the
*deployed* DLL at the faulting offset and compare the member the compiler chose
against the runtime truth. Two of the bugs above were "fixed" twice on the wrong
side before a disasm found them.
