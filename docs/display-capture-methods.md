# Why `display/` only works with the `wlr` capture method — and what supporting other methods would take

Research question: why does the virtual display in `display/` only work with Sunshine's `wlr` capture
method, and can it be made to work with the others (`kms`, `x11`, `nvfbc`, `kwin`)?

Short answer: **the `wlr`-only behavior is structural, not a misconfiguration.** `display/` builds an
isolated *headless Sway session* (a wlroots Wayland compositor with no X server and no kernel DRM
connector), and of all of Sunshine's Linux capture backends, only `wlr` knows how to read a wlroots
Wayland output. The other backends capture at layers where `HEADLESS-1` simply does not exist. Making
another method work means replacing Sway with a different virtual-display technology — a re-architecture,
not a setting.

## 1. What `display/` actually builds

All claims in this section are grounded in files in this repo:

- `display/scripts/sway_start.sh` launches Sway with `WLR_BACKENDS=headless,libinput` and
  `LIBSEAT_BACKEND=noop`, after explicitly `unset`-ing `DISPLAY`, `WAYLAND_DISPLAY`, and the desktop-session
  variables. It detects the new compositor instance by diffing `$XDG_RUNTIME_DIR/wayland-*` sockets and records
  the socket name for everything downstream.
- `display/scripts/sway_config` (the managed Sway config) defines exactly one output:
  `output HEADLESS-1 resolution …`. There is no physical connector behind it — it exists only inside the
  headless Sway compositor.
- `display/scripts/sunshine_wrapper.sh` starts the headless Sway session *first*, waits for the Wayland
  socket, then starts Sunshine; `display/scripts/sunshine_start.sh` exports that socket as `WAYLAND_DISPLAY`
  (plus `SWAYSOCK`, `XDG_SESSION_TYPE=wayland`, …) into Sunshine's environment. So the managed Sunshine
  process is a Wayland client *of the headless Sway instance*.
- `display/diagnostics.py` (`~line 362`) health-checks the stack with
  `swaymsg -t get_outputs` and looks specifically for an output named `HEADLESS-1`.
- Nothing in the repo pins Sunshine's `capture` or `output_name` settings (a search for
  `capture|output_name` across `display/`, `sunshine/`, and the script templates finds only audio-sink
  "capture" wording and test helpers). The stack therefore depends on Sunshine *auto-selecting* `wlr` —
  which it does whenever the headless Sway session correctly advertises the wlroots capture protocols
  (see §2), but which a user-level `capture = kms`/`x11` override would silently break.

## 2. Why `wlr` is the only backend that can see `HEADLESS-1`

Sunshine's own configuration reference (`docs/configuration.md` in `LizardByte/Sunshine`, `### capture`
section) defines the Linux capture choices and their scope:

- `wlr` — "Capture for wlroots based Wayland compositors via wlr-screencopy-unstable-v1. It is possible
  to capture virtual displays in e.g. Hyprland using this method." (Linux only.)
- `kms` — "DRM/KMS screen capture from the kernel. This requires that Sunshine has `cap_sys_admin`
  capability." (Linux only.)
- `x11` — "Uses XCB. This is the slowest and most CPU intensive so should be avoided if possible."
  (FreeBSD/Linux.)
- `nvfbc` — "Use NVIDIA Frame Buffer Capture… NvFBC does not have native Wayland support and does not
  work with XWayland." (Linux only.)
- `kwin` — "Capture with KDE/KWin Wayland compositor via KDE screencasting." (Linux only.)
- Default is "Automatic. Sunshine will use the first capture method available in the order of the table
  above" (table order: `nvfbc`, `wlr`, `kms`, `kwin`, `x11`).

The `wlr` backend (`src/platform/linux/wlgrab.cpp`) connects to the compositor over the `WAYLAND_DISPLAY`
it inherits and *fails initialization unless the compositor advertises two globals*:

```cpp
if (!interface[wl::interface_t::XDG_OUTPUT]) {
  BOOST_LOG(error) << "[wlgrab] Missing Wayland wire for xdg_output"sv;
  return -1;
}
if (!interface[wl::interface_t::WLR_EXPORT_DMABUF]) {
  BOOST_LOG(error) << "[wlgrab] Missing Wayland wire for wlr-export-dmabuf"sv;
  return -1;
}
```

