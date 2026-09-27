# Which other Sunshine capture method could keep a virtual monitor *and* input isolation?

Research question: setting `wlr` aside, if `display/` were re-architected around a different Sunshine
capture method, which one can still deliver both (a) a virtual monitor the game renders to and (b) input
isolation, i.e. the host user keeps working undisturbed while stream input goes only to the game?

Short answer: **`x11` is the only other method that can do both with production-grade tooling** — and it
is precedented by Sunshine's own headless-X11 guide. The price is a full Xorg-based re-architecture with
vendor-specific virtual outputs, no HDR path, worse performance, and weaker gamepad isolation. `kms`,
`kwin`, `portal`, and `nvfbc` each fail at least one of the two requirements without heroic scaffolding.

Note: current Sunshine master has two Linux backends newer than the released-docs snapshot used in
`docs/display-capture-methods.md` — `kwin` (direct `zkde_screencast_unstable_v1`) and `portal`
(xdg-desktop-portal RemoteDesktop + ScreenCast → PipeWire), both present as `kwingrab.cpp`,
`portalgrab.cpp`/`pipewire.cpp` in `src/platform/linux/`. They are evaluated below.

## 1. The two requirements, and how the current stack meets them

- **Virtual monitor.** `display/scripts/sway_start.sh` runs Sway with `WLR_BACKENDS=headless,libinput`;
  `display/scripts/sway_config` declares `output HEADLESS-1`. The output exists only inside the headless
  wlroots compositor — no hardware, no kernel connector, no X screen.
- **Input isolation.** Sunshine's Linux input is kernel-level: it creates virtual keyboard/mouse/gamepad
  devices via `/dev/uinput` (see Sunshine's `src_assets/linux/misc/60-sunshine.rules`, which grants
  `TAG+="uaccess"` on `uinput`; the getting-started docs require input-group/uaccess setup for "virtual
  input devices"; this repo's `display/input_isolation.py` installs its own udev rule matching Sunshine's
  vendor/product IDs for the same purpose). Because uinput events are system-wide, isolation is enforced
  one layer up, per compositor, on *both* sides:
  - guest: `sway_config` runs `input * events disabled`, then re-enables only the Sunshine passthrough
    devices (`48879:57005:*`);
  - host (Plasma): `display/scripts/kwin_input_isolation.py.template` disables those same devices at
    runtime over KWin D-Bus (`org.kde.KWin.InputDevice`).

Any replacement architecture must reproduce both halves: a fake output for capture, and per-session
device filtering over system-wide uinput.

## 2. Verdict table

| Method | Virtual monitor? | Input isolation? | Verdict |
|--------|-----------------|------------------|---------|
| `x11` | Yes — Xvfb (software), Xephyr (nested window), Xdummy/`xf86-video-dummy`, or NVIDIA TwinView virtual screen | Yes for kb/mouse via per-X-server `xinput disable` on the host server; gamepad leaks possible (apps read evdev directly) | **Viable — the only viable alternative.** Precedented, but a full re-architecture with real costs (§3) |
| `kms` (+ `vkms`/`evdi`) | Partially — `vkms` creates kernel connectors, but software-only; `evdi` needs its client driver | No input layer at all (KMS is display-only); isolation would require logind multiseat | No — fails isolation without multiseat; `vkms` also can't run games on GPU (§4) |
| `kwin` | Only by replacing Sway with a full KWin session; KWin has no declarative `output HEADLESS-1` equivalent | Inherits the same uinput problem with weaker per-device tooling than Sway's config | No — heaviest rewrite, least benefit (§5) |
| `portal` | No — captures the *host* compositor's monitors via its portal backend | No — RemoteDesktop injects into the host session; needs an on-screen approval click per the troubleshooting docs | No — architecturally host-bound (§6) |
| `nvfbc` | No (X11 physical displays only) | N/A | No (§7) |

## 3. `x11`: viable, precedented, expensive

**Capture side works.** `src/platform/linux/x11grab.cpp` opens the server named by the environment —
`xdisplay {x11::OpenDisplay(nullptr)}` (`x11_attr_t` constructor), i.e. `$DISPLAY` — captures
`DefaultRootWindow(xdisplay.get())`, and selects a monitor via XRandR by output name with numeric-index
fallback (`init`, ~lines 497–544; same name-or-index convention as `wlgrab`). A managed Sunshine started
with `DISPLAY=:1` and `output_name=<nested output>` therefore captures exactly the nested session, the
same way today's stack captures `HEADLESS-1`.

**Virtual-monitor side works, with options.** X11's virtual-display tooling is mature and first-party:
- `Xvfb` — "an X server that can run on machines with no display hardware and no physical input devices.
  It emulates a dumb framebuffer using virtual memory" (`Xvfb(1)` man page). Fully headless, but
  software-only: unsuitable for GPU games without further work.
