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

> **Read §10 with this section.** §4/§5 record what was true before the combat-AI
> cast pipeline was reversed (2026-09). The "animated **or** deterministic, pick
> one" conclusion below is superseded: you can have both, by answering the
> decision points the NPC's own AI already asks — see
> **§10** below. Everything
> §4/§5 say about `CastSpellImmediate` and about UseMagic packages rooting the
> actor remains correct.


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

---

# 10. The combat-AI **cast** pipeline, end to end (1.6.1170 / AE)

> **This section supersedes the pessimistic conclusion in §4/§5** ("you currently
> cannot have animated + mobile + deterministic casting at once"). You can — but
> not by driving the caster. You do it by answering the four decision points the
> NPC's own combat AI already asks. What follows is that pipeline, reversed from
> the 1.6.1170 binary in 2026-09. Addresses are RVAs of the decrypted image;
> `AE nnnnn` are Address-Library AE ids. Everything here is **CONFIRMED by
> disassembly** unless marked HYPOTHESIS.

**The shape to remember, because it generalises to every engine facet:**

```
ADMISSION  ->  SELECTION  ->  DECISION  ->  EXECUTION
(is it even    (does it win   (should I    (do it)
 representable? ) the slot?)    now?)
```

Every control seat people reach for first (`CheckStartCast`, `GetMagicTarget`,
the cast leaves) lives in **DECISION**. If ADMISSION rejected the candidate,
none of those seats is ever called — they are not wrong, they are unreachable.

## 10.1 ADMISSION — how a spell becomes a `CombatInventoryItem`

- The item set is **per-`CombatController`**, not per-actor-forever:
  `CombatInventory::Init` **0x80f010** (AE 44857) builds it once per controller;
  `CombatInventory::Rebuild` **0x811030** (AE 44879) re-syncs it whenever
  `CombatInventory::dirty` (+0x1C4) is set. The only `dirty` writer found by a
  full-encoding scan of the combat range is the per-tick check at 0x559bd0
  (`GetCombatStyle() != cached` -> dirty). **HYPOTHESIS: a runtime `AddSpell`
  does not by itself dirty the inventory** — no second writer was found.
- Sources walked: carried inventory entries; the actor's **known** spells via
  `Actor::VisitSpells` (`RELOCATION_ID(37827, 38781)`) = `addedSpells` + the base
  `TESSpellList` + race spells + one further source; base-actor shouts; the fists
  weapon. **A spell the actor does not KNOW can never become an item** — the AI
  cannot cast it, however you equip it.
- Factory **0x811cc0** (AE 44882), SPEL branch, gates in order:
  1. `GetSpellType()` must be 0 (`kSpell`) or 5;
  2. race equip-slot admissibility (0x810340 against `race+0x410`);
  3. **SKILL GATE** — a per-effect visitor (vtable AE **211957**, slot 1 =
     0x81de80): for every effect with `associatedSkill != -1`, the actor's
     **current** `GetActorValue(associatedSkill)` must be `>= EffectSetting::
     minimumSkill`. This is not the tome's level. **A spell above the actor's
     skill AV never becomes a combat item**, so the AI silently will not cast it.
  4. classification (below). No row -> **no item is created at all**.

## 10.2 ADMISSION — the classifier, and the row that does not exist

`CombatMagicItemData` (RTTI `.?AVCombatMagicItemData@@`, vtable **0x18d5120** =
AE **211955**) is a stack-local per-spell resolver. Its ctor 0x81d5a0 records
`+0x10` spell, `+0x18 CombatController*`, `+0x4c = (GetDelivery() == kSelf)`,
`+0x4d = (GetCastingType() == kConcentration)`. `MagicItem::VisitEffects`
(0x14c780) then calls **slot 1** (`0x81d830`, AE 45321) once per effect —
`call [rax+8]`, i.e. an ordinary virtual dispatch, so it is hookable.

```
key = (archetype << 16) | ((primaryAV & 0xFF) << 8) | (hostileFlag << 1) | isSelfDelivery
```
`archetype` = `EffectSetting` +0xC0; `primaryAV` = +0xC4 (only when the archetype
flag table 0x1fd3028 bit1 is set, else 0xFF); `hostileFlag` = `EffectSetting::
flags` (+0x68) bit 0 (`kHostile`); `isSelfDelivery` is the **SPELL's** delivery,
not the effect's. The key is looked up in a hash map at 0x31ab790, built by
0x81dc90 (AE 45326) from a **23-row table at 0x20163b0**. Archetype-0 rows are
additionally aliased under archetypes 5, 4, 32 and 34.

**THE HEADLINE FACT, and the most useful single thing in this document:**

> **There is NO row for `(ValueModifier, Health, self=0, hostile=0)`.**
> A heal-**other** spell therefore hashes to nothing, the resolver's chosen-row
> field (+0x30) stays null, and the item factory creates **nothing**.
> **Vanilla NPCs cannot heal an ally through the combat AI — not because of
> tuning or targeting, but because the AI's DATA MODEL cannot represent the
> spell as a combat item.** Everything downstream (score, equip gate, caster,
> target, aim) is unreachable for it.

Corroborating detail that proves bit 0 is a delivery class rather than a
"beneficial" flag: **Summon has BOTH rows** — r14 `(18, -, self=1, hostile=0)`
and r15 `(18, -, self=0, hostile=0)` — both pointing at the same creator.
Restore exists only at r10 `(0, Health, self=1, hostile=0)` and r11
`(0, Magicka, self=1, hostile=0)`. Offensive is r0 `(0, Health, self=0,
hostile=1)`. Rows for `(0, Magicka/Stamina, self=0, hostile=1)` and
`(0, Stamina, self=1, hostile=0)` exist with a **NULL creator**, which is a
different and equally useful fact: a spell whose only effects hit creator-NULL
rows also gets no item, which is why NPCs never cast pure magicka-damage spells.

**Multi-effect rule:** the item is typed by the **highest-scoring effect that
has a non-null creator** (`0x81dba0` keeps the best `{score, effect, row}` and
skips creator-NULL rows).

**Per-hand duplication (`0x811ac0`):** for an EitherHand equip slot the item is
added once per parent slot — the original for the first hand, `Clone()`
(vfunc 0x0D) for each further hand — each carrying `item+0x20 = BGSEquipSlot*`
and `item+0x28 = slot bitmask`. This is why a census shows exactly two entries
per either-hand spell, and why a right-hand weapon does not mask out a left-hand
spell.

## 10.3 SELECTION — how an item wins a hand

Selector **0x8134c0** (AE 44899). It walks **categories in a fixed preemption
order** read from the image: table RVA **0x20162c8 = `[1, 2, 4, 0, 3, 5, 0, 6]`**
(count 8 at 0x20162f0). **Category 1 (Restore) is evaluated FIRST; category 0
(Offensive) is FOURTH.** Within a category, candidates are popped highest-score
first. Gate ladder per candidate:

1. slot-mask collision — `item->+0x28 & set->slotMask` -> skip;
2. **per-FORM availability counter** (blackboard map) reached 0 -> **hard drop,
   silent, and apparently permanent** — a form that once failed to equip is
   barred with no seat observing it;
3. range hysteresis — may divert the item to an "out of range" scratch list
   **without ever calling the equip gate**. The band compares the candidate
   against items already in the set this pass, **not** against the distance to
   the combat target;
4. `CheckShouldEquip` (vfunc **0x0F**) false -> **hard drop**;
5. `GetResource` (vfunc **0x10**, 0x8199b0) — tests exactly one thing
   (`0.0 >= item->+0x40`) and **cannot drop an item**: both branches converge on
   AddItem;
6. a magicka **budget walk** (0x815050 = `ResourcePool::GetOrCreateEntry`, not a
   comparison) — normally "spend and add";
7. `CombatEquipment::AddItem` **0x80e490** — the real occupancy rule:
   **one item per equip-slot bit per set, first-come-first-served, with no score
   comparison anywhere in it.**

There are exactly **two** `CombatEquipment` sets (`+0x118` / `+0x148`) with
independent slot masks, each filled by its own full ordered pass; the tail does
not pick a winner between them. So the granularity that matters is the **equip
slot bitmask**, not "left hand / right hand".

**Practical consequence:** score decides ORDER, `CheckShouldEquip` decides
ADMISSION, and the slot bitmask decides OCCUPANCY. A zero score does not exclude
an item. A deny built only on score is not a deny.

## 10.4 DECISION — and the fact that there is no caster set

**`CombatMagicCaster*` objects are not a per-actor registry.** `CreateCaster`
(item vfunc **0x15**) is called from exactly one place: the
`CombatBehaviorContextMagic` constructor **0x89eae0** (vcall at 0x89ebb4), on
whatever `CombatInventoryItem` occupies the behaviour thread's equip slot. **The
caster class is a pure function of which item is in the slot**, minted per cast
and released with the context. "The Restore caster never ran" therefore means
"no Restore item was ever in the slot", not "the caster was filtered".

The four decision seats, all vfuncs on the minted caster:
`0x06 CheckStartCast` (whether), `0x0A GetMagicTarget` (where),
`0x07 CheckStopCast` (how long), `0x0D SetupAimController` (aim).
The **base** `GetMagicTarget` is `delivery == kSelf ? controller.attacker :
controller.target` — so a non-self restore spell is aimed at the **foe**. That
branch is dead code in vanilla only because no non-self restore item can exist
(10.2); the moment one does, the native AI would try to heal its enemy.

Restore's own admission and stop rules both run a health-percentage test:
`threshold = lerp(fCombatRestoreHealthPercentMin 0.0, fCombatRestoreHealthPercentMax 0.5, combatStyleGeneralData+0x24)`,
with `fCombatRestoreHealthRestrictTime` 15 s, `fCombatRestoreStopCastThreshold`
0.25 and `fCombatMagicConcentrationMinCastTime` 2.0 s (engine defaults; none
overridden in `Skyrim.esm`). That is why a vanilla NPC "never" self-heals: it
only starts below ~50% and usually much lower.

## 10.5 EXECUTION

Leaf -> `MagicCaster::CastSpell` **0x5bb720** (AE 34401) -> `SetCurrentSpell`,
`state = 1`, `RequestCastImpl`. **`RequestCastImpl` does not start a cast** — it
sets the animation-graph bool `bWantCastLeft`/`bWantCastRight`/`bWantCastVoice`
(via `Actor::SetWantCast` 0x69c120, AE 37950) and returns. The graph's own
`LeftHandSpellCastHandler` / `...FireHandler` handlers then advance the state
machine (`StartCharge` 2 -> ready 3 -> `StartCast` 4 -> `Release` 5 -> sustained
6). **The animation is the driver, and `currentSpell` must already be set** —
calling `RequestCastImpl` with a null `currentSpell` is a silent refusal that
leaves the state at 0 forever.

Readiness the AI itself gates on is the graph bool `bMLh_Ready` / `bMRh_Ready`
(`IsCastingSourceReady` 0x6b91e0), **not** weapon-drawn state.

## 10.6 The five rules a mod author should take away

1. The AI can only cast spells the actor **knows** and whose effects are within
   the actor's **skill AV**.
2. The AI cannot represent a **heal-other** spell at all.
3. The caster you want to hook may not exist yet — it is minted from the
   **equipped item**, so "hook the caster" is downstream of "win the slot".
4. Equipment is re-selected on a cadence; **force-equipping fights the AI and
   loses**. Steering `CalculateScore` (vfunc 0x0C) is the aligned lever.
5. A control seat that is never called is indistinguishable from a control seat
   that is wrong. **Prove the path RUNS before building on it.**

# 11. `Actor::StartCombat` — ABI and semantics (the 2-arg call is a CTD)

```
bool Actor::StartCombat(Actor* this /*rcx*/, Actor* target /*rdx, nullable*/,
                        CombatGroup* group /*r8, nullable*/)
```
1.6.1170 **0x6b6930**, AE id **38561** (SE 37608). **`r8` IS dereferenced when
non-null** (`cmp [r8+0x30], 0`, then a `CombatManager` call) — a two-argument
call leaves garbage in R8 and crashes. Always pass the third argument.

Semantics worth knowing before you reach for it:

- Refuses outright for the player, for a dead/dying `lifeState`, for
  `target == this`, and (when a certain actor float is 0) beyond a distance
  gate — a plausible explanation for "StartCombat did not take" at long range.
- If a `CombatController` already exists it only switches/joins; otherwise it
  creates one via `CombatManager` — **`group ? ... : target ? ... : none`.**
  **A `CombatController` exists only against a concrete target or an existing
  group; there is no "combat with nobody".** Since the combat inventory, the
  items, the casters, the magic context and the cast leaves all hang off the
  controller, none of that machinery exists out of combat.
- Two clean entries: `StartCombat(foe, nullptr)`, and `StartCombat(nullptr,
  existingGroup)` — the engine's own "an ally joins the fight" path.
- **Never pass the player as the target**: the function runs crime/hostility
  bookkeeping on that branch.
- It also performs a **direct `ActorEquipManager` weapon equip** that bypasses
  the combat-inventory selector entirely — so any equip deny built on the
  selector's seats does not cover it.

# 12. Doctrine: borrowing one facet out of a state you don't want

Engine capabilities are frequently only reachable inside a state that switches on
a lot of other behaviour (the whole cast machinery above only exists while a
`CombatController` is live). The workable pattern is not to avoid the state and
not to accept it wholesale, but to **enter it as a substrate, take the one facet
you came for, and deny everything else it switched on**.

That is only legitimate when the deny is **complete**. The useful review question
is therefore never "which side effects can we live with?" but **"for every facet
this state switches on, do we have a complete deny, and where are the holes?"**
Write the inventory down, name the holes, and do not force the state until the
holes are either closed or explicitly accepted in writing. In the case above the
inventory came out negative — combat movement leaves and the target/alarm
bookkeeping had no deny — so forcing combat out of combat was correctly refused
even though the cast facet worked.
