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