- `Xephyr` — "a kdrive server that outputs to a window on a pre-existing host X display"
  (`Xephyr(1)` man page, x.org). Nested and real, but the "virtual monitor" is a window on the host
  desktop — visible, focusable, and sharing the host server's GL story — so separation is weaker than a
  truly headless session.
- `Xdummy` (`xf86-video-dummy`) / vendor virtual outputs — headless X with GPU offload, at the cost of
  per-vendor `xorg.conf` work. This is exactly Sunshine's own documented headless recipe: the
  "Remote SSH Headless Setup" guide (`docs/.../guides/linux/headless_ssh.html`, v0.23.0) starts an X
  server on a virtual display and runs Sunshine in it — but it is explicit that its TwinView virtual
  display is "only available for NVidia GPUs using Xorg" (`ConnectedMonitor`/`MetaModes`/`TwinView`
  `xorg.conf`). Unlike Sway's generic `headless` backend (any GPU via `WLR_DRM_DEVICES`), X11 has no
  vendor-neutral headless path.

**Input-isolation side is replicable for keyboard/mouse.** X input devices are per-server: `xinput`
operates on the devices of the server it connects to (the Arch `xinput(1)` man page notes device
properties affect only clients of that same server process). The uinput devices Sunshine creates appear
in both `:0` (host) and `:1` (nested) device lists, so the analog of today's design is `xinput disable`
(or `Device Enabled 0`) for the Sunshine devices on the host server while leaving them enabled on the
nested one. Caveats:
- Gamepads mostly bypass X: host-side Steam/games reading evdev/js nodes would still see the virtual
  controller. Today's Plasma host script has the same class of problem and handles KWin-level devices;
  on X there is no equivalent filter for direct evdev readers — mitigating it needs device hiding
  (e.g. `joymap`/evdev-grab tooling), which is extra machinery with its own edge cases.
- Relative-mouse capture inside a nested Xephyr window needs that window focused; focus fights with the
  host desktop are possible in a way the isolated Sway session never has.

**What else breaks.** The whole `display/` script layer assumes Sway: `swaymsg` mode switching
(`set_resolution.sh`, `apply_exact_refresh.sh`, `reset_resolution.sh`) becomes `xrandr`; GPU selection
via `WLR_DRM_DEVICES`/`WLR_RENDER_DRM_DEVICE` becomes `xorg.conf` `Device` sections; the GLES2-vs-Vulkan
renderer toggle (`WLR_RENDERER`) has no X equivalent; HDR goes away with the Wayland session; and per
Sunshine's own docs `x11` "is the slowest and most CPU intensive" capture. So: viable, but a
downgrade on performance, features, and hardware generality.

## 4. `kms` (+ `vkms`/`evdi`): display half is test tooling, input half doesn't exist

