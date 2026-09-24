# Procedure: deploy a build to a test machine, then read its logs and crashes

The game runs under Proton (Steam or umu) with MO2's virtual file system. The
test machine is often a Steam Deck reached over ssh. These steps get a CI-built
DLL onto it, prove it landed, and read what it did.

---

## Deploy

**When:** after a green CI run you have verified is yours
([native-dll-via-github-actions.md](native-dll-via-github-actions.md#procedure-compile-a-dll-and-prove-the-green-run-is-yours)),
or after a release.

**Prerequisites:** the artifact (`gh run download <id> -n <Mod>-dll -D <dir>`)
or the release zip, the MO2 instance path on the target, and ssh access if the
target is remote.

### Where files go in MO2

```
<instance>/mods/<ModFolder>/SKSE/Plugins/<Mod>.dll   # the DLL
<instance>/mods/<ModFolder>/SKSE/Plugins/<Mod>.ini   # the plugin INI
<instance>/mods/<ModFolder>/<Mod>.esp                # plugin (zip root = Data/)
<instance>/mods/<ModFolder>/meta.ini                 # MO2 metadata, never in the zip
<instance>/overwrite/                                # runtime writes land here
```

### Steps (DLL-only dev deploy)

```bash
MOD="<instance>/mods/MFO/SKSE/Plugins"
cp "$MOD/MFO.dll" "$MOD/MFO.dll.bak-$(date +%Y%m%d-%H%M%S)"   # keep the previous build
cp /tmp/mfo-dll/MFO.dll "$MOD/"
sha256sum /tmp/mfo-dll/MFO.dll "$MOD/MFO.dll"                  # must match
```

Copy only the DLL for a code-only change. That leaves the local `MFO.ini`,
the MCM settings and `meta.ini` untouched. Deploy the ESP (or add-on ESL) too
when the change touched it.

### Steps (full release zip)

The zip carries a default `SKSE/Plugins/<Mod>.ini`. Unzipping over the mod
folder replaces the tester's INI. Exclude it to keep local settings:

```bash
unzip -o MFO-vX.Y.Z.zip -d "<instance>/mods/MFO" -x 'SKSE/Plugins/MFO.ini'
```

Drop the `-x` only when the release changed INI keys and the tester wants the
new defaults. MCM Helper's live user store is the copy in
`<instance>/overwrite/MCM/Settings/<Mod>.ini`, which shadows the zip's seed
copy, so unzipping does not reset MCM values.

### Steam Deck (or any remote box) over ssh

```bash
HOST=deck@<deck-host>
DEST='/home/deck/Games/<instance>/mods/MFO/SKSE/Plugins'
scp /tmp/mfo-dll/MFO.dll "$HOST:/tmp/MFO.dll"
ssh "$HOST" "cp '$DEST/MFO.dll' '$DEST/MFO.dll.bak-\$(date +%s)'; cp /tmp/MFO.dll '$DEST/'"
ssh "$HOST" "sha256sum '$DEST/MFO.dll'"; sha256sum /tmp/mfo-dll/MFO.dll
```

- **Paths with spaces** (MO2 folder names often have them) break plain `scp`
  targets. Quote the remote path inside the ssh command, or stream it:
  `cat file | ssh "$HOST" 'cat > "/path/with space/file"'`.
- **ssh times out:** a sleeping Deck. Wake it and retry. It is not a deploy
  failure.
- **Credentials:** use an ssh key in your agent. Never put a password, token or
  API key in a script, a doc or a commit.
- **Paired DLLs** (MFO + APMF) that were built against each other deploy
  together in one pass. A half-updated pair tests nothing.

### Instances synced by syncthing

If the MO2 instance is shared with the test box by syncthing, write the files
on the source side only. Do not also scp them. syncthing can miss the write
and report both sides "idle" while they hold different DLLs. After the copy,
force a rescan of just that subpath through syncthing's local REST API and
poll the remote hash until it matches:

```bash
CFG=~/.local/state/syncthing/config.xml
KEY=$(grep -oPm1 '(?<=<apikey>)[^<]+' "$CFG")        # read locally, never paste it anywhere
curl -sk -X POST -H "X-API-Key: $KEY" \
  "https://127.0.0.1:8384/rest/db/scan?folder=<folder-id>&sub=mods/MFO"
ssh "$HOST" "sha256sum '<remote-instance>/mods/MFO/SKSE/Plugins/MFO.dll'"
```

Check which instance the tester actually plays before deploying. The freshest
plugin log mtime tells you (`stat -c %y <instance>/overwrite/SKSE/Plugins/MFO.log`).

### Verify the build is the one running

1. The target's DLL sha256 equals the artifact's (or the `dll:` line in the
   release `MANIFEST.txt`).
2. The tester **quits the game executable** and relaunches. SKSE loads DLLs
   only at process start. Loading a save keeps the old DLL.
3. The plugin log's first lines show the new build. A dev deploy that did not
   bump the version prints the old version string, so the sha is the proof, not
   the banner.
4. MO2 has two checkboxes: the mod (left pane) and the plugin (right pane).
   `profiles/<P>/plugins.txt` needs the `*` prefix on the ESP line, or it is
   not loaded.

---

## Read logs

| Log | Where | Notes |
|---|---|---|
| `skse64.log` | `<prefix>/drive_c/users/steamuser/Documents/My Games/Skyrim Special Edition/SKSE/` | shows `plugin <Mod>.dll ... loaded correctly` |
| MFO.log, APMF.log | `<instance>/overwrite/SKSE/Plugins/` | they open `Data/SKSE/Plugins/<Mod>.log`, which MO2 redirects into overwrite |
| MEO.log | the prefix `SKSE/` folder above | MEO uses `SKSE::log::log_directory()` |
| `MCMHelper.log` | the prefix `SKSE/` folder | why an MCM did or did not register |
| CrashLogger | the prefix `SKSE/` folder, `crash-YYYY-MM-DD-HH-MM-SS.log` | one file per crash |

`<prefix>` is `~/.local/share/Steam/steamapps/compatdata/<appid>/pfx` for a
Steam or non-Steam-shortcut launch, or `~/Games/umu/<appid>` for a umu launch.
Several prefixes can exist and some are empty. Find the live one instead of
guessing:

```bash
find ~ -iname 'skse64.log' -printf '%T@ %p\n' 2>/dev/null | sort -rn | head -3
find ~ -iname 'crash-*.log' -printf '%T@ %p\n' 2>/dev/null | sort -rn | head -5
```

Some setups write under `My Games/Skyrim.INI/` instead of
`My Games/Skyrim Special Edition/`. The `find` catches that. Background on the
two log destinations: [instance-data-and-events.md](instance-data-and-events.md) §22.

**Plugin logs truncate on every launch.** Pull the log before the tester
relaunches, or the session is gone.

---

## Symbolize a crash frame

**When:** a CrashLogger log shows your DLL in the stack, for example
`[ 0][P] 0x6FFFE2840EC4        MFO.dll+0050EC4`.

**Prerequisites:** `llvm-symbolizer` (LLVM 19 verified), and the DLL **and PDB
from the same CI run** that was deployed. The release `MANIFEST.txt` records the
run id (`ci run:`).

```bash
gh run download <ci-run> -n MFO-dll -D /tmp/sym     # MFO.dll + MFO.pdb side by side
llvm-objdump -p /tmp/sym/MFO.dll | grep ImageBase    # 0000000180000000
llvm-symbolizer --obj=/tmp/sym/MFO.dll 0x180050EC4   # base + RVA
llvm-symbolizer --obj=/tmp/sym/MFO.dll --relative-address 0x50EC4   # same thing
```

The number after `MFO.dll+` is the RVA in hex. Add the preferred base
`0x180000000`, or pass it as is with `--relative-address`. Output is the
function and `file:line` (the file path is the CI runner's `D:\a\...`, so read
the line number against the same commit). Worked check on MFO main `72787d4`:
`SKSEPlugin_Load` exports at RVA `0x1d5c0`, and
`llvm-symbolizer --obj=MFO.dll 0x18001d5f0` prints
`SKSEPlugin_Load` / `plugin.cpp:495`.

**Failures**

| Symptom | Cause | Fix |
|---|---|---|
| `error: ... 'D:\a\...\MFO.pdb': No such file or directory` | no PDB beside the DLL | put `MFO.pdb` in the same directory as `MFO.dll` |
| plausible but wrong function | DLL/PDB from a different run than the crashed build | download the run named in the MANIFEST, or match the deployed DLL's sha256 |
| `??:0:0` | address outside any function (wrong base, or not your DLL) | recheck the arithmetic, use `--relative-address` |

**Engine frames** (`SkyrimSE.exe+089F4AE`) are already annotated by
CrashLogger with an Address Library id and offset (`-> 49090+0xEE`). To go from
an RVA to an id yourself, see
[hook-site-verification.md](hook-site-verification.md#procedure-checklist-verify-an-address-vfunc-or-hook-site).

Your DLL absent from the stack does not clear it. A lifetime bug can hand a
bad pointer to the engine and crash there later.
