# Pillar 4a — Catalog of proven engine hook sites (1.6.1170 / AE)

Every hook below is used in MRO's `plugin.cpp` and was byte-verified against the
running 1.6.1170 game (see [hook-site-verification.md](hook-site-verification.md)).
Address Library IDs are AE-database IDs unless noted. **Re-verify live after any
game update** — offsets move.

Reference: `../Requiem-modification/native/plugin.cpp`,
`../Requiem-modification/docs/NATIVE_REWRITE_PLAN.md`.

> What MRO takes from a reference mod is only the **published site address** (an
> Address Library ID + offset — a fact about the game binary), which it
> re-verifies against live memory. The thunk logic installed at each site is
> MRO's own code.

---

## Rules that govern all of them

- **No instruction-cave / fixed raw-offset asm patches.** The 1.6.1130+ recompile
  moved ArmorRatingRescaledRemake's cave sites — one game update = CTD. Prefer
  **`write_vfunc`** vtable hooks (layout-independent) or **call-site
  `write_call<5>` thunks** guarded by a live byte-match.
- **Self-verify at install.** Read the opcode at the site; require `0xE8` (a
  `call rel32`) or refuse to install and log the real bytes. A failed check must
  fall back gracefully, never crash.
- **Allocate the trampoline once**: `SKSE::AllocTrampoline(128)` before installing
  any `write_call` thunk.
- **DLL↔Papyrus handshake via a GlobalVariable.** When a hook goes live, set a
  GLOB to 1 so the Papyrus fallback stands down. **Globals are save-persisted** —
  loading a save restores the stored value over your `kDataLoaded` write, so
  **re-assert on `kPostLoadGame` and `kNewGame`**, not just `kDataLoaded`.

---

> **Before reaching for a hook:** most per-item and per-event behavior needs NO
> code hook at all — see [instance-data-and-events.md](instance-data-and-events.md)
> (per-instance extra data, event sinks, created enchantments, native message
> boxes; all validated in MEO).

## Hook 0 — Vendor gold (no hook at all)

Not every "native" change needs a code hook. To double merchant gold on any load
order, just mutate the leveled lists in memory at data load:

```cpp
auto* lvli = RE::TESForm::LookupByID<RE::TESLevItem>(formID);
for (std::uint8_t i = 0; i < lvli->numEntries; ++i)
    lvli->entries[i].count = min(entries[i].count * 2, 0xFFFF);
```

Runs in the `kDataLoaded` message handler. Dynamic on any load order; no address
dependency. (Leveled-list counts cannot be written from Papyrus — this is why a
tiny DLL is worth it.)

---

## Hook 1 — Physical damage taken (weapon hit)

Correct final damage past the engine armor cap, per hit, for player/teammates.

| | |
|---|---|
| Site | **AL ID 38627 + 0x4A8** — Valhalla Combat's documented call site ([D7ry/valhallaCombat](https://github.com/D7ry/valhallaCombat), maintained post-1.6.1170) |
| Install | `write_call<5>`, self-verify `0xE8` |
| Signature | `void thunk(RE::Actor* victim, RE::HitData& hitData)` |
| Effect | scale `hitData.totalDamage *= (1-ourDR)/(1-engineDR)` |
| Order | run our `Adjust()` **then** call original |

Read the live armor cap/scale from GMSTs (`fMaxArmorRating`, `fArmorScalingFactor`)
so it's portable across load orders — don't bake the 75%/750 kink.

---

## Hook 2 — Magic effect application (elemental absorb)

