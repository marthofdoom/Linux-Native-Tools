# Pillar 5 — Instance data & event-driven native mods (1.6.1170 / AE)

Everything below was **validated in-game** during MEO's M2–M4 arc (v0.5.0 →
v0.7.2) on a heavy load order (Lorerim). Reference implementation:
`../marth-enchanting-overhaul/native/plugin.cpp`. Canonical copy: `../marth-enchanting-overhaul/Docs/ENGINE_NOTES.md`
(this file is the one-time 2026-07-07 sync; MEO's repo stays authoritative).

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

## 2. Renaming an instance — the four traps

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

## 3. A functional instance enchantment (the SKSE recipe)

Attaching a base `ENCH` form via `ExtraEnchantment` shows the description on
the item card but applies **no effect**. The working flow (from SKSE64
`PapyrusWornObject.cpp`, the code behind `WornObject.CreateEnchantment`):

1. Build a player-style **created** enchantment from MGEF effect items:
   `RE::BGSCreatedObjectManager::GetSingleton()->AddWeaponEnchantment(
   BSTArray<RE::Effect>&)` (SKSE name:
   `PersistentFormManager::CreateOffensiveEnchantment`). Created forms get
   FF-prefix runtime FormIDs and are engine-persisted in the save.
2. Attach: `xList->Add(new RE::ExtraEnchantment(ench, charge, false))`.
3. If the item is currently equipped:
   `actor->UpdateWeaponAbility(baseForm, xList, leftHand)`
   (`RELOCATION_ID(37803, 38752)`) — activates the magic caster. Skipping
   it = description with no effect.

`RE::Effect` fields: `effectItem{magnitude, area, duration}`, `baseEffect`
(the `EffectSetting*`), `cost`. Real weapon enchants bring the engine charge
bar with them (charge/maxCharge + recharge UX); it is not repurposable.

## 4. Event sinks — hook-free triggers (all validated)

| Event | Shape | Notes |
|---|---|---|
| `RE::TESEquipEvent` | `{actor, baseObject, uniqueID, equipped}` | plain unenchanted items report `uniqueID=0` |
| `RE::TESSpellCastEvent` | `{NiPointer<TESObjectREFR> object; FormID spell}` | fires for lesser powers too → menu-less "cast a power" UX |
| `RE::TESDeathEvent` | `{actorDying, actorKiller, bool dead}` | fires twice; act on `dead == true` |
| `RE::TESCellAttachDetachEvent` | `{NiPointer<TESObjectREFR> reference; bool attached}` | **fires per reference**, not per cell — ideal for stamping world loot at load |
| `SKSE::CrosshairRefEvent` | `{NiPointer<TESObjectREFR> crosshairRef}` | via `SKSE::GetCrosshairRefEventSource()`; read-only ground-ref diagnostics |

Register TES events on `RE::ScriptEventSourceHolder`; defer all mutation to
`SKSE::GetTaskInterface()->AddTask` (main-thread).

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
deposited instances arrived as uid=0x1) and was scrapped same-day. If
instances must transit containers, re-key via TESContainerChangedEvent
(carries a uniqueID field) — unproven, verify which side's uid it reports.
