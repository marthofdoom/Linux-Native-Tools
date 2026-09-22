# steamstub-rtti — tooling from the 2026-09-21 APMF attack-selection design pass

- `unpack_steamstub.py` — SteamStub v3.1.2 unpacker (signature 0xC0DEC0DF confirmed on SkyrimSE 1.6.1170 and 1.5.97). NOT yet run: the agent's auto-mode refused it; run by hand.
- `addrlib.py` — Address Library decoder for AE (format 2) and SE (format 1); validated on ids 38894/37938.
- `rtti.py` / `img.py` — RTTI -> vtable walker over the PACKED image's plaintext .rdata (vtables/RTTI/strings are readable without unpacking; .text is not).
- `scar_ae_1.6.1170.asm` — SCAR.dll (AE support) disassembly used to locate its StartAttack/PerformAttackAction rewrites and the VTABLE_Character[2] slot-1 combo hook.

Design that produced these: ai-package-management-framework `Docs/SPEC-ATTACK-SELECTION-FACET.md`.
