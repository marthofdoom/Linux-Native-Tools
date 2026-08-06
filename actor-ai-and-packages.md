# Pillar 6 — Driving actor AI: packages, aliases, casting, combat targets

How to make an NPC you **do not own** perform an animated, engine-arbitrated
action (cast, attack, travel) on command — without seizing its AI. Validated
in-game across MFO's v0.8–v1.0 arc on a heavy order (Lorerim). Reference:
`../marth-follower-overhaul/native/plugin.cpp` and
`../marth-follower-overhaul/Docs/ENGINE_NOTES.md` (authoritative).

**Standing doctrine:** you do not call a verb to make an actor act. You arrange
the engine's own preconditions (a package on the reference, or a spell in hand +
a target) and let the actor's AI run it. Every "cast/attack now" verb was
refuted; the mechanisms below are what actually work.

---

## 1. Delivering a package to an arbitrary reference (per-reference, additive)

An authored `TESPackage` reaches an actor by placing that actor in a **quest
alias** that carries the package via `ALPC`. At runtime the carrier is
`ExtraAliasInstanceArray` on the *reference*, holding
`BGSRefAliasInstanceData { TESQuest* quest; const BGSBaseAlias* alias;
const BSTArray<TESPackage*>* instancedPackages; }`, read by
`Actor::CheckForCurrentAliasPackage()` (vfunc `0x049`).

Two decisive, field-proven properties:

- **Per-reference.** Only the filled reference is affected; the base `TESNPC`
  and other instances are untouched — so a follower-framework's copy (NFF) is
  unaffected.
- **Additive.** The carrier is a `BSTArray<BGSRefAliasInstanceData*>` — your
  alias entry and another framework's coexist on the same actor. You
  *structurally cannot* stomp another mod's packages.

Fill the alias by handle at runtime with **`TESQuest::ForceRefTo`
(`RELOCATION_ID 25052`)**; this *instances* the alias's `ALPC` packages onto the
reference. Proven **ESP-only, no DLL**: an alias-carried package rooted a live
follower with zero plugin code running.

> Do **not** deliver via the base record's package lists
> (`TESNPC::defaultPackList` DPLT / `TESAIForm::aiPackages` PKID) unless you own
> the actor — those are shared by every instance of that NPC. (ALYSLC can,
> because it ships its own bodies.)

## 2. Arbitration is by the *claiming quest's* priority — snapshotted at fill

The engine does **not** pick "the highest-priority quest with a valid package."
It picks the highest-priority quest whose **alias claims the actor**, then asks
that quest for a package (`QUEST_DATA::priority`; vanilla: default 30,
`DialogueFollower` 50, scenes 80–96 — MFO uses 60). Consequences, both
field-measured:

1. **A claimed quest that supplies no valid package ROOTS the actor** (idle /
   frozen). Never hold an actor in your alias without a valid package, and never
   gate the package with a false CTDA to "release" — that leaves the actor
   claimed-with-nothing = rooted.
2. **The owning-quest claim is locked when the alias instance is created**,
   using the quest's priority *at that moment*. A later write to
   `quest->data.priority` re-selects the package *within* the current owner but
   does **not** re-run owning-quest arbitration. A runtime 25→60 bump does not
   win the actor if the alias was filled while the quest was 25.

**Therefore release by changing alias occupancy, not priority.** Ship the
command quest at a static winning priority; toggle alias membership (fill =
engage, evict = release). The alias is the runtime switch; priority is a
build-time dial.

## 3. A force-filled *scriptless* alias is unclearable — evict instead

All three field-disproven:

- `ForceRefTo(alias, None)` does **not** empty a ref alias (SKSE's own callers
  null-guard and never pass null — now you know why).
- `TESQuest::ResetQuest` (`RELOCATION_ID 25014`) does **not** drop a force-filled
  ref.
- Papyrus `ReferenceAlias.Clear` cannot be VM-dispatched to a *scriptless* alias:
  with no bound script instance, `DispatchMethodCall2` returns false. (The fill
  works only because it uses native `ForceRefTo`, not the VM.)

