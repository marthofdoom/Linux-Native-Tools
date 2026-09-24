# Pillar 3 — Building a CommonLibSSE-NG SKSE DLL from Linux

CommonLibSSE-NG plugins need MSVC + vcpkg. Cross-compiling from Linux
(msvc-wine, clang-cl) is possible but fragile. The **proven** path is: keep the
source on Linux, let **GitHub Actions build it on a Windows runner**, download
the artifact, deploy. You never touch Windows; a `git push` is the build button.

Reference: `../Requiem-modification/.github/workflows/native.yml` and
`../Requiem-modification/native/`.

## Repo layout (everything under `native/`)

```
native/
  plugin.cpp                  # the plugin
  PCH.h                       # precompiled header (CommonLibSSE includes)
  CMakeLists.txt              # add_commonlibsse_plugin(...)
  CMakePresets.json           # Ninja + cl.exe + static-md triplet, C++23
  vcpkg.json                  # deps: commonlibsse-ng
  vcpkg-configuration.json    # pins vcpkg baseline + colorglass registry
```

### CMakeLists.txt (the whole thing)
```cmake
cmake_minimum_required(VERSION 3.21)
project(MyPlugin VERSION 0.1.0 LANGUAGES CXX)
find_package(CommonLibSSE CONFIG REQUIRED)
add_commonlibsse_plugin(${PROJECT_NAME} SOURCES plugin.cpp)
target_compile_features(${PROJECT_NAME} PRIVATE cxx_std_23)
target_precompile_headers(${PROJECT_NAME} PRIVATE PCH.h)
```

### vcpkg-configuration.json — the key to it building at all
CommonLibSSE-NG is **not in the default vcpkg registry**. Pull it from the
colorglass registry with pinned baselines (bump deliberately, never float):
```json
{
  "default-registry": { "kind": "git",
    "repository": "https://github.com/microsoft/vcpkg.git",
    "baseline": "d87340acc46bdeda386037b38aca30136e667e47" },
  "registries": [ { "kind": "git",
    "repository": "https://gitlab.com/colorglass/vcpkg-colorglass",
    "baseline": "6309841a1ce770409708a67a9ba5c26c537d2937",
    "packages": ["commonlibsse-ng"] } ]
}
```

### CMakePresets — triplet matters
`VCPKG_TARGET_TRIPLET: x64-windows-static-md`,
`CMAKE_MSVC_RUNTIME_LIBRARY: MultiThreaded$<$<CONFIG:Debug>:Debug>DLL`, Ninja
generator, `cl.exe`. Flags: `/permissive- /Zc:preprocessor /EHsc /MP /W4
-DWIN32_LEAN_AND_MEAN -DNOMINMAX -DUNICODE -D_UNICODE`.

## The workflow

```yaml
name: native
on:
  push: { paths: ["native/**", ".github/workflows/native.yml"] }
  workflow_dispatch:
jobs:
  build:
    runs-on: windows-latest
    defaults: { run: { working-directory: native } }
    steps:
      - uses: actions/checkout@v4
      - uses: ilammy/msvc-dev-cmd@v1
      # SPLIT restore/save -- see "the cache only saves on success" below.
      - name: Restore vcpkg cache
        id: vcpkg-cache
        uses: actions/cache/restore@v4
        with:
          path: ~\AppData\Local\vcpkg\archives
          key: vcpkg-${{ hashFiles('native/vcpkg.json','native/vcpkg-configuration.json') }}
          restore-keys: vcpkg-
      - name: Configure
        run: cmake --preset release
        env: { VCPKG_ROOT: C:\vcpkg }
      - name: Save vcpkg cache
        if: always() && steps.vcpkg-cache.outputs.cache-hit != 'true'
        uses: actions/cache/save@v4
        with:
          path: ~\AppData\Local\vcpkg\archives
          key: vcpkg-${{ hashFiles('native/vcpkg.json','native/vcpkg-configuration.json') }}
      - name: Build
        run: cmake --build build/release
      - uses: actions/upload-artifact@v4
        with:
          name: MyPlugin-dll
          path: |
            native/build/release/MyPlugin.dll
            native/build/release/MyPlugin.pdb
          if-no-files-found: error
```

Notes:
- `windows-latest` ships `C:\vcpkg` (`VCPKG_ROOT`). `msvc-dev-cmd` puts `cl.exe`
  on PATH.
- The **vcpkg archive cache** turns a ~15-min cold build into ~1.5–2.5 min once
  warm; key it on the manifest hashes.
- **`actions/cache` only saves in its post-step when the job SUCCEEDS**
  (verified MFO 2026-07-21: `gh cache list` was empty after three runs, every
  one of which had paid a ~30 min cold build). Every failed compile therefore
  throws the vcpkg build away. That is backwards for early development, which
  is mostly failed builds — **the cache matters MOST when the build is
  broken.** Split into `cache/restore` + `cache/save`, put the save
  immediately after `Configure` (the step that actually builds the
  dependencies) and before the compile that might fail, guarded by
  `if: always()`. First green build after the split: 2m24s.
