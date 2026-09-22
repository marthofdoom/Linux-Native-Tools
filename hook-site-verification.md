# Pillar 4b — Proving a hook site is safe (Linux, no debugger)

A call-site thunk is only safe if the target address really holds the `call`
instruction you expect. On AE this is harder than it sounds because **the exe is
encrypted on disk**. This is the single biggest time-sink in native work; do it
in this order.

Reference: `../Requiem-modification/tools/verify_hook_site.py`,
`verify_hook_site_live.py`, and
`../Requiem-modification/docs/DEBUGGING.md` (Native hooks section).

## The core problem: Steam DRM

`SkyrimSE.exe` on disk is Steam-DRM encrypted (a `.bind` section). **Static byte
reads of the code sections return garbage.** A tool that reads the on-disk exe
will "prove" a mismatch that isn't real, or a match that isn't real. We burned
hours on a false MISMATCH before realizing this.

→ The exe is only decrypted **in memory, while running**. Verify there.

## Two tools, two jobs

### `verify_hook_site.py <AL-ID> <hex-offset> <expected-hex>`
Parses the local **Address Library** `.bin` (decode logic ported from
CommonLibSSE-NG's `REL::IDDatabase::unpack_file`) to map an ID → this build's
RVA. Useful for the **ID→address mapping** even though the on-disk bytes it then
reads are DRM garbage. Treat its byte comparison as **inconclusive** for code
sections.

### `verify_hook_site_live.py <AL-ID> <hex-insn-offset> <expected-hex>`
The authoritative check. Finds the running `SkyrimSE.exe` wine process
(`pgrep -f 'SkyrimSE\.exe'`), reads `/proc/<pid>/mem` at
`module_base + AL_RVA + offset`, and compares to your expected bytes —
**decrypted ground truth.** `MATCH` here = safe to ship. Requires the game to be
running. It reuses the ID→RVA decode from `verify_hook_site.py` by importing it.

Typical session:
```bash
# game running:
tools/verify_hook_site_live.py 34526 0x20B E8      # -> MATCH => ship it
```

## Address Library DB pitfalls

- **Multiple `versionlib-*.bin` can exist for one game version** (e.g.
  `-1-6-1170-0.bin` vs `-0-1.bin` for a different binary revision). The wrong one
  parses cleanly and yields **plausible-but-shifted** addresses — we lost half a
  day "proving" a function inlined that wasn't.
- **Validate the DB against crash-log ground truth first.** A crash line like
  `38785+0x16D => exe+0x6C4EFD` pins ID 38785 to `0x6C4D90`; confirm
  `load_database(...)[38785]` matches before trusting anything else.

## Finding an initial candidate site

- Start from a **maintained reference mod** that hooks the same pipeline
  (Valhalla Combat for combat damage, po3's extender for magic apply) and reuse
  its *published* address — the Address Library ID + offset — as your candidate
  site. That address is a fact about the game binary, not the mod's code: you
  still verify it live (below) and write your own thunk. Don't invent sites.
- To hunt a relocated site, dump live memory around the AL address and
  disassemble with **capstone** (`pip install --user --break-system-packages
  capstone`).

## Confirming the hook fires (runtime)

Add a temporary `spdlog::info` line dumping the values you care about; reproduce
in-game; read the plugin log. This "instrument, don't eyeball" approach resolved
MRO's absorb scaling definitively (logged `frac`/magnitude per hit instead of
squinting at the health bar). Strip the diagnostic before release.

If you can't find the log, see the log-directory gotcha in
[native-dll-via-github-actions.md](native-dll-via-github-actions.md).

## Machine-specific bits to parameterize
- `verify_hook_site.py` hardcodes the `versionlib-1-6-1170-0.bin` path and
  `.../Stock Game/SkyrimSE.exe`. Inject these per project/runtime.

---

## The vtable-hook guard pattern (five layers, shipped and field-proven)

Everything above is about **call-site** thunks, where the question is "does this
address hold the `call` I expect". A `write_vfunc` hook has a different failure
mode: the address resolves fine, but the object behind the vtable is not the
class you think, or the runtime is not the one your offsets came from. This is
the pattern that survived contact, in install order. Skipping any layer has cost
a crash at least once.

1. **Refuse runtimes you have not verified — first, before anything else.**
   `REL::Module::IsVR()` -> log a warning and return; VR shifts vtables. And if
   your offsets were only read off one runtime's disassembly, refuse the others
   too (`!REL::Module::IsAE()`), rather than guessing that a struct layout
   carries across. **Refusing to install and saying so beats installing and
   being wrong** — an "UNSUPPORTED" report is data; a wrong answer is a week.
2. **INI kill-switch, read once at install, default OFF for anything
   diagnostic.** One key per seat, so a single seat can be disabled in the field
   without a rebuild. (`GetPrivateProfileIntA` usually has to be declared by hand
   — a CommonLib PCH does not pull in `<Windows.h>`.)
3. **Install-time RTTI verification. Never a blind vtable write.** Two idioms:
   - no CommonLib class for the type: walk `vtableAddr - sizeof(void*)` ->
     `CompleteObjectLocator*` -> `typeDescriptor->mangled_name()` and `strcmp`
     against the literal you read out of the disassembly
     (e.g. `".?AVCombatMagicItemData@@"`);
   - a CommonLib `RTTI_X` symbol exists: walk the class hierarchy and confirm it
     actually **derives** the base you are typing it as.
   A mismatch must refuse the install outright. This layer is what catches a
   *vtable symbol with no class behind it* — a real, shipped hazard.
4. **Per-call vtable-identity re-test inside the thunk.** One thunk usually
   serves many concrete vtables, so store the originals in a map keyed by the
   **vtable address**, and recover with
   `*reinterpret_cast<uintptr_t*>(a_this)`. If the live vptr is not one you
   recorded at install, do not touch the object.
5. **Lookup-miss recovery = call the LIVE original. Never fabricate a return.**
   On a miss, read the current function pointer straight out of the vtable slot
   and call it, after logging an `error` (the path should be unreachable, so it
   should be loud when it is not). Returning an invented `false`/`nullptr`/`0.0f`
   turns a hook bug into a behaviour change you will chase for weeks.

Supporting rules that belong with the five:

- **`static_assert(offsetof(...))` every member a thunk reads.** And on
  1.6.1170 keep combat-thread `CombatController` reads **below 0x68**
  ([commonlibsse-ng-traps.md](commonlibsse-ng-traps.md) §2).
- **A field with no CommonLib class gets all three of** the INI switch, the
  install-time RTTI check and the per-call identity re-test — not one of them.
- **No allocation, no locks, no file I/O inside a thunk** on an engine thread.
  Push to a fixed-size lock-free queue and drain it on your main-thread pump.
- **Read fields, do not call vfuncs, from an observation thunk**
  ([commonlibsse-ng-traps.md](commonlibsse-ng-traps.md) §6).

## Passive observation probes: the rules that keep them passive

A probe exists to tell you **which stage of an engine pipeline stopped
considering your subject**, cheaply, so that expensive disassembly can be aimed.
It is not there to explain. Rules learned the expensive way:

- **No hotkeys, no toggles, no writes.** Config-gated (default OFF) and
  always-on-when-enabled. A probe that changes behaviour produces a result about
  the probe.
- **Always chain the original, always return exactly what it returned.**
- **Instrument the stage that runs BEFORE the decision, not the stage that
  consumes it.** A probe placed downstream of a rejection reports silence, and
  silence is ambiguous between "never enumerated" and "enumerated then dropped".
  Find the site the engine runs **once per candidate, before deciding** — log
  both the key it computed and the verdict it returned. This one choice was the
  difference between a two-cycle investigation and a one-run answer.
- **Emit a per-site invocation counter every session, including zero.** If a
  hooked site never fired, every conclusion drawn from its silence is void, and
  the log must say `UNPROVEN` rather than implying a negative. Five correct
  engine seats were once built, reviewed and shipped onto a code path the engine
  never runs; nothing in the output said so.
- **Dedup by STATE TRANSITION, not by a time throttle.** A time throttle
  re-prints a stable condition forever — in one measured 10-minute session a
  single unchanging "package stable" condition emitted 37 identical lines through
  a 1.5 s throttle, and one three-line diagnostic triplet accounted for 171 of
  634 lines. Keep a per-subject high-water mark and log only when it advances.
- **Scope to the actors you actually track.** A classifier or behaviour-tree
  site fires for every NPC in the cell; the same probe is a handful of lines for
  four tracked followers and thousands for a town.
- **Budget the volume explicitly and cap it.** A real session baseline was
  ~40-65 lines/minute per plugin; a probe that doubles that hides its own signal.
  Cap the session, self-disable at the cap, and print that you did.

## SteamStub-packed SkyrimSE.exe: unpack once, store forever; and the jmp-thunk trap (2026-09-21)

- Steam ships SkyrimSE.exe with SteamStub v3.1.2 (`.text` AES-encrypted, entropy 8.00). `.rdata` (vtables,
  RTTI, strings) is PLAINTEXT, so vtable/slot/id work never needed the unpack; instruction-level work does.
- `tools/steamstub-rtti/unpack_steamstub.py <packed> <out>`: reads the stub header at EP-0xF0 (XOR-rolled),
  signature 0xC0DEC0DE/0xC0DEC0DF, AES-256 key + ECB-decrypted IV, CBC-decrypts .text with the 16 stolen
  header bytes prepended, restores the OEP, keeps the file layout (raw = 0x400 + rva - 0x1000 for .text).
  Flags & 0x04 = NoEncryption -> the image is already plaintext (1.7.104 ships that way).
- Permanent unpacked copies: MFO repo `binaries/<ver>/SkyrimSE.unpacked.exe` (gitignored). Do not re-derive.
- **TRAP:** Address Library ids for many "impl" functions land on a 5-byte `E9 rel32` thunk padded with INT3,
  not the body. Read the first byte; if E9, body = rva + 5 + rel32. Example:
  IAnimationGraphManagerHolder::NotifyAnimationGraph AE 38048 0x6a35f0 -> 0x54c450; SE 37020 0x60f240 -> 0x4f12c0.
- Tail-jump vtable dispatch shows up as `48 8b 01 48 ff 60 NN` (`mov rax,[rcx]; jmp [rax+NN]`) = slot NN/8;
  seen at CombatAnimation::Execute (AE 0x7f9470 / SE 0x75ff10, slot 5 = TESActionData::Process).
- Full id map for the NPC attack-pick chain: MFO/APMF `Docs/ADDRESS-TABLE-2026-09-15.md` §5.
