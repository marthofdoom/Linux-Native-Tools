# Pillar 5 — Instance data & event-driven native mods (1.6.1170 / AE)

Everything below was **validated in-game** during MEO's M2–M4 arc (v0.5.0 →
v0.7.2) on a heavy load order (Lorerim). Reference implementation:
`../marth-enchanting-overhaul/native/plugin.cpp`. Canonical copy: `../marth-enchanting-overhaul/Docs/ENGINE_NOTES.md`
(synced from MEO 2026-07-09 — §3 updated, §9–§11 added; MEO's repo stays authoritative).

**Standing doctrine (Marth):** replicate engine features by CALLING the
engine's own flow, the way SKSE's Papyrus natives do — never hand-write the
state a flow produces. Before mutating instance/extra data, read the SKSE64
source (github.com/ianpatt/skse64) for the equivalent Papyrus native and
replicate *those* engine calls. Distrust CommonLibSSE-NG's C++
reimplementations for engine-visible state (they cut corners; see SetName
below). M2 burned ~6 release cycles rediscovering, field by field, what one
engine flow does in one call.

---

## 1. The self-describing item instance (zero hooks)

A per-instance "socket" is three extra-data entries on the instance's
`ExtraDataList` (inventory entry's list, or the world reference's
`extraList`). The engine serializes them in the `.ess` natively — the item IS
the database; it travels through drop/pickup/containers/saves with no script:

| Extra | Role |
|---|---|
| `ExtraUniqueID` (0x9F) | instance identity — **only unique per base form**; any map must key on `(baseFormID, uniqueID)` |
| `ExtraTextDisplayData` (0x99) | display name (see §2) |
| `ExtraEnchantment` (0x9B) | the *created* enchantment (see §3) |

## 2. Renaming an instance — the five traps

1. **Force, never add-if-absent.** The engine lazily creates a *blank*
   `ExtraTextDisplayData` for any TEMPERED item (health ≠ 1.0) to render
   "Fine <name>" — add-if-absent silently skips all tempered gear. Mirror
   SKSE `SetDisplayName(force=true)`: reuse the record, null
   `displayNameText`/`ownerQuest`, then `SetName`.
2. **temperFactor must equal ExtraHealth.health.** NG's `SetName` reimpl
   hardcodes `temperFactor = 1.0`; on tempered gear the mismatch makes the
   pickup notification (and other UIs) fall back to the base name. Fix by
   the engine's own builder: `xText->GetDisplayName(baseObj, health)` —
   real engine fn, `RELOCATION_ID(12626, 12768)` — reconciles
   suffix/customNameLength/temperFactor.
3. **NO SQUARE BRACKETS in display names.** UI suites (moreHUD / BTPS /
   Simple Activate render the activate rollover via Scaleform `htmlText`)
   strip a leading `[...]` as an icon/categorization tag. `[MEO] Glass
   Dagger` rollover-rendered as "Glass Dagger" — indistinguishable from a
   failed rename; proven with a table-renamed control (`[pookie] X` → "X").
4. **`ExtraDataList::GetDisplayName` resolves through `ExtraReferenceHandle`
   (0x1C)** to the ORIGINAL reference's name data when present. An inventory
   entry that kept the handle from its pickup can read a *stale* name from
   the old world ref. (The enchanting table's output never carries 0x1C —
   it mints a fresh entry.)
5. **`displayName` INCLUDES the temper suffix after the reconcile.** After
   `GetDisplayName(base, health)` (`RELOCATION_ID(12626, 12768)`),
   `displayName` holds "Name (Fine)" and `customNameLength` is the length
   *without* the temper string. Any string surgery (prefix swaps, ends-with
   tests against the base name) must operate on
   `displayName.substr(0, customNameLength)` or it silently fails on every
   tempered item. Corollary: only a `kCustomName` record is a real custom
   name — the engine's lazily-created blank (trap 1) is `kUninitialized` yet
   holds the DECORATED name; treat it as custom and the next `GetDisplayName`
   appends a *second* suffix ("… (Fine) (Fine)"). Gate name-capture on
   `IsPlayerSet()` (the `kCustomName` comparison, and the only spelling that
   compiles — the scoped enumerator isn't injected into class scope).

## 3. A functional instance enchantment (the SKSE recipe)

Attaching a base `ENCH` form via `ExtraEnchantment` shows the description on
the item card but applies **no effect**. The working flow (from SKSE64
`PapyrusWornObject.cpp`, the code behind `WornObject.CreateEnchantment`):

1. Build a player-style **created** enchantment from MGEF effect items:
   `RE::BGSCreatedObjectManager::GetSingleton()->AddWeaponEnchantment(
   BSTArray<RE::Effect>&)` (SKSE name:
   `PersistentFormManager::CreateOffensiveEnchantment`). Created forms get
   FF-prefix runtime FormIDs and are engine-persisted in the save.
2. **Make it free** (MEO v0.20.0 find) — `AddWeaponEnchantment` auto-computes
   a per-hit CHARGE COST from magnitude, and `ExtraEnchantment.charge` is a
   `uint16` (max `0xFFFF`): a scaled-up magnitude starves the charge and the
   enchant **shows its description but never fires** — and its elemental glow
   only reconciles on equip (the classic "FX lag until sheathe/redraw" is
   this, not an FX-mod bug). If the enchant should not drain, force cost 0:
   `ench->data.costOverride = 0;`
   `ench->data.flags.set(RE::EnchantmentItem::EnchantmentFlag::kCostOverride);`
   then attach with `xList->Add(new RE::ExtraEnchantment(ench, 0xFFFF, false))`.
   A free, fully-charged enchant applies damage AND visuals immediately on
   stamp, no re-equip. Armor: `AddArmorEnchantment` + constant/self MGEFs.
3. If the item is currently equipped:
   `actor->UpdateWeaponAbility(baseForm, xList, leftHand)` (armor:
   `UpdateArmorAbility(baseForm, xList)`, no hand arg)
   (`RELOCATION_ID(37803, 38752)`) — activates the magic caster. Skipping
   it = description with no effect. **This only works in a live session — it
   cannot revive delivery after a game load; see §9.**

`RE::Effect` fields: `effectItem{magnitude, area, duration}`, `baseEffect`
(the `EffectSetting*`), `cost` (the effect's own — the enchant recomputes its
per-hit cost from magnitude, hence step 2).

## 4. Event sinks — hook-free triggers (all validated)

| Event | Shape | Notes |
|---|---|---|
| `RE::TESEquipEvent` | `{actor, baseObject, uniqueID, equipped}` | plain unenchanted items report `uniqueID=0` |
| `RE::TESSpellCastEvent` | `{NiPointer<TESObjectREFR> object; FormID spell}` | fires for lesser powers too → menu-less "cast a power" UX |
| `RE::TESDeathEvent` | `{actorDying, actorKiller, bool dead}` | fires twice; act on `dead == true` |
| `RE::TESCellAttachDetachEvent` | `{NiPointer<TESObjectREFR> reference; bool attached}` | **fires per reference**, not per cell — ideal for stamping world loot at load |
| `SKSE::CrosshairRefEvent` | `{NiPointer<TESObjectREFR> crosshairRef}` | via `SKSE::GetCrosshairRefEventSource()`; read-only ground-ref diagnostics |

Register TES events on `RE::ScriptEventSourceHolder`; keep sink bodies to a
queue-only shim and defer mutation to `SKSE::GetTaskInterface()->AddTask`.
**Caveat — AddTask does NOT run on the main thread in this runtime; see §21
before mutating live engine state from a task.** `TESEquipEvent` dispatch is
*synchronous* into follower AI and third-party outfit managers (guaranteed
present in a heavy order), so cycling gear inside the handler re-enters them —
snapshot `(object, xList, key)` tuples first and re-find live records by key at
act time. A **global** SKSE actor event (`RegisterForActorAction`) fires for
*every actor in the load order* and the cost is the dispatch, not the handler —
bailing early inside the handler does not help; never build "react to every
actor" on it.

## 5. Native message box with buttons (no UI framework)

Verified against Exit-9B/ForgetSpell + D7ry/valhallaCombat:

```cpp
auto* factory = RE::MessageDataFactoryManager::GetSingleton()
    ->GetCreator<RE::MessageBoxData>(RE::InterfaceStrings::GetSingleton()->messageBoxData);
auto* box = factory->Create();
box->unk4C = 4;  box->unk38 = 10;          // required magic (copied from working mods)
box->bodyText = "Socket which gem?";
box->buttonText.push_back("Fire I");        // up to ~10 buttons; paginate past 8
box->callback = RE::BSTSmartPointer<RE::IMessageBoxCallback>{ new MyCallback };
box->QueueMessage();
// In MyCallback::Run(Message m): button index = static_cast<int32_t>(m) - 4
```

The `- 4` offset on the callback message is the trap everyone hits.

## 6. Co-save discipline (SKSE serialization)

- Versioned records; keep readers for every shipped version forever, write
  only the newest. Never reorder/remove fields — extend via version bump.
- Store stable STRING identities (catalog gids / effect signatures), never
  enumeration indexes — they must survive load-order and catalog changes.
- Key per-instance records on `(baseFormID, uniqueID)` (§1).
- Newer-version records on an older DLL: log loudly, leave unread, don't
  crash (downgrade = sockets inert for the session).
- One-time grants (starter kits): only consume the persisted flag when the
  grant actually succeeded — a missing ESP must retry next load, not burn it.

## 7. Ops traps (test protocol)

- **Stale DLL voids tests.** The MEO.log version header is the mandatory
  first check before believing any in-game result (bitten twice).
- **MO2 has two checkboxes**: left-pane mod AND right-pane plugin.
  `profiles/<P>/plugins.txt` needs `*MEO.esp` — starless = not loaded, and
  the only symptom is our own "form not found" log line.
- Zero-code control items beat code diagnostics: an enchanting-table-renamed
  weapon is an engine-canonical record to diff against (`DumpXList` in
  plugin.cpp prints the full extra-data anatomy of any instance).
- Proton log path:
  `compatdata/<appid>/pfx/drive_c/users/steamuser/Documents/My Games/
  Skyrim.INI/SKSE/MEO.log` (Lorerim appid 3375297225; truncates per launch).
- **Enchant-visual mods key off equip events**: an ability applied via
  `UpdateWeaponAbility` on a drawn weapon works immediately, but FX mods
  won't show the glow until sheathe/redraw. External timing, not fixable
  from the stamping side.
- **`TESCellAttachDetachEvent` only covers loose world refs**: container
  contents, NPC inventories, and rack/display (linked/persistent) items do
  not come through it — inventory entries aren't references. Pre-processing
  those needs a container-changed sink or stamp-on-transfer.

---

## 8. Pipeline additions beyond the MRO-era docs (the 2026-07 diff)

Additions MEO made on top of pillars 1–4; reference code in
`../marth-enchanting-overhaul/`:

- **ESP generator, more record types** (`MEO_GenerateESP.py`): MISC, MGEF,
  SPEL (lesser power), QUST (+ VMAD script property baking), FLST, and the
  ESL header flag (TES4 flags |= 0x200; new records in 0x800–0xFFF). Also
  emits a runtime JSON manifest mapping catalog keys -> generated FormIDs.
- **Embed data in the DLL instead of runtime JSON**
  (`tools/gen_catalog_header.py`): codegen a constexpr C++ header from the
  design-data JSON at commit time. No file paths to break in-game, no parser
  dependency, catalog can't drift from the code consuming it.
- **MO2-installable release wrapper** (`tools/release_native.sh`): pulls the
  CI-built DLL from the latest green Actions run and zips it with the zip
  root as the virtual `Data/` folder (`MEO.esp` + `SKSE/Plugins/MEO.dll`) —
  installs in MO2 with zero manual placement. Releases are immutable,
  one testable change per release.
- **Runtime form resolution**: `RE::TESDataHandler::LookupForm<T>(localID,
  "Plugin.esp")` per form at `kDataLoaded`; a missing master just disables
  that one feature with a log line (never a hard requirement).
- **Native power-grant replaces Papyrus startup quests**: `Actor::HasSpell`/
  `AddSpell` at `kPostLoadGame`/`kNewGame` (after the co-save load) — an ESP
  whose VMAD scripts are simply not shipped is benign.

### ExtraUniqueID does NOT survive container transfers (MEO, 2026-07-07)
The engine reassigns `ExtraUniqueID::uniqueID` per-container (fresh ids from
1) when an item moves between containers. Stable within one container only;
world-ref drop/pickup and equip/unequip preserve it, `player -> chest ->
player` rewrites it. Any (baseFormID, uniqueID)-keyed instance store breaks
the moment the item passes through another container — MEO v0.11.0's
ContainerMenu "Gem Pouch" lost banked-XP records this way (log-proven:
deposited instances arrived as uid=0x1) and was scrapped same-day.

**SOLVED (MEO v0.27.0-m19, proven in-game 2026-07-09): re-key via a
`TESContainerChangedEvent` sink.** The event's `uniqueID` field names the
**ARRIVING (new-container) uid** (field-proven: `[rekey] uid 36933 -> 42
(evUid=42)`). Recipe: gate cheaply (does the moved base have records at
all?), defer to a task, then in the new container find the arriving orphan
xList (has `ExtraEnchantment`, no record) and the stranded record (uid
present in neither old nor new container), and move the record to the new
uid; prefer the event uid when it matches either side, and on ambiguity
(multiple simultaneous identical instances) log + skip — worst case is one
orphaned instance, never corruption. With this, uid-keyed instance stores
survive corpse looting, vendor purchases, and player storage.

**UPDATE (MEO m49, 2026-07-17): the ambiguity log+skip is a permanent
RATCHET — disambiguate by effect signature instead.** One stranded record
poisons EVERY later transfer of that base (its uid is always "present in
neither container," so any future transfer sees >1 stranded and skips
forever). Field case: an ancient orphan record + a newly-bought converted
item of the same base → skip → the new item's per-instance enchant is
invisible/unmanageable. Fix: resolve the ARRIVING orphan first, then when
the stranded set is ambiguous, filter it to the record whose own effect
identity appears in the arriving instance's created enchant (pointer match
of the effect settings, or a signature compare for ranked kin). Exactly one
survivor → re-key it; zero or >1 → keep skipping (mis-assignment is worse
than a strand). It self-heals a poisoned save on the next drop+re-pickup.
Also: a vendor's barter offerings come from THREE places — the merchant
`vendorData.merchantContainer` chest, the LVLI re-roll into that chest at
barter-open (§ vendor-restock), AND the vendor ACTOR's own personal
inventory (NPC-record leveled lists). A sweep that only touches the chest
misses the actor's personal stock.

## 9. Instance enchantments DIE on game load — only a real re-equip revives them (MEO m14–m17b, settled 2026-07-09)

Instance `ExtraEnchantment` delivery does NOT survive a save/load. **Every
piece of persisted data survives intact** — instrumented across three builds:
the FF created enchant, its `kCostOverride`/`costOverride`, the
`ExtraEnchantment.charge`, and even the actor's `kLeft/RightItemCharge` AVs
all round-trip perfectly. Yet the worn item's enchant is inert after load
(shows on the card, never fires) until re-equipped.

- **The engine's post-load equip reconstruction registers enchant delivery
  from the item's BASE form (`formEnchanting`) only** — it never reads the
  instance `ExtraEnchantment`. Items enchanted the vanilla-table way work
  because the table mints an enchanted base form; instance-enchanted items
  don't have one.
- `Actor::UpdateWeaponAbility`/`UpdateArmorAbility` **cannot** fix it
  post-load (verified: called 4× over 8s, still dead). They work fine
  in-session on an item that went through a live equip.
- REFINED (MEO m19f): the real rule — **the ability refresh only takes when
  the enchant extra CHANGES**. A same-form rebuild no-ops against the save's
  stale ability bookkeeping; re-equip works only because unequip is a forced
  teardown. The blinkless revival (no equip cycle, invisible): strip
  `ExtraEnchantment` + `Update*Ability` (teardown), then NEXT task rebuild +
  `Update*Ability` (fresh ability) — the same flow an in-session socket
  change uses, which is why socketing never needed a re-equip.
- **Timing**: `kPostLoadGame` is too early — the engine is still finalizing
  its own load-equip and the cycle conflicts/gets undone. A blind delay is
  ALSO unreliable: on heavy-area loads a +4s timer fired during the loading
  screen and was swallowed (field-hit). Anchor to the **Loading Menu
  closing** (`MenuOpenCloseEvent`, gameplay resumed) + ~1.5s fade margin,
  with a long fallback timer; one pass = one visible blink.
- Build trap: `d3d11.h` → `wingdi.h` `#define`s `GetObject` → `GetObjectW`,
  hijacking `BGSDefaultObjectManager::GetObject<T>()`. `#undef GetObject`
  after the D3D includes.

## 10. World-ref ownership: spawn-and-pickup is THEFT in owned locations (MEO m17b)

A `PlaceObjectAtMe` reference has NO owner, so ownership falls back to the
cell/location owner. `Actor::PickUpObject` on it near witnesses = **witnessed
theft, instant bounty** (found in-game: a bounty per gem swap in town — the
spawn→stamp→pickup instance-minting recipe from §1 was the criminal). Any
spawn→pickup flow must first:
`ref->extraList.SetOwner(player->GetActorBase());`
Apply the same to `RemoveItem(kDropping)`-minted refs before re-pickup.

## 10b. `AddObjectToContainer` LINKS (does not copy) the ExtraDataList — the entry OWNS the pointer (MEO m44→m47)

To mint an instance (custom `ExtraDataList`, e.g. a socket enchantment) INTO a
container, the CORRECT engine flow is: build a HEAP `ExtraDataList*` with the
engine's OWN ctor, stamp it, then
`holder->AddObjectToContainer(base, xl, 1, nullptr)`. **NO placeholder world ref.**

```cpp
RE::ExtraDataList* MakeEngineXList() {
    auto* mem = RE::MemoryManager::GetSingleton()->Allocate(0x20, 0, false);
    if (!mem) return nullptr;
    using ctor_t = RE::ExtraDataList* (*)(void*);
    static REL::Relocation<ctor_t> ctor{ RELOCATION_ID(11437, 11583) };  // SE 11437 / AE 11583
    return ctor(mem);
}
```

**CONTRACT — proven at disassembly on 1.6.1170:**
`TESObjectREFR::AddObjectToContainer` (vtable slot 0x5A) forwards to the
InventoryChanges worker (addrlib id **16053**), which allocates a fresh entry
+ a 0x10-byte `BSSimpleList` node and at **`+0x484`** does `mov [node], rsi` —
i.e. it stores **the caller's ExtraDataList pointer** into the entry's
`extraLists` list. **It LINKS the pointer; there is NO deep-copy of BSExtraData
anywhere in the engine. The container entry then OWNS that pointer** (frees it
with the entry). `a_fromRefr` is used for ownership/companion bookkeeping only;
it is neither consumed nor does it change list handling. AE ctor id **11583** =
RVA 0x151E90 (sets the vtable — AE 1.6.629+ made BaseExtraList virtual, which is
why CommonLibSSE-NG **declares but never defines** `ExtraDataList::ExtraDataList()`
and `new RE::ExtraDataList()` won't link; the reloc ctor is the only way). The
ctor leaves the presence bitfield null; the engine `Add` worker lazily allocates
and zeroes it on first use, so `HasType`/`GetByType` on the fresh list are safe.

**⚠ The prior note here (and MEO m44) was WRONG: "copies / entry owns
independently / reap the placeholder" is FALSE.** m44 did
`PlaceObjectAtMe` → stamp `&ref->extraList` (an interior pointer at
TESObjectREFR+0x70) → `AddObjectToContainer(base, &xl, 1, ref)` →
`ref->Disable(); ref->SetDelete(true)`. Since the engine LINKED
`&ref->extraList`, deleting the ref freed a list the container entry still
owned — a use-after-free. Inventory extra-data transfers **by pointer** between
`InventoryChanges` on every loot/buy/transfer, so the dangling list rode the
converted item into the player's inventory, was freed at the source cell's
detach, and detonated on the next inventory walk (a keyword-condition re-eval,
savegame serialize, or item destroy): a torn `0x2` pointer AV at a
`SkyrimSE.exe` offset with the mod nowhere on the stack (MEO issue #2, fixed
m47/v1.0.6c; a tbbmalloc `Block::freeOwnObject` free-path AV is the same plant
detonating on the entry side). It shipped ~24h on Nexus because the m44 review
validated the visible SYMPTOM (no under-counter duplicate) without checking the
copy-vs-link contract.

The live-ACTOR path is different and fine: `Actor::PickUpObject` CONSUMES the
placeholder ref, so `PlaceObjectAtMe → stamp ref->extraList → PickUpObject`
correctly moves the ref's own list into inventory with no dangling pointer.
Never pass a pointer interior to an object you then destroy to any
ownership-taking inventory API.

## 11. Biped slots + menu dismissal (MEO m12/m18)

- **`BGSBipedObjectForm::HasPartOf(mask)` is `.all()`** — every bit in the
  mask must match, so a combined multi-slot mask demands one piece fill ALL
  slots (always false). Test each slot and OR the results.
- **Dismissing an engine menu**: queue it through the engine —
  `RE::UIMessageQueue::GetSingleton()->AddMessage(MenuName,
  UI_MESSAGE_TYPE::kHide, nullptr)`. Used to replace the enchanting-station
  `CraftingMenu` with a custom menu at bench-open. CAVEAT: the hide fires the
  menu's normal CLOSE event — any "that menu closed → close mine" coupling
  must be gated or it kills the menu you just opened. Enchanting-bench
  detection: `player->GetOccupiedFurniture()` → base `TESFurniture` →
  `workBenchData.benchType == BenchType::kEnchanting` (3).

## 12. Mutagen on Linux: patching a full MO2 load order without the VFS (MEO m20)

.NET 9 + `Mutagen.Bethesda.Skyrim` runs natively on Linux and reads a
3,418-plugin MO2 order in ~1.5 s (lazy binary overlays; `ulimit -n 8192` —
every overlay keeps its file open). No game install, no VFS:

- **MO2 resolution by hand**: `profiles/<p>/plugins.txt` `*`-lines = enabled
  plugins in load order; `modlist.txt` is highest-priority-FIRST `+`-lines;
  first hit across `mods/<name>/` wins, `Stock Game/Data` is the final
  fallback; base masters (Skyrim.esm..Dragonborn.esm) aren't in plugins.txt.
- **Winning overrides**: build `ModListing<ISkyrimModGetter>` per plugin →
  `LoadOrder` → `.PriorityOrder.<Group>().WinningOverrides()` +
  `ToImmutableLinkCache()` resolves cross-master links (0.2 s for a perk
  tree walk).
- **Offline verification trick**: substitute a regenerated plugin at its
  load-order slot and append the not-yet-installed patch, then re-dump the
  winning override — proves the patch wins without touching the game dirs.
- **Writing**: `GetOrAddAsOverride(getter)` deep-copies into a new
  `SkyrimMod`; `WriteToBinary(path)` computes masters from actual links.
  `IsSmallMaster = true` sets the ESL header flag (0x200) — fine for
  pure-override patches, costs no load-order slot.
- **Perk tree / ranked perk / CTDA formats**: see MEO ENGINE_NOTES §11
  (AVIF nodes: FNAM root=300 nodes=1; NNAM rank chains; GetBaseActorValue
  CTDA = func 277, AV index in param1, op byte 0x60 for >=).

## 13. MGEF-level conditions travel with the effect (Skyrim, 2026-07-09)

An MGEF record's own condition list is evaluated wherever the effect is
used — including runtime-created enchantments from
`BGSCreatedObjectManager::AddWeaponEnchantment`. Copying an effect into a
generated enchant copies its gating for free (Requiem's
dwemer-bonus/vs-undead companions self-gate as riders). Per-effect-entry
conditions (CTDA on the ENCH effect item) do NOT travel — generated effect
items are bare unless you write conditions yourself. Also: Requiem
reimplements vanilla effects as Script-archetype MGEFs at the vanilla
FormKey (0x0B72A0 slow); Script archetypes function normally when
referenced by generated enchants — never treat archetype as a
replicability test. Staff-ness lives in ENCH.EnchantType ==
StaffEnchantment + Concentration/Aimed cast shape.

## 14. CommonLibSSE-NG: `TESDataHandler::LookupForm<T>` rejects abstract intermediates (MEO m23b, 2026-07-09)

`TESDataHandler::LookupForm<T>(id, plugin)` and `LookupFormRaw<T>` gate the
result on `form->Is(T::FORMTYPE)`. Intermediate classes such as
`RE::TESBoundObject` define no `FORMTYPE` — they inherit `TESForm`'s
`FormType::None` — so the check fails for every real form and the template
returns nullptr 100% of the time. It compiles clean and fails silently at
runtime (MEO shipped a 10,146-row loot-conversion table that resolved
"0 live"). The trap is asymmetric: `TESForm::LookupByID<T>` routes through
`form->As<T>()`, which handles intermediates correctly. Rule: pass the
data-handler templates CONCRETE record classes only; for an intermediate,
use the non-template `LookupForm(id, plugin)` and cast with
`->As<RE::TESBoundObject>()`. Corollary for any table-resolution pass: split
the skip counter by failure reason (item/base/whatever) in the log line —
a single aggregate count hides a 100%-systematic failure inside what reads
like ordinary per-row attrition.

## 15. Vendor stock RESTOCKS from leveled lists at BarterMenu-open, silently (MEO m38, 2026-07-13)

A merchant's barter inventory is not static: the engine re-generates the
vendor's leveled-list (LVLI) contents when the player enters trade (as the
`BarterMenu` opens), gated on the vendor's restock day counter vs
`iDaysToRespawnVendor`. Two traps for anything that rewrites vendor stock:

1. **Ordering.** `DialogueMenu`-open fires BEFORE the restock. A sweep hung on
   dialogue-open (the safe moment to mutate the chest — mutating it *during*
   barter-list construction corrupts the list; MEO's m19e Belethor breakage)
   therefore operates on the PREVIOUS restock cycle's stock. The engine then
   re-rolls fresh contents a beat later at barter-open, on top of your work.
   Log-proven (MEO deck 2026-07-13): `[convert] container … 7 item(s)
   converted` at dialogue time, yet the barter UI still showed re-rolled
   enchanted names that were absent from the converted-7 list — created by the
   post-sweep restock.
2. **No event.** LVLI regeneration emits NO `TESContainerChangedEvent`, so a
   container-changed sink never sees the new items (same silence noted for
   in-place vendor generation generally).

**Recipe:** do the authoritative sweep at `BarterMenu`-open, but defer TWO
frames (`GetTaskInterface()->AddTask` nested inside `AddTask`) so the list is
fully built before you touch the chest, then rebuild the open menu via the
game's own inventory-update routine — the identical one the engine fires on a
buy/sell. On CommonLibSSE-NG that helper is `RE::SendUIMessage::
SendInventoryUpdateMessage(ref, nullptr)`; on OLDER NG (≤3.7.0 it doesn't exist
yet) bind its relocation directly: `REL::Relocation<void(RE::TESObjectREFR*,
const RE::TESBoundObject*)>{ RELOCATION_ID(51911, 52849) }`.
Resolve the sell container as `vendorData.merchantContainer` (fall back to the
speaker actor's own inventory). Do NOT pre-empt the reset by hand — calling the
reset early and writing `vendorData.lastDayReset` yourself is hand-writing
engine bookkeeping and risks desyncing the vendor's gold refresh. Let the
engine restock, then convert, then ask it to refresh (call the engine's own
functions, as SKSE does).

## 16. Synthesis/Mutagen load order omits Creation Club; native-Linux drops CC master refs (MEO 2026-07-14)

Porting a Mutagen console patcher to a **Synthesis** patcher (distributed as
GitHub source Synthesis compiles locally — no binary to ship/scan/flag) surfaced
two traps that both silently REDUCE conversions:

**(a) Synthesis's `state.LoadOrder` omits Creation Club plugins that aren't in
`plugins.txt`.** On an Anniversary Edition install that is ALL of them — the ~74
CC plugins load via `<GameRoot>/Skyrim.ccc`, not `plugins.txt` (verified: deck
`plugins.txt` listed 0 of 74; `Skyrim.ccc` listed 75). Relying on the patcher's
`state.LoadOrder` dropped ~24% of conversions and an entire enchantment family
(`chaos`, a CC effect). FIX: in the patcher, build your OWN calibration load
order — read `Skyrim.ccc` from the game root, order `base masters -> ccc ->
plugins.txt`, resolve each name from `state.DataFolderPath` (works for vanilla
and MO2 alike), then feed THAT to the analysis. The perk-tree edit can still use
`state.LoadOrder` (CC adds no enchanting perks). The standalone installer already
did this (`ResolveGame` reads `Skyrim.ccc`); a Synthesis patcher must too.

**(b) Native-Linux (case-sensitive FS) makes Mutagen fail to resolve some CC
master references → fewer records.** Same binary, same load order: run
NATIVELY on Linux gave 4139 conversions (chaos=0); run under **Proton/Wine**
(case-INsensitive) gave 5392 (chaos=49). CC plugins/master refs whose casing
doesn't exactly match on disk resolve under Wine but not native Linux. This is a
Mutagen-on-Linux limitation, not app-specific. Consequence for TESTING: a
native-Linux run under-counts — always validate a load-order patcher via
Proton/Wine (or on Windows), or you'll chase a phantom regression. Consequence
for USERS: Synthesis run via Proton (typical Linux Skyrim setup) is
case-insensitive and correct; only native-Linux Synthesis is affected.

Verification recipe (Steam Deck, real load order w/ 74 CC): run the win-x64 build
under Proton — `STEAM_COMPAT_DATA_PATH=<compatdata/489830>
STEAM_COMPAT_CLIENT_INSTALL_PATH=<Steam> "<Proton>/proton" run <exe> ...` with
paths as `Z:\...`; Proton stdout does NOT pipe, so read the output files, not the
console. A byte-diff of the patcher's output vs the standalone installer's on the
same load order is the parity check.

## 17. Co-save FormIDs MUST pass ResolveFormID on load — raw rehydration dies on any load-order change (MEO v1.0.6 review, 2026-07-14)

An SKSE co-save stores whatever bytes you write — including the mod-index
byte of every runtime FormID. Users change load orders constantly (add,
remove, ESL-flag one plugin); the plugin indices remap, and every raw
rehydrated id now points into the WRONG plugin's FormID space. Symptoms are
silent and total: every persisted per-item record reads as dead, banked
state is unrecoverable, and a stale id can falsely attach to an unrelated
item. SKSE provides the cure — the co-save embeds the writing session's
plugin list and `SerializationInterface::ResolveFormID(stored, out)` maps a
stored id into the current order. Rules, all field-derived from MEO's 1.0.6
review:

- **Every** stored FormID resolves on load — including dynamic FF-form ids
  (created refs still pass through the handle map; an unresolvable one means
  the object is gone → recreate, don't reuse the stale id).
- Unresolvable → **drop the record and log it** (its plugin left the order);
  never guess or keep the raw id.
- **Bound every count and bail on a short read** — a truncated co-save must
  stop the parse, not fabricate keys from garbage.
- **Clamp deserialized fields at ingestion** (a corrupt level 0 indexed
  `thresholds[level-1]` out of bounds two subsystems away).
- **SKSE does NOT round-trip unread records**: if an older DLL loads a save
  with newer-versioned records and then SAVES, those records are destroyed.
  Warn loudly (log + one-shot message box); never log "preserved as unread"
  — that comforting falsehood steers users into data loss.

## 18. Never mutate a container/BSSimpleList you are iterating — snapshot targets first (MEO v1.0.6 review, 2026-07-14)

The engine's inventory structures (`InventoryChanges::entryList`, an entry's
`extraLists` — both `BSSimpleList`) are live singly-linked lists. Two ways a
"read-only" walk turns into mutation under your feet:

1. **Your own downstream call mutates the list.** MEO's kill-XP walk called
   a grant function that could `AddObjectToContainer` (an item birth) —
   a head-insert on the walked list makes the iterator revisit the head:
   double award that frame, chainable into spurious level-ups.
2. **Equip/unequip dispatch is SYNCHRONOUS into every registered sink.**
   Cycling an item mid-walk hands control to follower AI and third-party
   mods (outfit managers are guaranteed in a big order) which mutate the
   same inventory → node use-after-free.

The pattern: **collect (object, xList, key) tuples into a vector first, then
act** — and on the act side, RE-FIND live records by key rather than holding
references (an earlier action may have rewritten the map), and hold actors
by handle, re-resolved at act time. Same discipline for active-effect walks:
collect the dispel list fully before calling `ae->Dispel(true)`. Corollary
from the same review: the player-side path had the comment explaining all
this and the follower path (written later) violated it anyway — audit every
NEW call site against existing doctrine, the compiler won't.

## 19. MCM-Helper persists INI values per key name forever — a key that changes SEMANTICS must be RENAMED (MEO v1.0.6, 2026-07-14)

MCM Helper writes the user's settings to `Data/MCM/Settings/<mod>.ini` —
which lands in MO2's overwrite and SURVIVES mod updates. Consequence: the
key name is a permanent contract about the value's MEANING, not just its
spelling. MEO changed `fGemXpSkillXP` from an absolute rate (default 0.01)
to a ×multiplier (default 1.0) and the review found the old absolute value
live in the deployed profile's persisted INI — the new DLL would have read
0.01 as a multiplier and silently cut that XP stream ~100× for every
upgrading user, with the slider innocently showing the stale number. Fix:
**rename the key** (`fGemKillXpMult`) so stale persisted values are orphaned
rather than misread, and keep the DLL parse branch + the MCM config
generator in lockstep. Related INI hygiene from the same pass: strip the
UTF-8 BOM MCM Helper writes, and on an unparseable value warn and keep the
default — `strtof`'s silent 0.0 zeroed features.

## 20. Player-relative filters must be owner-gated when shared code paths serve NPCs (MEO v1.0.6 blocker, 2026-07-14)

Any helper that answers a question by scanning THE PLAYER's state (worn set,
inventory, active effects) is a landmine inside a code path that other
callers reach with non-player actors. MEO's 2-of-a-kind stacking cap asked
"is this instance among the player's top-2 worn copies?" — reached from the
NPC-gear stamping path, the answer was always no (an NPC's item is never in
the player's worn set), so the freshly built enchant was STRIPPED from every
NPC item and its record orphaned in the co-save permanently. The fix is
mechanical but must be total: **thread the owning actor through every layer
that gates on it** (stamp → rebuild), gate the filter on
`owner->IsPlayerRef()`, and treat "nullptr owner" as an explicit legacy
contract, not a wildcard. General rule: when writing any inventory/effect
scan helper, name WHOSE state it reads in the function comment, and audit
every caller for a non-player actor reaching it. (MEO is retiring the gate
entirely by moving the cap to a runtime active-effect tally — deriving from
live per-actor state beats owner plumbing.)

---

## 21. Follower / teammate detection, and why `IsPlayerTeammate` is not enough
*(MFO, 2026-07-21 — first project in this family to drive actor behaviour.)*

**Native enumeration:** walk `RE::ProcessLists::GetSingleton()->highActorHandles`
and filter. Hold `ActorHandle`, never `Actor*`, and re-resolve at act time —
followers cross cells, get dismissed, and die mid-tick. Papyrus cross-check is
`PO3_SKSEFunctions.GetPlayerFollowers()` (no quest alias needed).

**`IsPlayerTeammate()` is NECESSARY but NOT SUFFICIENT**, and the reason is
subtle enough that it was designed wrong twice:

- **Inigo** — one of the most-installed followers in existence — **sets** the
  teammate flag while following but **does not clear it on dismissal**. He
  signals dismissal with `GetActorValue("WaitingForPlayer") == -1`. So the flag
  alone reports him as an active follower *forever after you dismiss him*.
- **Vilja** carries a dedicated `DismissedFollowerFaction`; **Tindra** signals
  dismissal as rank 0 in her own follow faction. Same shape: still teammates,
  dismissal signalled elsewhere.
- **Pet frameworks** are the inverse case — `PetFramework_PetFollowingFaction`
  rank ≥ 1 grants follower status *without* teammate status.

So the working shape (read off Swiftly Order Squad's shipped
`IsFollower` / `IsDismissedCustomFollower`, which has solved this at scale) is:

```
cheap disqualifiers  (dead / disabled / deleted / is-player)
  -> teammate flag as the GATE
    -> quirk table that REVOKES for followers whose dismissal the flag misses
      -> (optionally) inclusion quirks that GRANT without the flag
```

Keep the quirk data resolving against the live load order and treat an absent
plugin as normal — log it at debug, never as an error.

**VALIDATED in-game** (MFO 0.0.1, 1.6.1170): teammate add/remove/re-add
detected deterministically across three cycles via a 2 s refresh.

---

## 22. Where an SKSE plugin's log lands is a CHOICE, not an environment fact
*(MFO, 2026-07-21.)*

Two destinations, and picking the wrong one means nobody finds your log:

1. **Game-root-relative** (`Data/SKSE/Plugins/X.log`) — MO2's USVFS redirects
   this into the profile's **Overwrite** folder, which is where most plugins
   in a Wabbajack-style setup land (ActorLimitFix, BugFixesSSE, ScrambledBugs,
   BarterLimitFix, …). Open the sink with that literal relative path.
2. **`SKSE::log::log_directory()`** — resolves into the *wine prefix's*
   `My Games\Skyrim Special Edition\SKSE\`, **a different filesystem**,
   typically holding only skse64's own logs.

MEO and MAO use (2), which is why their logs are not in Overwrite with
everything else. MFO uses (1) with (2) as a fallback for running outside MO2.

**Prefix location is worth verifying, not assuming.** On this machine the live
prefix is **umu** (`~/Games/umu/<appid>/`), not Steam's
`steamapps/compatdata/<appid>/` — the Steam one exists but has no SKSE
directory at all, so any path built from it silently finds nothing. MRO's
playbook cites a stale appid entirely.

---

## 23. CommonLibSSE-NG: `BGSDefaultObjectManager::IsObjectInitialized` loads a `bool[]` AS A POINTER and dereferences it (MEO v1.0.7-beta2, 2026-07-21)

`IsObjectInitialized` and the `GetObject<T>()` inline that calls it both
resolve the manager's `objectInit` array — a plain `bool[]` living at
`+0xB80` — via `REL::RelocateMember<bool*>(this, 0xB80, 0xBA8)`. That macro
is meant to compute an ADDRESS (`lea`) but here it emits a member LOAD
(`mov`, opcode `48 8b`): it reads the bytes AT `+0xB80` as if they were a
pointer, then dereferences that "pointer". The bug is old — broken in
upstream commit `61383c79` — and MEO's pin (NG **3.7.0**, REF `c4ab853d`)
sits squarely inside the broken window; the fix (`054cbcd4`, 2024-07-28)
landed on NG main but was never cut into a tagged release, so it ships in
every MEO build to date.

**Why it's silent on AE and fatal on SE.** The two runtimes disagree about
what lives at `+0xB80`:

- **SE 1.5.97** — `+0xB80` genuinely *is* the `objectInit` bool array. Eight
  `true` bytes read back as the 64-bit value **`0x0101010101010101`** — a
  register full of `0x01` bytes is the tell that a bool array just got read
  as a pointer. Dereferencing that "address" is a guaranteed access
  violation. 100% reproducible: any worn socketed **weapon** taken through
  an equip cycle CTDs.
- **AE 1.6.x** — the same struct offset happens to hold a populated,
  dereferenceable `TESForm*` left over from adjacent layout differences, so
  the bad load "succeeds" and AE survives **by accident**, not because the
  code path is correct there too.

Field-proven by a v1.0.7-beta2 report: 100% reproducible CTD on SE 1.5.97 at
`MEO.dll+0x4A62A` (the v1.0.6 line's equivalent site is `+0x458EA`), both
inside the equip-cycle slot lookup that calls the inlined `GetObject<T>()`.

**Fix:** never call `dom->GetObject<T>()` / `IsObjectInitialized()` against
this pin. Index `dom->objects[idx]` directly — same underlying engine data,
no broken inline in the path. Any sibling project pinned to NG ≤3.7.0
(REF `c4ab853d` or earlier) that touches `BGSDefaultObjectManager` carries
this bug too.

## Minting an `ExtraUniqueID` onto WORN gear (2026-07-28)

Two ways to give an inventory item a fresh `ExtraUniqueID` (per-instance
identity), and picking the wrong one for worn gear misbehaves:

- **Plain, UNWORN stack (no xList of its own):** use the engine drop/pickup
  flow — `actor->RemoveItem(form, 1, kDropping, …)` → on the dropped ref
  `extraList.SetOwner(actor->GetActorBase())` (avoid theft) +
  `extraList.Add(new RE::ExtraUniqueID(base, uid))` → `actor->PickUpObject(ref)`.
  Required because NG 3.7 declares but does not export the `ExtraDataList`
  ctor, so you can't `new` one; the engine must materialize it.
- **WORN gear:** it ALREADY has an xList (the `kWorn`/`kWornLeft` extra), so
  mint IN PLACE: `wornXList->Add(new RE::ExtraUniqueID(base, MintUID(base)))`.
  Do NOT drop/pickup a worn item to mint — `RemoveItem(kDropping)` unequips
  it, and on a non-player actor (a follower) that invites an AI re-equip race
  and can leave the item unequipped. Add the uid to the live worn xList and
  the item stays equipped; then apply the ability normally.

Found while adding follower gem-socketing to MEO (own gear displayed
read-only, minted only on the deliberate socket action). Cross-ref MEO
`Docs/ENGINE_NOTES.md` (build traps) + `INVARIANTS.md` 7c.

## SKSE inter-plugin messaging is bucketed by SENDER (2026-07-29)

Providing a message-exchange API between SKSE plugins:

- `RegisterListener(callback)` is `RegisterListener("SKSE", callback)` — the listener
  lands ONLY in SKSE's sender-bucket.
- `Dispatch(type, data, len, receiver)` iterates ONLY the DISPATCHING plugin's own
  bucket; `receiver` filters WITHIN that bucket — it does NOT route the message into
  the receiver plugin's bucket. So a consumer's `Dispatch(..., receiver="MyPlugin")`
  is NEVER delivered to MyPlugin's `RegisterListener(cb)` ("SKSE") listener.
- To RECEIVE directed inter-plugin requests, register a WILDCARD listener with a
  null sender: `RegisterListener(OnApiMessage, nullptr)` — a null sender sits in
  every plugin's bucket (the SKEE/RaceMenu pattern). Gate the handler on your own
  magic message type; the SKSE lifecycle broadcasts it also receives are then no-ops.
- The interface itself is a POD request struct (consumer passes a ptr, provider
  fills a function-pointer interface pointer) OR — the other common idiom — a
  `GetModuleHandle` + `GetProcAddress("RequestPluginAPI")` DLL export (what
  TrueHUD/TDM/Precision actually use; those are NOT messaging).
- Dispatch target name = the CommonLibSSE-NG version-data plugin name (CMake
  `project(NAME)` → `add_commonlibsse_plugin`), matched case-insensitively.

Found building MEO's `IMEO` inter-plugin API (for MFO). Cross-ref MEO
`Docs/ENGINE_NOTES.md`.

## 25. NEVER equip-cycle an actor who is IN or ENTERING furniture (MEO v1.0.8, 2026-08-04)

Seated/lean/crouch furniture entry is a SYNCHRONIZED enter→loop transition
(`BSSynchronizedClipGenerator`) that requires the actor to hold a stable
anim-graph state through the sit-down → seated-loop hand-off. An equip/unequip
event landing in that window forces a graph state change that ABORTS the
transition: the actor plays the full sit-down idle and is then INSTANTLY
EJECTED the moment the seated loop should take over. Standing crafting stations
(forge/enchanter) are idle-only (sit-state `kNormal`) with no seated hand-off,
so an equip cycle there is harmless — which is the diagnostic tell (forge fine,
chairs eject).

- **Guard:** skip any un/re-equip when
  `actor->AsActorState()->GetSitSleepState() != RE::SIT_SLEEP_STATE::kNormal`.
- **Why it was brutal to diagnose:** a load/refresh pass gated on the player's
  3D/drawn readiness re-fires the equip cycle on EVERY 3D rebuild — so
  `resurrect` and `disable`/`enable` never cleared it (they rebuild 3D and
  re-trigger the pass). The eject is produced LIVE each session, so working vs
  broken saves are byte-identical (an exhaustive save diff finds nothing). And
  the pass sweeps INVENTORY, not just worn slots, so un-equipping the offending
  item doesn't stop it (still in inventory). Trigger = merely CARRYING an
  instance-enchanted item; brand-new characters / NPCs without one are immune.
- **Deeper fixes (future):** make the refresh idempotent (don't cycle when the
  socket state is unchanged since last pass) and prefer refreshing the ability
  in place (no full unequip/re-equip, so no anim event is emitted at all).

Found fixing MEO's furniture-entry eject. Cross-ref MEO `Docs/ENGINE_NOTES.md`.

## 26. `SKSE::GetTaskInterface()->AddTask` does NOT run on the main thread (MFO, crash-stack-proven)

The KB long said "defer mutation to AddTask (main-thread)." **That is wrong for
this runtime.** Field-proven three times from crash stacks
(`… → skse64 task delegate → Job_Post_process → BSJobs::JobThread`): an AddTask
body runs on a **BSJobs job-worker thread**, overlapping the cell-streaming
threads — not the main thread. Re-queuing from inside a task lands on a worker
one drain later, not on main. Consequences:

- **`RE::TES::ForEachReferenceInRange` is unsafe in an exterior during a cell
  stream** — its exterior branch ends with `worldSpace->GetSkyCell()`,
  dereferencing the `TES::worldSpace` global while it is being rewritten
  mid-transition → torn-pointer AV. Iterate the actor's own
  `GetParentCell()->ForEachReferenceInRange(...)` (gated on `IsAttached()`)
  instead — the cell method walks only that cell's list under the cell's
  `BSSpinLock` and touches no worldspace/grid/skycell global.
- **`PickUpObject`/`Activate` tear down 3D and mutate the cell → main-thread
  only.** Loose-item pickup is therefore unreachable from an AddTask tick — make
  it a package-acquisition feature (let the engine walk the actor and grab it).
  Inventory *transfer* (`RemoveItem`/`AddItem`) does not tear 3D, so it is
  worker-safe.
- **The real road to main:** a `write_vfunc` on `RE::VTABLE_PlayerCharacter[0]`
  index **`0x0AD`** (`Actor::Update(float)`) that drains a mutex-guarded queue —
  fires once/frame on the main thread, player-only (hooking PlayerCharacter's
  vtable, not Character's, = zero per-NPC cost). `Update` is
  `SKYRIM_REL_VR_VIRTUAL`, so refuse to install on VR.

Capture actors by **handle + copies** in any deferred body (a frame passes before
it runs), never by reference.

## 27. Native instance-manipulation traps (equip, inventory walks, poison)

- **Two-handers must not be forced onto a hand slot when equip-cycling.** A
  two-handed weapon (`WEAPON_TYPE::{kTwoHandSword, kTwoHandAxe, kBow,
  kCrossbow}`) is worn as `kWorn`, never `kWornLeft`. Picking the equip slot from
  the worn flag hands a two-hander `kRightHandEquip` — a slot it can't occupy;
  the `applyNow` `ActorEquipManager::EquipObject` then completes only the
  AIProcess combat-equip half (weapon fires) and silently **skips the
  `ExtraWorn` stamp and 3D attach** → invisible-but-firing weapon, shown
  unequipped, save-baked. **Pass `slot = nullptr` for two-handers** so
  `ActorEquipManager` resolves the weapon's own BothHands slot; keep the explicit
  left/right pick only for one-handers.
- **`GetInventory()` returns a SNAPSHOT.** `InventoryEntryData`'s copy ctor
  allocates a fresh `BSSimpleList<ExtraDataList*>`, so a `GetInventory()` map
  hands you a copied entry + node-list (only the `ExtraDataList` pointees are
  live). You may convert/equip-cycle an item mid-walk over a `GetInventory()`
  map safely. The same walk over a **live** `changes->entryList` /
  `extraLists` must snapshot-first (§18) — equip dispatch is synchronous into
  every sink and head-inserts revisit the head. Verify which list you hold.
- **`ExtraPoison` is the engine's own weapon-coating slot.** Replace it on the
  equipped weapon's worn `ExtraDataList` to coat at runtime (engine flow — don't
  hand-write poison state). Time-expiry must **sweep every weapon in the
  inventory** (unequip can't bank remaining budget) and strip only *your* poison;
  strip stale coatings at load (the clock is runtime-only — don't serialize
  timers).
- For `RemoveByType`, the AE `CombatController` +8 shift, `LookupByEditorID`
  coverage, banned wider-than-intent calls, and the `unordered_map` rehash UB,
  see [commonlibsse-ng-traps.md](commonlibsse-ng-traps.md).

## 28. Station-menu takeover + MCM-Helper config discipline

- **Crafting-station takeover: hide the menu AND eject the actor.** A
  `MenuOpenCloseEvent` sink catching `CraftingMenu` can `kHide` the vanilla menu
  and open a custom overlay, but while the player is still *seated* the engine
  **re-activates `CraftingMenu` every few seconds**, flashing vanilla chrome
  through the overlay. Eject the actor via
  `NotifyAnimationGraph("IdleForceDefaultState")` on open *and* close. This is
  furniture-type-specific — an enchanting bench does not re-activate, so "it
  worked on the enchanting bench" is false evidence for the alchemy table. Verify
  per furniture type.
- **MCM-Helper reads a key absent from an *existing* Settings ini as OFF/zero**
  (it does not fall back to `config.json` `defaultValue`), so a control added
  after a user's first install looks dead. Backfill new keys into existing
  `Settings/<mod>.ini` on deploy. Companion rules: **one bad field drops the
  ENTIRE config** (bind controls with a top-level `"id":"key:Section"`, not
  `"modSettingName"`; `minMcmVersion` required at root) — the tell that a config
  registered is `Data/MCM/Settings/<mod>.ini` appearing; a key that changes
  *meaning* must be **renamed** (§19); reset-then-parse each ReadConfig so an
  absent key reverts to default.