- **Anything hashed into the cache key is a build-time cost.** `vcpkg.json`'s
  own `version-string` affects nothing at build time but IS in the key, so
  stamping a release version into it invalidates the cache every release.
  (`restore-keys: vcpkg-` prefix-matches an older entry and softens this — a
  key-churn build came back in 3m30s rather than 30 min — but don't design
  around the fallback.)
- Add a **`concurrency` group with `cancel-in-progress`**: without it, several
  ~30 min cold builds run in parallel racing to populate the same cache and
  none of them wins.
  ```yaml
  concurrency:
    group: native-${{ github.ref }}
    cancel-in-progress: true
  ```
- `paths:` filter means only `native/**` changes trigger a build — doc/script
  commits don't waste CI.
- **First-build gotcha: your PCH must supply what the GENERATED file needs.**
  `add_commonlibsse_plugin` generates `__<Project>Plugin.cpp` using `"..."sv`
  string-view literals and force-includes your PCH into it. Without
  `using namespace std::literals;` in the PCH the build dies with
  `error C3688: invalid literal suffix 'sv'` **in a file you never wrote**.
  MEO's and MAO's PCHs both end with that line; MFO copied the structure,
  missed the last line, and lost its first build to it (2026-07-21).
- **Release gating: compare the `native/` TREE, not the commit sha.** A
  release script that refuses unless the last green run built `HEAD` will
  block on any docs- or script-only commit and force a pointless rebuild.
  `git rev-parse HEAD:native` vs the run's native tree is exact.

## The Linux-side loop

```bash
git push                                   # triggers the build
gh run watch <id> --exit-status            # wait for green
gh run download <id> -n MyPlugin-dll -D /tmp/out
cp /tmp/out/MyPlugin.dll ".../mods/MyMod/SKSE/Plugins/"
```

Always verify the deployed copy: `sha256sum` the artifact vs the live file. Keep
a timestamped backup of the previous DLL before overwriting.

## Confirm it actually loaded in-game

