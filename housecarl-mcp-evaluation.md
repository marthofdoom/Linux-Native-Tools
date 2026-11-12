# houseCARL (MCP server for SSE / MO2): evaluation 2026-11-11

Verdict: DON'T for now; revisit when upstream issue #1256 (Linux support) ships.

- Source: https://github.com/Avick3110/houseCARL (GPL-3.0-only, C# .NET 10, Mutagen.Bethesda.Skyrim 0.54.4, solo author, very active). Nexus SSE 181738. Run-only use is fine for MIT projects; never copy its code.
- Linux: refuses to start off Windows (`src/housecarl-mcp/SupportedPlatform.cs`, "path handling ... unsound"). Under Wine is untested. It reads the MO2 instance statically (ModOrganizer.ini, profile modlist/plugins/loadorder), MO2 need not run. Our LoreRim/Tuxborn instances are Windows MO2 under Wine (Z:\ paths).
- Read-only: no global read-only switch. Writes go to `<ModsDir>\houseCARL - <name>` by default; `in_place=` overwrites with no backup; `decompile_script` writes .psc to a folder; large read results spill to a `results` dir beside `houseCARL.user.json`. Enforce read-only externally (ro bind mount or snapshot) plus an MCP permission allow-list.
- Network: only the keyless Nexus GraphQL bridge (`nexus_*` tools); deny them, since queries could leak the mod list. Runs external exes only for the Papyrus compiler and BSArch (pack/extract), paths user-set via `set_tool_path`; deny those tools.
- Fit: strong at load-order winners (GMST included) and override chains (`records`, `plugins` tree/chain, `conflicts_only`), asset/SKSE/SkyPatcher winners. Gap: no call-site search across Papyrus (decompile to a folder, then grep yourself).
- Read-only tool subset if ever trialled: records, check, asset_status, bsa_list, skse, skypatcher_layer, load_order_status, set_mo2_instance (+ decompile_script to a scratch out_path).
- Cheaper alternative for our questions: a small read-only Mutagen (.NET, runs on Linux) script for GMST/record winners, plus a BSA reader + pex decompiler for script call sites.
