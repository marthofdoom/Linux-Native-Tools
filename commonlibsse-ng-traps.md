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

---

## The meta-rule

When a crash log points at your module with a plausible-but-wrong pointer (a
small integer like `1` read as a `Actor*`/`TESForm*`), suspect a **layout or
reimplementation bug in an NG header** before your own logic — disasm the
*deployed* DLL at the faulting offset and compare the member the compiler chose
against the runtime truth. Two of the bugs above were "fixed" twice on the wrong
side before a disasm found them.