`skse64.log` (in the game's SKSE log dir) prints `plugin MyPlugin.dll ... loaded
correctly`. Your own plugin's log confirms hook install lines. **Gotcha:** on
some setups `SKSE::log::log_directory()` resolves to an oddly-named `My
Games/<X>/SKSE/` folder (we saw `Skyrim.INI` instead of `Skyrim Special
Edition`). If your log is "missing", `find <proton-prefix> -iname MyPlugin.log`.
The prefix for a non-Steam shortcut is under
`~/.local/share/Steam/steamapps/compatdata/<appid>/pfx`.

## Alternative
`msvc-wine` can build locally without CI, but you own the toolchain setup and
breakage. CI is the low-maintenance default; use msvc-wine only if you need
offline iteration.

---

## Procedure: compile a DLL and prove the green run is yours

**When:** any change under `native/`. There is no local MSVC on our Linux
boxes. The Windows runner is the only compiler that ever sees the code.

**Prerequisites:** `gh` authenticated (`gh auth status`), a push remote, the
workflow file at `.github/workflows/native.yml`. MFO, APMF and MEO all use the
workflow name `native` and upload one artifact named `<Mod>-dll` holding
`<Mod>.dll` + `<Mod>.pdb`.

**Steps**

```bash
git push -u origin <branch>                 # the push IS the build button
SHA=$(git rev-parse HEAD)
gh run list --workflow=native --commit "$SHA" \
   --json databaseId,status,conclusion,headSha
gh run watch <id> --exit-status             # blocks until done, non-zero on failure
gh run view <id> --json headSha,status,conclusion,headBranch
gh run download <id> -n MFO-dll -D /tmp/mfo-dll   # MFO.dll + MFO.pdb
```

**Verify success.** All three must hold. Anything else is not green.
1. The run's `headSha` equals `git rev-parse HEAD` of the commit you mean to ship.
2. `status` is `completed`.
3. `conclusion` is `success`. `cancelled` means a newer push on the same ref
   superseded it (the workflow sets `cancel-in-progress`), so look for the
   newer run. `queued` / `in_progress` means keep waiting.

`gh run view <id> --exit-status` returns 0 for a run that is still pending.
Use `gh run watch` to wait, and read `conclusion` to decide.

**The `paths:` filter.** The workflow only fires for `native/**` and the
workflow file itself. A docs-only commit has no run at all. Two ways to handle
a tip with no run:
- Prove the tree is identical to a green commit, then use that run:
  `git diff --quiet <green-sha> HEAD -- native/ && echo IDENTICAL`.
  The release scripts compare `git rev-parse HEAD:native` against the run's
  `native` tree for exactly this reason.
- Or force a build of the tip: `gh workflow run native --ref <branch>` (the
  workflow has `workflow_dispatch`). Then find the new run with
  `gh run list --workflow=native --branch <branch> --limit 1`.

**The PDB.** Always keep the `.pdb` from the SAME run as the DLL you deployed.
It is the only way to turn a crash offset into a source line (see
[deploy-and-logs.md](deploy-and-logs.md#symbolize-a-crash-frame)). A PDB from
any other run gives wrong lines, silently. MFO and APMF add `/Zi` and
`/DEBUG /OPT:REF /OPT:ICF` in `native/CMakeLists.txt` so a Release build writes
the PDB. MEO's CMakeLists does not, so its artifact may carry no PDB (its
release script copies one only "if CI produced one"). An early MFO build had
the same gap and its first crash could not be symbolized.

**Common failures**

| Symptom | Cause | Fix |
|---|---|---|
| `gh run list --commit` is empty | commit touched nothing under `native/` | tree-compare to a green commit, or `gh workflow run native --ref <branch>` |
| green run, wrong code | you read the newest run on ANY branch | filter by `--commit "$SHA"` and check `headSha` |
| run `cancelled` | a newer push on the same ref | wait for the newer run |
| compile error | read it with `gh run view <id> --log-failed` | fix, push again |
| `error C3688: invalid literal suffix 'sv'` in a file you never wrote | PCH lacks `using namespace std::literals;` | see the first-build gotcha above |
| unresolved or unknown CommonLib symbol | the symbol does not exist in the pinned 3.7.0 | verify it (next section) before writing it |
| cold build takes ~30 min | vcpkg cache miss (manifest hash changed) | expected once. Never stamp versions into `vcpkg.json` |

Repo differences: MFO and APMF split the cache into `cache/restore` +
`cache/save` and set a `concurrency` group. MEO still uses the combined
`actions/cache@v4` with no `concurrency` group, and restricts `push` to
`branches: ['**']` so a tag push does not build.

---

## Procedure: where CommonLibSSE-NG comes from, and verifying a symbol

**How the build gets it.** `native/vcpkg.json` lists `commonlibsse-ng` with no
version. `native/vcpkg-configuration.json` routes that one package to the
colorglass git registry at a pinned `baseline`. The baseline decides the
version. MFO, APMF and MEO all pin the same two baselines:

| Registry | Baseline |
|---|---|
| default, `github.com/microsoft/vcpkg` | `d87340acc46bdeda386037b38aca30136e667e47` |
| `gitlab.com/colorglass/vcpkg-colorglass` (package `commonlibsse-ng`) | `6309841a1ce770409708a67a9ba5c26c537d2937` |

At that colorglass baseline the port is `version-semver 3.7.0` and fetches
`CharmedBaryon/CommonLibSSE` at `REF c4ab853d095e81e3390b282d7ba01ab2f24ebf25`.
Check it yourself:

```bash
B=6309841a1ce770409708a67a9ba5c26c537d2937
curl -s "https://gitlab.com/colorglass/vcpkg-colorglass/-/raw/$B/ports/commonlibsse-ng/portfile.cmake" | grep -E 'REPO|REF'
curl -s "https://gitlab.com/colorglass/vcpkg-colorglass/-/raw/$B/ports/commonlibsse-ng/vcpkg.json" | grep version
```

**Get a local copy of exactly that source** (once):

```bash
git clone https://github.com/CharmedBaryon/CommonLibSSE-NG pinned-3.7.0
git -C pinned-3.7.0 checkout c4ab853d095e81e3390b282d7ba01ab2f24ebf25
```

On the dev box it already lives at
`/mnt/gaming/modlists/Projects/_commonlib/pinned-3.7.0-c4ab853d/`. A second
checkout there, `live-alandtse-ng/` (alandtse fork, v7.x), is reference only.
It has bindings 3.7.0 lacks, so verifying against it produces code that fails
CI.

**Verify a symbol before you write it.**

```bash
P=/path/to/pinned-3.7.0
grep -rn "GetGoldAmount" "$P/include" "$P/src"     # declared? defined?
```

- Not in the pinned tree means it does not exist for our build. Do not use it.
- Present in the header is still not proof of the ABI. For any vfunc, offset
  or struct layout, check the disassembly of the real binary. See
  [hook-site-verification.md](hook-site-verification.md) and
  [commonlibsse-ng-traps.md](commonlibsse-ng-traps.md) §6 (the hidden `sret`
  slot).
- Read the implementation in `src/`, not only the declaration. Several 3.7.0
  helpers crash by design (commonlibsse-ng-traps.md lists them).

**Changing a baseline** is a deliberate, reviewed change of its own. Never
float it. Any edit to either vcpkg file changes the cache key and costs one
cold build.

**Forward note (in progress, not final).** An MIT-licensed fork of CommonLib
3.7 served from our own vcpkg registry is being introduced (stage F0, on an
MFO branch at the time of writing). Until it merges, `main`'s
`native/vcpkg-configuration.json` is authoritative. Read that file, not this
note, to learn what a given build compiled against.