See the *real* pre-resistance magnitude of a magic effect as it's applied — the
skill/perk/dual-cast-scaled value, which Papyrus `OnHit` cannot see (it only gets
a spell's authored base magnitude).

| | |
|---|---|
| Site | **AL ID 34526 + 0x20B** (AE) — po3's PapyrusExtenderSSE `magicApply` call site. SE equivalent: **ID 33742 + 0x1E8** |
| Install | `write_call<5>`, self-verify `0xE8` |
| Signature | `bool thunk(RE::MagicTarget* self, RE::MagicTarget::AddTargetData* data)` |
| Order | call original **first** (apply the effect), then post-process |
| Key field | `data->magnitude` at **offset 0x3C** = pre-resistance magnitude (damage at 0% resist) |

CommonLibSSE idioms used here:
- victim: `self->GetTargetStatsObject()` returns a `TESObjectREFR*`; cast to
  `Actor*` only after `self->MagicTargetIsActor()`.
- effect classification: `data->effect->baseEffect` (`EffectSetting*`);
  `base->data.resistVariable` (the mitigating `ActorValue`);
  `base->IsDetrimental()` / `IsHostile()`; `base->data.archetype`
  (`kValueModifier` / `kDualValueModifier` = it actually deals resource damage —
  filter out staggers/hazards/scripts that merely carry a resist flag).
- apply: `victim->AsActorValueOwner()->RestoreActorValue(
  ACTOR_VALUE_MODIFIER::kDamage, ActorValue::kHealth, amount)`.

**Requiem note:** elements damage *different* resources (fire→health,
frost→stamina, shock→magicka), so filter by **archetype**, never by the effect's
target actor value.

---

## Thunk skeleton (both call-site hooks)

```cpp
struct MyThunk {
    static ReturnT thunk(ArgsT... a) {
        // pre or post depending on the hook; see per-hook "Order"
        auto r = func(a...);      // original
        DoOurThing(a...);
        return r;
    }
    static inline REL::Relocation<decltype(thunk)> func;
};

bool Install() {
    REL::Relocation<std::uintptr_t> target{ REL::ID(ID), OFFSET };
    if (*reinterpret_cast<std::uint8_t*>(target.address()) != 0xE8) {
        log("site check failed, not installing"); return false;   // graceful
    }
    MyThunk::func = SKSE::GetTrampoline().write_call<5>(
        target.address(), MyThunk::thunk);
    return true;
}
```

---

## Vtable-index hooks — a more robust class (layout-independent)

`write_vfunc` on a vtable **index** does not drift across game updates (unlike
the addrlib call-site offsets above), so re-verification after a game update is
lighter. Sites proven in MEO/MAO/MFO:

| # | Site | Signature / job | Notes |
|---|---|---|---|
| **3** | `PlayerCharacter::VTABLE[0]` index **`0x10F`** — `DrinkPotion` | intercept item consumption | The universal drink funnel — also catches auto-potion mods, so it doubles as the compat intercept. Return `true` (handled) to stop vanilla destroying an item you keep. **SSE/AE index only — VR shifts the Actor vtable; bail on `REL::Module::IsVR()`.** (MAO) |
| **4** | `RE::VTABLE_Character[0]` index **`0xE4`** — `UpdateCombat` | steer combat targets | After the original, write **both** `currentCombatTarget` and `combatController->targetHandle`(+prev) under `BSReadLockGuard(combatGroup->lock)`. Re-assert **only when a target already exists**; targets aren't sticky (re-picked every call). See [actor-ai-and-packages.md](actor-ai-and-packages.md) §6. (MFO) |
| **5** | `CombatMagicCaster::CheckStartCast` index **`0x06`** | *influence* a follower's casting (remove the AI veto; don't insert a cast) | Prefer influence over insertion. **Validate the runtime vtable is a real derived class** — `VTABLE_CombatMagicCasterArmor` is a symbol with no class (index 6 = a different fn → CTD). `CombatMagicCasterRestore` also casts *potions* — gate on `formType == Spell`. (MFO) |
| **6** | `PlayerCharacter::VTABLE[0]` index **`0x0AD`** — `Actor::Update(float)` | genuine main-thread pump | Drain a mutex-guarded queue once/frame on the main thread, player-only. The correct home for work AddTask can't do (§26 of instance-data). `SKYRIM_REL_VR_VIRTUAL` — bail on VR. (MFO) |

