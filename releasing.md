# Procedure: cut a release (MFO, APMF, MEO)

A release is an immutable, tagged, MO2-installable zip whose DLL came from a
green CI run of exactly the tree being tagged. The scripts enforce most of
this. This page says how to drive them and where the three repos differ.

Build and CI verification are in
[native-dll-via-github-actions.md](native-dll-via-github-actions.md#procedure-compile-a-dll-and-prove-the-green-run-is-yours).

## Rules every repo shares

- **Immutable.** `releases/vX.Y.Z/` and tag `vX.Y.Z` are never overwritten or
  deleted. A bad build gets a new version, and the old one is recorded as
  withdrawn in the changelog.
- **The DLL comes from CI, never a local build.**
- **The changelog entry exists before the cut.** MFO and APMF refuse to run
  without a `## vX.Y.Z` heading in `CHANGELOG.md`.
- **The zip root is the virtual `Data/` folder**, so MO2 installs it with no
  manual placement.
- **The zip is gitignored (`*.zip`).** MFO and APMF commit the
  `MANIFEST.txt` in a follow-up commit named `release: vX.Y.Z MANIFEST` (MFO)
  or `release: vX.Y.Z manifest` (APMF). MEO writes no MANIFEST (see below).

## Changelog format

Newest first. Heading form `## vX.Y.Z -- Title` (double hyphen, not a dash).
Then `-` bullets written from the player's point of view. One block per
version, never grouped by theme. The GitHub release and Nexus text are pasted
from this block, so write it in the public voice (no em or en dashes, no
semicolons, short declarative sentences). MFO keeps a
`## vX.Y.Z -- Unreleased` block at the top while work lands. Rename it to the
real title before phase 2. APMF's script warns if an `## Unreleased` heading is
left above the new entry.

## MFO: two-phase `release.sh`

**Prerequisites:** clean tree on `main`, every change for this version merged
and reviewed, `gh` authenticated, the CHANGELOG block written.

```bash
# PHASE 1: stamp, commit, push (to origin main)
./release.sh 2.0.13
```
Phase 1 writes `VERSION`, the `project(MFO VERSION ...)` line in
`native/CMakeLists.txt`, and the MCM Debug-page `"value": "vX.Y.Z"` in
`out/MCM/Config/MFO/config.json`, commits `Bump version to X.Y.Z` and pushes
`main`. Because `native/` changed, CI rebuilds.

Wait for that run and check it is yours:
```bash
gh run list --workflow=native --commit "$(git rev-parse HEAD)" --json databaseId,status,conclusion
gh run watch <id> --exit-status
```

```bash
# PHASE 2: verify + package + tag (no argument)
./release.sh
```
Phase 2 does, in order:
1. refuses if `releases/vX.Y.Z` exists, the CHANGELOG entry is missing, or the
   tree is dirty,
2. checks the CMake stamp equals `VERSION`,
3. regenerates the ESP (`python3 MFO_GenerateESP.py out`) and runs
   `tools/audit_esp.py` + `tools/audit_mcm.py` (a FAIL stops it),
4. recompiles Papyrus with `tools/compile.sh all` if Proton's wine is present,
   otherwise ships the committed `out/Scripts/*.pex` with a WARN,
5. takes the newest successful `native` run and refuses unless that run's
   `native/` tree equals `HEAD:native`,
6. stages and zips `MFO-vX.Y.Z.zip`, writes `releases/vX.Y.Z/MANIFEST.txt`
   (commit, CI run id, DLL and ESP sha256, UTC build time) and creates the
   local tag.

Then publish:
```bash
git add releases/vX.Y.Z/MANIFEST.txt
git commit -m "release: vX.Y.Z MANIFEST"
git push origin main
git push origin vX.Y.Z
V=X.Y.Z
awk -v v="## v$V " 'index($0,v)==1{f=1;next} /^## v/{f=0} f' CHANGELOG.md > /tmp/notes-$V.md
gh release create "v$V" "releases/v$V/MFO-v$V.zip" \
   --title "MFO v$V -- <title from the changelog heading>" \
   --notes-file /tmp/notes-$V.md --verify-tag
```
Update `Docs/STATUS.md` in the same session (the script prints the reminder).

**Verify:** `gh release view vX.Y.Z` lists the zip. Unzip it and
`sha256sum SKSE/Plugins/MFO.dll` equals the `dll:` line in `MANIFEST.txt`.

**Common failures**

| Symptom | Cause | Fix |
|---|---|---|
| phase 1: `commit your work before bumping` | dirty tree (often `installer/MFO.Synthesis/bin,obj`, which are gitignored now) | commit or clean, rerun |
| phase 2: `VERSION says X but CMakeLists says Y` | phase 1 never ran or never pushed | run `./release.sh X.Y.Z` |
| phase 2: `native/ has changed since the last green CI run` | CI for the bump not finished, or failed | wait for green on the bump commit |
| "green" CI after phase 1 is really the previous version | phase 1 silently failed to stamp, so the newest green run is old | check `gh run view <id> --json displayTitle,headSha`: title must be `Bump version to X.Y.Z` and `headSha` must equal `HEAD` |
| `CHANGELOG.md has no '## vX.Y.Z' entry` | block still titled Unreleased, or missing | write it, commit, rerun |
| `MFO_Trade.pex missing` | Papyrus compile failed or never ran | see [papyrus-on-linux.md](papyrus-on-linux.md) |

## MFO Progression add-on (packaged by hand)

The add-on (`MFO_Progression.esl`) has its own version line and its own
release. `release.sh` does not build it. Any commit that touches the add-on's
files means an add-on bump, cut in the same session as the MFO release.

Check whether it moved since the last add-on package:
```bash
git log <last-addon-commit>..HEAD -- out/MFO_Progression.esl \
   out/MCM/Config/MFO_Progression out/Scripts/MFOP_MCM.pex out/SEQ/MFO_Progression.seq
```

Package it:
```bash
V=1.1.5
python3 MFO_GenerateESP.py out
python3 tools/audit_esp.py
python3 tools/audit_mcm.py out/MCM/Config/MFO_Progression/config.json
S=$(mktemp -d)
mkdir -p "$S/Scripts" "$S/MCM/Config/MFO_Progression" "$S/SEQ"
cp out/MFO_Progression.esl "$S/"
cp out/Scripts/MFOP_MCM.pex "$S/Scripts/"
cp out/MCM/Config/MFO_Progression/config.json out/MCM/Config/MFO_Progression/settings.ini "$S/MCM/Config/MFO_Progression/"
cp out/SEQ/MFO_Progression.seq "$S/SEQ/"
mkdir -p "releases/progression-addon-v$V"
(cd "$S" && zip -rq "$OLDPWD/releases/progression-addon-v$V/MFO-Progression-Addon-v$V.zip" .)
sha256sum out/MFO_Progression.esl "releases/progression-addon-v$V/"*.zip
```
Write `releases/progression-addon-vX.Y.Z/MANIFEST.txt` by hand (build date,
required MFO version, zip + ESL sha256, source commit, the version's notes and
the zip contents list). Commit it. Publish with a prefixed tag so it never
collides with an old mod tag:
```bash
gh release create "progression-addon-v$V" "releases/progression-addon-v$V/MFO-Progression-Addon-v$V.zip" \
   --target main --latest=false --title "MFO Progression Add-on v$V"
```
It can also be attached to the same-day MFO release as a second asset (v2.0.12
did this). `PROG_VERSION_STAMP` in the generator is a detection anchor and
still reads 1.0. It does not track the add-on version. The version lives in
the folder, zip name, MANIFEST and tag.

## APMF: two-phase, same shape, three differences

`./release.sh 0.9.8` then `./release.sh`, exactly like MFO, except:
- **No `VERSION` file.** The version is read back from
  `project(APMF VERSION ...)` in `native/CMakeLists.txt`.
- **Phase 1 pushes the current branch**, not a hard-coded `main`. It also fills
  the `MinReleaseForAbi` placeholder in `native/core/ClientAPI.cpp` with the
  new version when one is present.
- **The ESL is checked, not just regenerated.** Phase 2 runs
  `APMF_GenerateESL.py` into a temp dir and refuses unless it is byte-identical
  (`cmp`) to the committed `Data/APMF.esl`. Fix a mismatch with
  `python3 APMF_GenerateESL.py Data`, review, commit.

The zip lands at `releases/vX.Y.Z/APMF-vX.Y.Z.zip` and carries three files:
`SKSE/Plugins/APMF.dll`, `SKSE/Plugins/APMF.ini`, and `APMF.esl` at the root.
Its MANIFEST adds the zip sha256. Public release title form:
`Harbinger (APMF) vX.Y.Z -- Title`.

## MEO: one-phase `tools/release_native.sh`

```bash
tools/release_native.sh v1.0.18 "one-line description" [--run <ci-run-id>]
```
- One phase. You push the version bump yourself and wait for green first.
- Without `--run` it takes the newest successful `native` run on any branch.
  Pass `--run` with the id you verified.
- **Version gate:** it greps the downloaded DLL for the version string and
  refuses if absent, which catches a run that built a different version.
- It regenerates the ESP, compiles `MEO_MCM`, and asserts the staged tree
  holds no executable other than `SKSE/Plugins/MEO.dll` (the Synthesis patcher
  is the only install path).
- Output: `releases/<version>/MEO-<version>.zip` plus `VERSION` and
  `NOTES.txt` (the CI run id). There is no `MANIFEST.txt`.
- **It does not tag.** It prints the command. Tag it yourself, annotated:
  `git tag -a <version> -m "<description>" && git push origin <version>`.
  MEO tags can carry suffixes (`v1.0.7-beta7`).

`tools/release.sh` in MEO is the older Papyrus-only path. Use
`release_native.sh` for anything with a DLL.