**Release by eviction:** `ForceRefTo(alias, <throwaway ref>)` replaces the
actor's instance so a framework reclaims it. See §5 for *what* to evict with —
it must be a **non-actor** marker.

---

## 4. Casting: the animation graph gates it; `CastSpellImmediate` bypasses it

`ActorMagicCaster` inherits `SimpleAnimationGraphManagerHolder` and sinks
`BSAnimationGraphEvent` — **the animation is what advances the cast** through
`RequestCastImpl → StartChargeImpl → StartReadyImpl → StartCastImpl →
FinishCastImpl`.

`CastSpellImmediate` (`GetMagicCaster(kInstant)->Cast(...)`) is the "apply now,
no actor required" path (traps / scripts / enchants). It necessarily bypasses
the graph, so:

- It plays **no animation** regardless of `CastingSource` (all four sources
  tested — none animate).
- It **deducts magicka only while a pool exists, then casts free forever** (no
  refusal at 0). Any cost/competence gate must be your own pre-check:
  `SpellItem::CalculateMagickaCost(actor)` (the actor overload accounts for
  skill/perks).

Use `CastSpellImmediate` only for **silent/instant apply** (self-heals, traps).
For a **visible actor cast**: equip the spell + supply a target and let combat
AI fire it, or drive a **UseMagic package** (§9) — but note packages also cast
free, so you still deduct cost yourself.

Symbol traps at the pinned NG rev: `MagicCaster::SetCurrentSpell` does **not
exist** (that's a current-master name); only `SetCurrentSpellImpl` (a no-op on
`ActorMagicCaster`) and the public `currentSpell` member — never hand-write it
or the select/deselect bookkeeping desyncs.

## 5. UseMagic roots the actor; evict alias slots with a non-actor marker

- A **UseMagic package is "stand and cast"** — it roots the actor **even
  mid-fight** (`PKDT 0x00100000` / `PLDT type 12`). Vanilla combat mages move
  while casting because they run *combat AI, not packages*. You currently cannot
  have animated + mobile + deterministic casting at once: package = deterministic
  but stationary; equip-and-let-AI-cast = mobile but not deterministic.
- **Never evict a package-carrying alias with the PLAYER as the throwaway ref.**
  An actor (player included) in a package-carrying alias is pulled out of
  furniture to run the package — parking the player there ejects them from
  chairs / mining / workbenches ~1/sec. Displace with a session-minted,
  force-persisted **non-actor XMarker** (base `0x3B`, `PlaceObjectAtMe` at
  `kPostLoadGame`/`kNewGame`, main thread). A non-actor is the only inert
  occupant.
- Alias fills are **engine-serialized** into the save, so a fix that stops
  writing bad state must **also sweep it out of old saves** on load.

---

## 6. Steering combat targets — a vtable-index hook (layout-independent)

Unlike the addrlib call-site hooks in [known-hooks.md](known-hooks.md), a
`write_vfunc` on a vtable index does not drift across game versions. Shipped
precedent (RedyellowUnit/SmartTargetingNPC, 1.5.97 + 1.6.1170):

- Hook `RE::VTABLE_Character[0]` index **`0xE4`** (`UpdateCombat`). After calling
  the original, enumerate `combatGroup->targets` under
  `RE::BSReadLockGuard(combatGroup->lock)` and write **both**
  `currentCombatTarget` **and** `combatController->targetHandle`
  (+ `previousTargetHandle`).
- Offsets: `combatController` at `GetActorRuntimeData().combatController`;
  within `CombatController` — `combatGroup` 0x00, `targetHandle` 0x2C,
  `previousTargetHandle` 0x30 (all below the AE-shift boundary, see
  [commonlibsse-ng-traps.md](commonlibsse-ng-traps.md)).
- **Only redirect when the engine already HAS a target** (respect a vanilla
  clear). Targets are **not sticky** — the engine re-picks every `UpdateCombat`,
  which is why this is "re-assert inside the hook" (compare-and-write), never a
  `StopCombat`/`StartCombat` reset (that churns into stuttering never-attacking
  behavior).
