# Pillar 4a — Catalog of proven engine hook sites (1.6.1170 / AE)

Every hook below is used in MRO's `plugin.cpp` and was byte-verified against the
running 1.6.1170 game (see [hook-site-verification.md](hook-site-verification.md)).
Address Library IDs are AE-database IDs unless noted. **Re-verify live after any
game update** — offsets move.

Reference: `../Requiem-modification/native/plugin.cpp`,
`../Requiem-modification/docs/NATIVE_REWRITE_PLAN.md`.

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
