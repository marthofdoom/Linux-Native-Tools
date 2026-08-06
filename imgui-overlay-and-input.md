# Pillar 8 — In-process ImGui overlay + input (native UI beyond the message box)

Draw a full ImGui UI inside the game's own DX11 present — no external overlay,
no UI framework — for menus richer than the native message box
([instance-data-and-events.md](instance-data-and-events.md) §5). Validated in
MEO v0.12–v0.13 on Linux/Proton. Reference:
`../marth-enchanting-overhaul/native/` and `Docs/ENGINE_NOTES.md` §9.
Cross-verify the hook shapes against **D7ry/wheeler**.

---

## 1. The three trampoline hook sites (install at plugin load)

Install all three in `SKSEPluginLoad`, **before the renderer exists**, after
`SKSE::AllocTrampoline(64)`:

| Hook | Site | Signature / job |
|---|---|---|
| **D3DInit** | `RELOCATION_ID(75595, 77226)` at `VariantOffset(0x9, 0x275, 0x0)` | grab device/context/swapchain; init ImGui backends |
| **DXGIPresent** | `RELOCATION_ID(75461, 77246)` at `Offset(0x9)` | draw — **runs on the RENDER thread** |
| **InputDispatch** | `RELOCATION_ID(67315, 68617)` at `Offset(0x7B)` | translate + swallow input |

InputDispatch signature:
`void(RE::BSTEventSource<RE::InputEvent*>*, RE::InputEvent**)`.

Renderer access on NG 3.7:
`RE::BSGraphics::Renderer::GetSingleton()->data.{forwarder, context,
renderWindows[0].swapChain}`; hwnd from `swapChain->GetDesc().OutputWindow`.
Swap WndProc via `SetWindowLongPtrA`; clear ImGui keys on `WM_KILLFOCUS`.

**Swallow input while the menu is open:** after copying events into ImGui IO,
set `*a_events = nullptr` so the game sees no input (no vanilla menu
bleed-through).

**Threading:** the present hook is on the render thread — read a
**mutex-guarded snapshot** only; defer all engine mutation to
`GetTaskInterface()->AddTask` (and see the worker-thread caveat in
[instance-data-and-events.md](instance-data-and-events.md) §26).
`io.IniFilename = nullptr` — never write `imgui.ini` into the game dir.

vcpkg: `imgui` with features `dx11-binding`, `win32-binding`; link
`imgui::imgui d3d11`.

## 2. `io.DisplaySize` lies under Proton/upscalers (Linux-specific)

`ImGui_ImplWin32_NewFrame()` sets `io.DisplaySize` from `GetClientRect`, which
under Proton + an upscaler (DLSS / FSR / Community Shaders) can differ from the
actual backbuffer — a "centered" window renders visibly off-center. The layout
math is correct; the reported viewport is wrong, so it breaks **only on the
target platform**.

**Fix:** cache `sd.BufferDesc.{Width,Height}` from the swapchain desc at init and
overwrite `io.DisplaySize` every frame **between** `ImGui_ImplWin32_NewFrame()`
and `ImGui::NewFrame()`. Never trust `io.DisplaySize` on Linux; drive it from the
DXGI backbuffer.

## 3. Action rows must fire single-shot via `IsItemActivated()`

`IsItemClicked` reports the PRESS frame; a `Selectable` return reports the
RELEASE frame — OR-ing them is **two fires per physical click**. A `busy`-disable
only masks the echo if a disabled frame is actually *drawn* (ImGui clears
ActiveId in `ItemHoverable` on a disabled draw), but the SKSE task pump usually
beats the next present, so no disabled frame renders and the release re-fires
against the rebuilt snapshot — a render-thread/task-pump timing race (one click
socketed two units of a 0-XP stack). An epoch/idempotence check can't catch it —
the echo is stale in *intent*, not data.

**Fix:** use `ImGui::IsItemActivated()` for action rows; also claim `busy` via an
`exchange` so two rows activated in one frame can't both queue.

## 4. Gamepad input mapping

- **Analog triggers arrive as `RE::ButtonEvent`s** with synthetic IDs
  `RE::BSWin32GamepadDevice::Key::kLeftTrigger` (0x0009) / `kRightTrigger`
  (0x000A) (header comments them "arbitrary values … meant to be used with
  ButtonEvent"). `value` is the analog magnitude, so standard `IsDown()`/`IsUp()`
  edge logic works. Map to `ImGuiKey_GamepadL2`/`R2`.
- **Face/dpad/shoulder codes are XInput bitmasks** (`kA=0x1000`,
  `kRightShoulder=0x200`, `kUp=0x1` …), but each ButtonEvent carries **exactly
  one** code — use an exact-match `switch`, **not** a bit test.