- Do **not** hook `CombatTargetSelectorStandard` — NG has it only as a forward
  declaration (no vtable, no members). Papyrus has **no** combat-target setter at
  all, which is why no follower-command mod ships an "attack" verb in Papyrus.

### Consent hooks vs insertion hooks
`CombatMagicCaster::CheckStartCast` (vtable index **`0x06`**) is an *influence*
hook: returning early removes the AI's veto on casting **without inserting a
cast** — the follower's own AI does the movement / aim / animation / magicka.
Prefer influence over insertion for AI behavior. Traps:

- `VTABLE_CombatMagicCasterArmor` is a **vtable symbol with no class** in the
  pinned headers — it does not derive `CombatMagicCaster`, so its index 6 is a
  different function; hooking it derefs a garbage `this`. **A name match is not a
  type match** — only vtables whose classes actually derive the base are valid;
  no-op the thunk if the runtime vtable isn't one you recorded at install.
- `CombatMagicCasterRestore` is **also the caster for potion drinking**, so a
  blanket `CheckStartCast` deny suppresses combat potions too — gate on
  `formType == Spell`.

---

## 7. Authoring PACK records (see also [esp-without-xedit.md](esp-without-xedit.md))

You do **not** author package templates — `Skyrim.esm` ships 104 (PACK type 19).
Point `PKCU.template` at a vanilla one and supply only your inputs. Useful
templates: **UseMagic `000504F5`** (inputs Spell=uid3, Target=uid4, plus
CastTime/Cooldown/NumToCast rate-limiting), **UseWeapon `0001C338`**, **Travel
`00016FAA`**, HoldPosition `000503D0`, Activate `00019B2D`. Byte-verified rules:

- **`QNAM` (owner quest) is required whenever any input names an alias**, and
  must be emitted **immediately before `PKCU`** (0 of 2,109 vanilla records put
  it after). Emit QNAM *only* when an input names an alias — a stray/misordered
  QNAM is the suspect for a zero-precedent CTD.
- **Target needs no alias:** `PackageTarget::targType == 0` takes an
  `ObjectRefHandle` (name a foe directly). `t4` = reference alias (points at
  *someone else*; vanilla never self-targets via alias — it stalls silently);
  `t6` = self.
- Use `kIgnoreCombat` (`0x00100000`), not `kMustComplete`, for a package meant to
  fire in a fight.
- **`preferredSpeed` (PKDT byte 6: 0 Walk/1 Jog/2 Run/3 FastWalk) is INERT unless
  general flag `0x00002000` ("Preferred Speed") is set** (proven across 5,961
  records).
- **Runtime input-mutation trap:** a runtime package instance's nameMap is
  non-null but carries the ESP instance's `ANAM` **type** strings
  ("TargetSelector","Bool"), not input **names** ("Spell","Target") — those live
  only on the vanilla template's map. `FindInput` must fall back to the template
  map on a lookup miss, not only when the instance map pointer is null.
- Copy the vanilla twin `TG08BMercerCombatOverrideCastAtBrynjolf` (`000FCC26`)
  for the UseMagic + QNAM + PTDA-t4 shape.

## 8. Off-mesh routing primitives (walk an NPC to an arbitrary point)

`PLDT` has **no raw-XYZ form** (12-byte type/value/radius only) — a package
destination must be an **XMarker REFR** moved via VM-dispatched
`ObjectReference.MoveTo`. To find a reachable point: `BSNavmesh::triangles`
(`BSTArray<BSNavmeshTriangle>` @0x028; `BSNavmeshTriangle{ vertices[3],
triangles[3], triangleFlags }`, sizeof 0x10; skip `TriangleFlag::kDeleted`),
Ericson closest-point-on-triangle under the cell's `BSSpinLock`. Reference:
`../marth-follower-overhaul/Docs/ROUTING_SNAP_DESIGN.md`.

---

## Threading caveat (critical)

Much of the above mutates live engine state, so read the AddTask/worker-thread
correction in [instance-data-and-events.md](instance-data-and-events.md) §21
(§26) before wiring any of it into a tick — `SKSE::GetTaskInterface()->AddTask`
does **not** run on the main thread in this runtime.