## Hook 3 — Leveled-list counts (no address dependency)

There is **no Papyrus/po3 API to write leveled-list counts at runtime** (po3
only reads them). To change them dynamically on any load order, a native DLL
rewrites the LVLI entries in memory at `kDataLoaded` (Hook 0 is the vendor-gold
instance of this). Testing gotcha: after any LVLI change, merchant gold/stock is
unchanged until the merchant chest **re-rolls on cell reset** — wait 72+ in-game
hours away from the cell before judging the result.

> **VR caution for all vtable hooks:** any index above is SSE/AE; the VR runtime
> shifts vtables. Gate installs on `REL::Module::IsVR()` and refuse, or source a
> VR-specific index.

---

## Catalog — combat-AI cast pipeline (1.6.1170 / AE), verified by disassembly

Reversed 2026-09 against the decrypted 1.6.1170 image. RVAs are of that image;
`AE nnnnn` are Address-Library AE ids. The mechanism these sites belong to is
documented in [actor-ai-and-packages.md](actor-ai-and-packages.md) §10 — read
that first, because most of these are only reachable if an **upstream** one
admitted the candidate. Every one is a `write_vfunc` slot (layout-independent)
unless the row says otherwise; guard them per
[hook-site-verification.md](hook-site-verification.md).

### Vtables

| Object | vtable RVA | AE id | notes |
|---|---|---|---|
| `CombatMagicItemData` (the per-spell caster-type resolver) | `0x18d5120` | **211955** | **slot 1** = the per-effect classifier `0x81d830` (AE 45321). RTTI `.?AVCombatMagicItemData@@`. Stack-local, one per spell evaluation |
| the affordability/skill visitor | `0x18d5138` | 211957 | slot 1 = `0x81de80` (AE 45328) — the `minimumSkill` gate |
| `CombatMagicCasterRestore` | `0x18cc890` | 211142 | `GetCategory` = 1 |
| `CombatMagicCasterOffensive` | `0x18cc4a0` | — | `GetCategory` = 0 |
| `CombatInventoryItemMagicT<Magic, Restore>` | `0x18d0570` | 211612 | 23 slots (0x00..0x16) |
| `CombatInventoryItemMagicT<Magic, Offensive>` | `0x18d0ff0` | — | the twin |

> Two RVAs published elsewhere for the caster vtables (`0x1709eb8` /
> `0x170adc0`) are **NOT valid on this binary** — do not use them.

### Item vtable slots (all `CombatInventoryItem` subclasses)

| slot | name | notes |
|---|---|---|
| `0x0B` | `GetCategory` | Restore/Magic `0x832580` = `return 1`; Offensive `0x8341a0` = `return 0` |
| `0x0C` | `CalculateScore` | **all 80 magic templates share `0x819aa0` -> `0x819c10`** — one function-level trampoline covers them all. Weapon classes have their own: Melee `0x8183e0`, Ranged `0x8188b0` (helper `0x8189d0`; a bow with no arrows scores 0), Shield `0x818df0`, Torch `0x819480` |
| `0x0D` | `Clone` | used for per-hand duplication |
| `0x0E` | `CheckBusy` | `0x819da0` — **not** consulted by the selection loop |
| `0x0F` | `CheckShouldEquip` | the equip ADMISSION seat. Base magic impl is `return true`; the Restore templates route to the static `0x81f7c0` (AE 45371), which is the vanilla self/foe health gate. Restore<Magic> thunk `0x832650` (AE 46304) |
| `0x10` | `GetResource` | `0x8199b0` — cannot drop an item (both branches reach AddItem) |
| `0x15` | `CreateCaster` | Restore/Magic `0x832510` -> caster ctor `0x81f710`. **Virtual only, no direct callers** |

### Caster vtable slots (`CombatMagicCaster` family)

