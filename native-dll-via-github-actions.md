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
