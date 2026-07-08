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
      - name: Cache vcpkg binaries
        uses: actions/cache@v4
        with:
          path: ~\AppData\Local\vcpkg\archives
          key: vcpkg-${{ hashFiles('native/vcpkg.json','native/vcpkg-configuration.json') }}
          restore-keys: vcpkg-
      - name: Configure
        run: cmake --preset release
        env: { VCPKG_ROOT: C:\vcpkg }
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
- `paths:` filter means only `native/**` changes trigger a build — doc/script
  commits don't waste CI.

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