(`wlgrab.cpp`, `wlr_t::init`, ~lines 82–89; the VRAM path repeats the same checks at ~lines 544–551.)
It then enumerates `interface.monitors` via `xdg_output` and selects the target by stable output name
first (`m->name == display_name`, ~line 107, e.g. `HEADLESS-1`), falling back to a numeric index for
back-compatibility (~line 114). The required globals are declared in `src/platform/linux/wayland.h`
(`interface_t::interface_e`: `XDG_OUTPUT`, `WLR_EXPORT_DMABUF`, `LINUX_DMABUF`).

Both capture protocols are wlroots inventions, shipped in the wlroots repository itself
(`protocol/wlr-screencopy-unstable-v1.xml`, `protocol/wlr-export-dmabuf-unstable-v1.xml` — both resolve
at `raw.githubusercontent.com/swaywm/wlroots/master/protocol/…`). Sway is a wlroots-based compositor
(its `README.md`: "sway is an i3-compatible Wayland compositor", built against wlroots; wlroots'
`README.md` lists the `headless` backend among its display backends). So the chain is:

> headless Sway advertises `wlr-export-dmabuf` + `xdg_output` → Sunshine's `wlr` backend passes its
> init checks → `output_name = HEADLESS-1` resolves to the virtual output → frames flow via DMA-BUF
> screencopy.

Sunshine's `output_name` documentation confirms the intended usage: "It is recommended to use the stable
display connector name … for this value. For wlgrab/x11grab and kmsgrab the numeric id value can also
be used." A virtual output like `HEADLESS-1` is exactly the stable-name case (numeric ids shift under
hotplug — the motivation for upstream PR `LizardByte/Sunshine#5071`, which added xdg-output-name
matching to `wlgrab`).

## 3. Why each other method cannot capture this stack

- **`kms`** reads pixels from the *kernel*: `src/platform/linux/kmsgrab.cpp` opens DRM card nodes under a
  thread that temporarily owns `CAP_SYS_ADMIN` (`class cap_sys_admin`, ~lines 47–63), enumerates physical
  connectors into `connector_t { type, crtc_id, index, connector_id, connected }` (~lines 369–377), and
  walks CRTCs/planes (`drmModeGetCrtc`, ~line 665; `plane->crtc_id` correlation, ~lines 1119–1191).
  A Sway `headless` output has no DRM connector, no CRTC, and no framebuffer in the kernel — it is
  composited in userspace inside Sway. KMS therefore captures the *physical* GPU scanout (the host's real
  monitors), never the isolated session. It is also the wrong isolation boundary: a KMS capture would
  leak the host desktop into the stream instead of the game's private display.
- **`x11`** talks to an X server: `src/platform/linux/x11grab.cpp` loads `XOpenDisplay` (~line 164) and
  `xcb_connect` (~line 194) and captures via XCB/SHM. `sway_start.sh` deliberately unsets `DISPLAY` and
  runs a pure-Wayland session (no XWayland output is configured for `HEADLESS-1`), so there is no X
  screen to connect to. Upstream additionally documents `x11` as the slowest, most CPU-intensive option.
- **`nvfbc`** captures NVIDIA GPU memory on X11/XWayland systems and, per Sunshine's own docs, "does not
  have native Wayland support and does not work with XWayland" — doubly inapplicable to a headless
  wlroots session, and vendor-locked besides.
- **`kwin`** speaks KDE/KWin's screencasting integration only. Our compositor is Sway, not KWin, so the
  backend's protocol peer never exists.

There is also no fallback inside Sunshine for non-wlroots Wayland compositors: the documented Linux
capture set is exactly `{nvfbc, wlr, kms, kwin, x11}` — there is no `xdg-desktop-portal` / PipeWire
backend, which is why headless sessions on GNOME/Mutter can't be captured this way either.

## 4. Could we support another method? Per-method verdicts