`kmsgrab.cpp` captures kernel DRM connectors/CRTCs under a `CAP_SYS_ADMIN` worker (established in
`docs/display-capture-methods.md`). A virtual connector is obtainable — `vkms` is documented in the
kernel docs (`docs.kernel.org/gpu/vkms.html`, "Useful for testing and for running X (or similar) on
headless machines", configurable via configfs with connectors/CRTCs/planes) — but:
- `vkms` is "a software-only model of a KMS driver" whose stated purpose is DRM API testing, not
  running GPU workloads. Games would render in software (`llvmpipe`) — a non-starter for streaming.
- KMS is a display API only; it has no input. Sunshine's uinput devices would be consumed by whatever
  session owns the physical hardware (the host), so isolation needs a second logind seat (multiseat:
  separate device assignment per seat) — root-level session plumbing far beyond a systemd user unit.
- `evdi` (DisplayLink) gives hardware-usable virtual displays but needs its kernel module plus a
  display client driving it, and still leaves the input problem unsolved.
- Ironic footnote: the natural client to drive a `vkms` connector is a DRM compositor — e.g. Sway with
  `WLR_DRM_DEVICES` pointed at it — at which point `wlr` capture already sees the content and KMS adds
  nothing.

## 5. `kwin`: a heavier compositor swap for no isolation gain

`kwingrab.cpp`'s header states the chain plainly: "KWin → Wayland `kde_screencast` → PipeWire →
Sunshine", bypassing xdg-desktop-portal, with Sunshine connecting directly to KWin's Wayland socket and
selecting the output by `wl_output` name (`start(output_name)`, `get_output_names()`). Permission is
granted via a `.desktop` file carrying `X-KDE-Wayland-Interfaces=zkde_screencast_unstable_v1` (or
`KWIN_WAYLAND_NO_PERMISSION_CHECKS=1`). Consequences:
- The managed session's compositor would have to *be* KWin (nested `kwin_wayland`), replacing the ~10-line
  `sway_config` model with a full KDE compositor stack and its own output tooling (kscreen/D-Bus instead
  of `swaymsg`); KWin offers no declarative headless-virtual-output equivalent of `output HEADLESS-1`.
- Input is unchanged (system-wide uinput) while KWin's per-device input control is poorer than Sway's
  `input * events disabled` model — the filtering story gets worse, not better.

## 6. `portal`: host-bound by design

`portalgrab.cpp` talks to `org.freedesktop.portal.Desktop` (`RemoteDesktop` + `ScreenCast`) on the
session bus and requests `SOURCE_TYPE_MONITOR` streams delivered over PipeWire (`pipewire.cpp`), with
persistent approval via restore tokens (`restore_token_t`, `PERSIST_UNTIL_REVOKED`). Two disqualifiers:
- The portal backend belongs to the *host* compositor, so `SelectSources` offers the host's monitors —
  there is no virtual monitor in the picture unless the host compositor itself provides one.
- Sunshine's troubleshooting docs state the operational blocker outright: "Portal capture requires you
  to manually approve Remote Desktop permissions via an on-screen prompt on the host." A
  service-managed headless stack has nobody to click, and every approval grants capture *of the host
  session*, with RemoteDesktop input landing in the host session — the exact opposite of isolation.

## 7. `nvfbc`: no

Per Sunshine's configuration reference, NvFBC "does not have native Wayland support and does not work
with XWayland", is NVIDIA-only, and captures physical X displays. No virtual monitor, no applicability
to a headless session.

## 8. Bottom line for an architecture change

If `wlr` ever had to go, the migration target is **`x11` against a dedicated nested/headless X server**,
with per-server `xinput` filtering as the input-isolation mechanism — i.e. the spiritual predecessor of
the current design, and the approach in Sunshine's own headless guide. Budget for: vendor-specific
virtual-output setup (or software/nested fallbacks), an `xrandr`-based rewrite of the resolution
scripts, loss of HDR and the renderer toggle, the documented performance cost of XCB/SHM capture, and a
residual gamepad-visibility problem on the host. Everything else (`kms`, `kwin`, `portal`, `nvfbc`)
sacrifices the virtual monitor, the isolation, or both — which is also why the current Sway + `wlr`
design is the right default to keep.

## Sources

- Sunshine capture/output/input references:
  `https://github.com/LizardByte/Sunshine/blob/master/docs/configuration.md`
  (`### capture`, `### output_name`); getting-started "Virtual Input Devices" (input-group/uaccess);
  troubleshooting "Portal capture requires you to manually approve Remote Desktop permissions via an
  on-screen prompt on the host"
- `x11` backend (`$DISPLAY` capture, root window, XRandR name/index matching):
  `https://github.com/LizardByte/Sunshine/blob/master/src/platform/linux/x11grab.cpp`
- KMS backend (DRM connectors/CRTCs, `CAP_SYS_ADMIN` worker):
  `https://github.com/LizardByte/Sunshine/blob/master/src/platform/linux/kmsgrab.cpp`
- KWin backend (direct `zkde_screencast_unstable_v1`, `.desktop` permission mechanism, output-name start):
  `src/platform/linux/kwingrab.cpp` (master tree)
- Portal backend (`org.freedesktop.portal.Desktop`, `SOURCE_TYPE_MONITOR`, restore tokens, PipeWire):
  `src/platform/linux/portalgrab.cpp` + `src/platform/linux/pipewire.cpp` (master tree)
- Linux virtual-input permission model:
  `https://github.com/LizardByte/Sunshine/blob/master/src_assets/linux/misc/60-sunshine.rules`;
  PR `LizardByte/Sunshine#1127` (`TAG+="uaccess"` on `/dev/uinput`)
- Sunshine's own headless-X11 precedent (Xorg virtual display via NVIDIA TwinView, `startx` + Sunshine):
  `https://docs.lizardbyte.dev/projects/sunshine/v0.23.0/about/guides/linux/headless_ssh.html`
- X11 virtual-display primitives: `Xvfb(1)` ("no display hardware and no physical input devices… dumb
  framebuffer using virtual memory"); `Xephyr(1)` ("outputs to a window on a pre-existing host X
  display", x.org); `xinput(1)` (per-server device enable/disable semantics)
- `vkms` scope ("software-only model of a KMS driver… useful for testing"):
  `https://docs.kernel.org/gpu/vkms.html`
- This repo's current isolation contract: `display/scripts/sway_start.sh`,
  `display/scripts/sway_config` (`input * events disabled` + passthrough enables),
  `display/scripts/kwin_input_isolation.py.template` (KWin D-Bus runtime disable),
  `display/input_isolation.py` (udev rule, device detection)
