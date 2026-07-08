# Pillar 2 — Compiling Papyrus (.psc → .pex) on Linux

The Papyrus compiler is a .NET/Mono Windows exe. It runs fine under wine — but
**only wine that ships Mono**. System wine usually lacks it and fails with mono
errors. Proton's bundled wine has Mono.

Reference: `../Requiem-modification/tools/compile.sh`.

## The recipe

Run `PapyrusCompiler.exe` (bundled with Nemesis, or the CK tools) via Proton's
wine, pointing `WINEDATADIR` at Proton's wine share so Mono resolves:

```bash
PROTON="/mnt/gaming/Steam/steamapps/common/Proton Hotfix/files/bin/wine"
MONO_DATA="/mnt/gaming/Steam/steamapps/common/Proton Hotfix/files/share/wine"
PAPYRUS=".../Nemesis_Engine/Papyrus Compiler/PapyrusCompiler.exe"
FLAGS=".../Papyrus Compiler/scripts/TESV_Papyrus_Flags.flg"

WINEDATADIR="$MONO_DATA" "$PROTON" "$PAPYRUS" "Source/Scripts/Foo.psc" \
    -f="$FLAGS" -i="$IMPORTS" -o="out/Scripts"
```

Success is the line `1 succeeded, 0 failed` in stdout. `compile.sh` greps for it
and copies the `.pex` into the package trees on success.

## Import path (`-i`, semicolon-separated)

Include your own `Source/Scripts` plus the source for every framework you call:

- SKSE64 `Scripts/Source` (**required** — without it you get bogus
  "cannot relatively compare variables to None" errors from a stripped
  `Actor.psc` shadowing SKSE's)
- Custom Skills Framework, po3's Papyrus Extender, PapyrusUtil — whichever you
  import
- the compiler's own `scripts` folder (vanilla script sources)

## Traps

| Symptom | Cause | Fix |
|---|---|---|
| mono errors, compiler won't start | system wine has no Mono | use Proton's wine + `WINEDATADIR` (above) |
| "unknown type X" for a vanilla/SKSE class | that `.psc` isn't in the import path | add its source dir, or drop a one-line stub `Scriptname X extends Form Hidden` |
| error line numbers don't match the file | multibyte UTF-8 in the source | **ASCII only, everywhere** |
| `â€¢`-style garbage in in-game text | non-ASCII in user-facing strings | ASCII only |

## .pex is never hot-swapped (deployment gotcha)

A running game session keeps whatever `.pex` was loaded when the save was made;
installing a new `.pex` mid-session does nothing until a genuine cold load.
SkyUI additionally caches an MCM's `ModName` + `Pages` array from
`OnConfigInit` (runs once per save). To fully apply MCM script changes: cold
restart, then `setstage SKI_ConfigManagerInstance 1` to re-register title/tabs.
Rendered rows regenerate per page-open, so they update on reload alone.

Prove which pex a session runs by grepping the installed file:
`grep -a 'NewString' installed/Foo.pex` — pex keeps its string table in plain
text (this is also what makes the MCM-pex-editor stub feasible).