| Method | What it would take | Verdict |
|--------|-------------------|---------|
| `kms` | A *kernel-visible* virtual display: e.g. `vkms` module, `evdi`, or an IDD-style virtual connector, with games rendering onto that DRM device instead of Sway's `HEADLESS-1`. | Re-architecture. Loses `swaymsg` mode switching, `WLR_DRM_DEVICES`/`WLR_RENDER_DRM_DEVICE` GPU selection, and the Vulkan-vs-GLES2 renderer toggle as currently implemented; needs root/module loading and `CAP_SYS_ADMIN` handling. Upstream Sunshine has no Linux virtual-display creation (unlike Windows IDD), so we'd own that whole layer. |
| `x11` | Replace Sway with `Xvfb`/`Xephyr`, rewrite `set_resolution.sh`/`apply_exact_refresh.sh` around `xrandr`, redo input isolation for X, accept the documented slowest/most-CPU-intensive path. | Re-architecture with a performance downgrade. Only sensible if Wayland itself had to go. |
| `kwin` | Run the virtual session under KWin instead of Sway and re-implement output control/input isolation against KWin tooling. | Re-architecture; trades a lightweight headless compositor for a full KDE session component. |
| `nvfbc` | Impossible without leaving Wayland: no native Wayland support per Sunshine docs, NVIDIA-only. | No. |
| Portal/PipeWire (GNOME etc.) | Sunshine has no such Linux backend, so even switching compositors wouldn't help without upstream work. | Blocked upstream. |

In short: `wlr`-only is a consequence of choosing Sway (a choice this project shares with its acknowledged
inspiration, `daaaaan/sunshine-headless-sway`, per `README.md`) — wlroots is currently the only Linux
compositor family that both offers a headless backend *and* exposes a capture protocol Sunshine speaks.
Changing capture methods means changing compositors/display technologies, not changing a config value.

## 5. Recommended hardening (no architecture change)

The risk in the current tree is not that `wlr` fails but that nothing *guarantees* it: Sunshine's
`capture` defaults to automatic and `output_name` defaults to the default display. A user-level
`capture = kms` in `sunshine.conf` would silently stream the host desktop instead of erroring. Cheap,
high-value follow-ups:

1. Pin `capture = wlr` and `output_name = HEADLESS-1` from the managed stack (managed override or
   first-run `sunshine.conf` reconciliation), so auto-selection can never pick KMS/X11 for the virtual
   session.
2. Fail fast in `setup_display`/`start_display` (and/or the doctor report in `display/diagnostics.py`)
   when the effective Sunshine config forces a non-`wlr` capture method, with a message naming the
   offending key.
3. Keep the `HEADLESS-1`-by-name convention (never numeric id) in any `output_name` pinning, per the
   upstream stability guidance.

## Sources

Each claim above traces to the primary source that owns it:

- Sunshine capture methods, defaults, and per-method notes:
  `https://github.com/LizardByte/Sunshine/blob/master/docs/configuration.md` (`### capture`, `### output_name`)
- `wlr` backend init requirements and output matching:
  `https://github.com/LizardByte/Sunshine/blob/master/src/platform/linux/wlgrab.cpp`
  (`wlr_t::init`, `XDG_OUTPUT`/`WLR_EXPORT_DMABUF` checks, name-then-index matching)
- Required Wayland globals declaration:
  `https://github.com/LizardByte/Sunshine/blob/master/src/platform/linux/wayland.h` (`interface_t::interface_e`)
- KMS backend (DRM connectors/CRTCs, `CAP_SYS_ADMIN` worker):
  `https://github.com/LizardByte/Sunshine/blob/master/src/platform/linux/kmsgrab.cpp`
- X11 backend (XCB/`XOpenDisplay` capture):
  `https://github.com/LizardByte/Sunshine/blob/master/src/platform/linux/x11grab.cpp`
- Stable-name output matching rationale:
  `https://github.com/LizardByte/Sunshine/pull/5071` (`feat(linux/wlgrab): match output_name by xdg_output name`)
- User-forced capture method (auto-selection order context):
  `https://github.com/LizardByte/Sunshine/pull/1063`
- Sway is a wlroots-based Wayland compositor:
  `https://github.com/swaywm/sway/blob/master/README.md`
- wlroots headless backend + capture protocols originating in wlroots:
  `https://github.com/swaywm/wlroots` (`README.md`;
  `protocol/wlr-screencopy-unstable-v1.xml`; `protocol/wlr-export-dmabuf-unstable-v1.xml`)
- This repo's virtual-display construction:
  `display/scripts/sway_start.sh`, `display/scripts/sway_config`,
  `display/scripts/sunshine_wrapper.sh`, `display/scripts/sunshine_start.sh`,
  `display/scripts_render.py`, `display/diagnostics.py`, `display/sunshine_service.py`
