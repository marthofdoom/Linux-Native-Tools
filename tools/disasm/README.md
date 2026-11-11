# sdis.py: quick annotated disassembly of an unpacked SkyrimSE image

`python3 sdis.py id:<AE id> [count]` or `python3 sdis.py <rva hex> [count]` prints capstone x64 disassembly with:
- call / jmp targets named by Address Library id,
- rip-relative loads annotated with the target's id, the GameSetting NAME when the target is a `Setting` value
  (the name pointer sits 8 bytes after the value), and the float image default.

Paths to the exe and the versionlib bin are at the top of the file (1.6.1170 by default). It caches the id table in
`ids.pkl` beside itself. Needs `pip install capstone pefile`.

Do NOT name a script `dis.py` next to it: capstone imports the stdlib `dis` module and a local `dis.py` shadows it
(a "partially initialized module capstone" error).

Used for the 2026-11-11 CalculateDetection read (`../../stealth-detection.md`).