| slot | name | notes |
|---|---|---|
| `0x06` | `CheckStartCast` | Restore `0x81f980` (AE 45372); Offensive `0x81e7b0` |
| `0x07` | `CheckStopCast` | Restore `0x81faf0` (AE 45373) |
| `0x0A` | `GetMagicTarget` | shared base `0x81e020` (AE 45336) — `kSelf ? attacker : controller.target`. **See the ABI trap in [commonlibsse-ng-traps.md](commonlibsse-ng-traps.md) §6 before hooking this one** |
| `0x0B` | `NotifyStartCast` | `0x81fc60` |
| `0x0D` | `SetupAimController` | aim-target override |

### Non-virtual functions and data (trampoline / read sites)

| what | RVA | AE id |
|---|---|---|
| `CombatInventory::Init` | `0x80f010` | 44857 |
| `CombatInventory::Update` | `0x80f380` | 44858 |
| `CombatInventory::Rebuild` | `0x811030` | 44879 |
| item factory (form -> `CombatInventoryItem`) | `0x811cc0` | 44882 |
| per-hand add / clone | `0x811ac0` | — |
| equipment selector | `0x8134c0` | 44899 |
| `CombatEquipment::AddItem` | `0x80e490` | — |
| caster-type row table (23 rows) | `0x20163b0` | 382289 |
| caster-type row hash map | `0x31ab790` | 405245 |
| row-table init | `0x81dc90` | 45326 |
| **category preemption order** `[1,2,4,0,3,5,0,6]` | `0x20162c8` (count at `0x20162f0`) | — |
| `MagicItem::VisitEffects` (dispatches `visitor->vtable[1]`) | `0x14c780` | — |
| `Actor::VisitSpells` | — | `RELOCATION_ID(37827, 38781)` |
| `MagicCaster::CastSpell(spell, target, bool)` | `0x5bb720` | 34401 |
| the AI's cast-fire helper | `0x89ee30` | 49083 |
| `CombatBehaviorContextMagic::ctor` (mints the caster) | `0x89eae0` | — |
| `Actor::SetWantCast` (graph bools) | `0x69c120` | 37950 |
| `IsCastingSourceReady` | `0x6b91e0` | — |
| `CombatController::Update` | `0x5589b0` | 33217 |
| `CombatManager::StartCombat(actor, target)` | `0x83d180` | 46873 |
| `CombatManager::StartCombat(actor, group)` | `0x83d2c0` | 46874 |

### `Actor::StartCombat` — a 3-argument ABI

`0x6b6930`, **AE 38561 / SE 37608**:
`bool StartCombat(Actor* this, Actor* target /*nullable*/, CombatGroup* group /*nullable*/)`.
**R8 is dereferenced when non-null**, so a two-argument call passes garbage and
crashes. Semantics and refusal conditions:
[actor-ai-and-packages.md](actor-ai-and-packages.md) §11.

### Combat behaviour tree

`VTABLE_CombatBehaviorTreeNode` (AE 212199) plus ~70
`VTABLE_CombatBehaviorTreeNodeObject_*` leaves already carry Address-Library ids
in CommonLibSSE-NG's `Offsets_VTABLE.h`, but **no C++ class exists** for them in
the pinned tree — you must declare the vtable slots yourself. Slot `0x02` is
`act()` ("enter") and slot `0x03` is `pop()`.

> **Deny a leaf in PAIRS or corrupt the game.** `act()` PUSHES per-thread state on
> the behaviour thread's data stack and the runner calls that SAME node's `pop()`
> immediately after; `pop()` removes exactly what its own `act()` pushed. Push
> sizes differ per leaf (4 for most, 0xC for the cast/ranged leaves, 0x18/0x30 for
> others; the context-creation nodes push 0x30). Substituting a foreign `act()`
> whose push size differs from the node's own `pop()` drifts the stack and
> eventually crashes far away, in an interrupt unwind, with a garbage current
> node. The safe substitution is to run `CombatBehaviorForceFail`'s **own**
> compiled `act()` and `pop()` (4/4) as a pair — never a hand-rolled `SetFailed`,
> never `act()` alone. This was a months-live CTD.
