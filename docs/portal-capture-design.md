# Portal capture for the virtual display: a working headless design

Research question: can Sunshine's **portal** capture backend (`portalgrab.cpp` → xdg-desktop-portal
ScreenCast → PipeWire) replace `wlr` for `display/` while keeping the virtual monitor and the
stream/host input separation?

Short answer: **yes — by running a private portal stack *inside* the headless session.** The reason
portal "doesn't work" today is not the protocol, it's bus topology: our managed Sunshine talks to the
host's session bus, so portal requests land on the host compositor (host monitors, host approval
dialog). Give the headless Sway session its own D-Bus session bus, run `xdg-desktop-portal` +
`xdg-desktop-portal-wlr` on it with a non-interactive output config, pin `capture = portal`, and the
same `HEADLESS-1` output becomes capturable through the portal — with input isolation unchanged. The
key enabler is documented in portal-wlr's own man page: `chooser_type=none` serves the configured
output **"without further interaction."**

## 1. Why portal fails today (bus topology, not protocol)

- Sunshine's portal backend connects to `G_BUS_TYPE_SESSION` (`portalgrab.cpp`, `dbus_t` setup) — i.e.
  whatever `DBUS_SESSION_BUS_ADDRESS` is in its environment. Today `sunshine_start.sh` propagates the
  **host** session bus, so `CreateSession`/`SelectSources` go to the host's `xdg-desktop-portal`,
  which fronts the host compositor: host monitors, and (per Sunshine's troubleshooting docs) "Portal
  capture requires you to manually approve Remote Desktop permissions via an on-screen prompt on the
  host." Both failure modes from `docs/other-capture-methods.md` §6 follow from this one fact.
- Nothing about the portal *protocol* is host-specific. ScreenCast is `CreateSession → SelectSources →
  Start → OpenPipeWireRemote` against `org.freedesktop.portal.Desktop`, delivering PipeWire streams
  with position/size/source-type metadata (official `org.freedesktop.portal.ScreenCast` docs, interface
  version 6). Point the session bus at a portal stack that fronts the *nested* Sway session, and the
  same calls yield `HEADLESS-1`.

## 2. The design: a nested portal stack on a private bus

```
host session bus (untouched)          private headless bus (new, managed)
────────────────────────────          ────────────────────────────────────
host compositor + host portal         sway (HEADLESS-1, WAYLAND_DISPLAY=nested)
                                      xdg-desktop-portal -r
                                      xdg-desktop-portal-wlr (output HEADLESS-1, chooser none)
                                      sunshine (capture=portal, output_name=HEADLESS-1)
                                      games (Wayland clients of nested sway)
shared: user PipeWire daemon (via inherited XDG_RUNTIME_DIR)
system-wide: Sunshine uinput devices (isolation filtering unchanged, §6)
```

**Why the bus must be private.** D-Bus activation and backend lookup are per-bus: the portal daemon
selects backends from `*-portals.conf` for the requestor's `XDG_CURRENT_DESKTOP` and activates
`org.freedesktop.impl.portal.desktop.wlr` on *its own* bus. Sharing the host bus would need a second
wlr backend instance fronting the nested compositor on the host bus — conflicting bus names, wrong
activation environment, and leakage into the host portal. A private bus (one `dbus-daemon --session`
or `dbus-run-session` wrapping the existing `sunshine_wrapper.sh` supervision tree, address published
in a file next to the Wayland-display file) gives full session isolation with the lifecycle already
handled by the wrapper's `cleanup` trap.

## 3. Components, each grounded in its primary source

### 3.1 `xdg-desktop-portal-wlr` as the backend

- Scope (its `README.md`): "backend for wlroots" implementing **only** `Screenshot` and `ScreenCast`
  — no RemoteDesktop. That is sufficient: Sunshine's portal video path uses ScreenCast only
  (`SOURCE_TYPE_MONITOR`, `SelectSources` with `types=MONITOR`, `OpenPipeWireRemote`).
- Headless operation (its man page, `xdg-desktop-portal-wlr.5.scd`): under `[screencast]`,
  `chooser_type=none` means "xdpw will allow screencast either on the output given by **output_name**,
  or if empty an arbitrary output **without further interaction**." Managed config:
  `output_name=HEADLESS-1`, `chooser_type=none`. No slurp/wofi/rofi needed in the headless session.
- Same wire protocols as today: its `protocols/` ships `wlr-screencopy-unstable-v1.xml` and
  `src/screencast/` implements `wlr_screencopy` (+ `ext_image_copy`) → DMA-BUF → PipeWire. Portal here
  is a *transport upgrade*, not a new capture mechanism — Sway needs nothing new.
- Useful knobs for our existing features: `max_fps` (could follow the display-sync/double-refresh
  state instead of a static value), `force_mod_linear` (multi-GPU compat — pairs with the repo's
  `WLR_DRM_DEVICES` GPU selection), `exec_before`/`exec_after` hooks. Config resolution order is
  documented: `$XDG_CONFIG_HOME/xdg-desktop-portal-wlr/$XDG_CURRENT_DESKTOP`, then `.../config`, then
  `/etc/xdg/…` — ship the managed file at the `XDG_CONFIG_HOME` path (or the `sway`-named one, since
  our session sets `XDG_CURRENT_DESKTOP=sway`).
- Dependencies (`meson.build`): `libpipewire-0.3 ≥ 0.3.64`, `wayland-client`, `wayland-protocols ≥
  1.24`, `inih`, `gbm`, `libdrm`, sd-bus provider — all standard distro packages; they join the
  `missing_dependencies` check alongside `sway`.

### 3.2 Portal routing + activation files (ship verbatim patterns from upstream)

- `sway-portals.conf` → managed `$XDG_CONFIG_HOME/xdg-desktop-portal/sway-portals.conf`, using the
  repo's own `contrib/wlroots-portals.conf` pattern (`ScreenCast`/`Screenshot` → `wlr`; portal-wlr's
  README documents exactly this file and notes `XDG_CURRENT_DESKTOP` must be set — ours is `sway`).
- Backend identity: `wlr.portal` (`DBusName=org.freedesktop.impl.portal.desktop.wlr`,
  `Interfaces=…Screenshot;…ScreenCast;`, `UseIn=…;sway;…`) and the systemd-style unit
  (`contrib/systemd/xdg-desktop-portal-wlr.service.in`: `Type=dbus`,
  `BusName=org.freedesktop.impl.portal.desktop.wlr`, `ConditionEnvironment=WAYLAND_DISPLAY`).
  In the headless session, either let the portal daemon D-Bus-activate the backend from installed
  service files, or start both daemons explicitly from the wrapper for deterministic ordering
  (portal first, then backend, then Sunshine — mirroring the existing sway-before-sunshine wait loop).
- portal-wlr's README also requires `WAYLAND_DISPLAY`/`XDG_CURRENT_DESKTOP` in the D-Bus activation
  environment — already true in our managed env (`sunshine_start.sh` exports both); the new daemons
  just need to inherit it plus the private-bus address.

### 3.3 PipeWire sharing

portal-wlr publishes the screencast as a PipeWire node on the **user** PipeWire daemon; Sunshine
connects via the `OpenPipeWireRemote` fd (`pipewire.cpp`). Our wrapper already inherits
`XDG_RUNTIME_DIR`, so `pipewire-0` is visible to the nested session without forwarding. Precondition
to check at setup: a user PipeWire (or pipewire+wireplumber) daemon is running — true on effectively
all modern desktops, but it becomes a hard dependency rather than an audio-only one. Sunshine's
`pipewire.cpp` already handles both node-ID and `pipewire-serial`/`PW_KEY_TARGET_OBJECT` targeting
(ScreenCast v6 deprecation), so stream re-identification across mode switches is covered.

### 3.4 Sunshine side: pin `capture = portal`

- `src/platform/linux/misc.cpp` accepts `config::video.capture == "portal"` (verified via
  `verify_portal()` = non-empty `portal_display_names()`), and `display()` tries backends in order
  KMS → NvFBC → WAYLAND → X11 → PORTAL → KWIN. In auto mode `wlr` still wins in our env, so the
  managed `sunshine.conf` reconciliation must pin `capture = portal` explicitly (this supersedes the
  `capture = wlr` recommendation in `docs/display-capture-methods.md` §5 if the portal design is
  adopted). `output_name = HEADLESS-1` still applies — `portalgrab` matches streams by monitor name
  (`match_display_name`/`to_display_name`) and logs each discovered stream at startup.
- Build precondition: Sunshine must be built with `SUNSHINE_BUILD_PORTAL` (`cmake/…/linux.cmake`
  gates on `libpipewire-0.3`). The doctor/setup flow should detect this (look for "Screencasting
  with XDG portal" in a probe run, or document the distro/Flatpak matrix) before promising portal mode.

### 3.5 Approval bootstrap: one prompt at most, then headless forever

- Sunshine requests `persist_mode = PERSIST_UNTIL_REVOKED` and persists the returned restore token to
  disk (`portalgrab.cpp`: `restore_token_t::load/set`, save on receipt). Per the portal spec,
  `persist_mode=2` returns a single-use restore token from `Start()`; each restore consumes the token
  and yields the next one — Sunshine already implements this rotation.
- Why first run should also be prompt-free here: with `chooser_type=none` the backend performs no
  selection UI ("without further interaction"), and the main daemon's permission store grants silently
  on first use for ScreenCast; the prompt users know from desktops is the *chooser/permission dialog*,
  which this configuration eliminates. Residual risk: portal versions whose permission storefront behaves
  differently, or distros gating ScreenCast behind their own policy — hence the fallback: perform the
  first stream once with a monitor attached (or via the existing host session), let the token persist,
  and all later runs restore headlessly. The restore path also pins the stream to the same monitor id,
  which is exactly our fixed `HEADLESS-1`.
- Verification is cheap (see §8 prototype): if `Start()` returns streams with no UI on a fresh
  permission store, the design is fully non-interactive.

### 3.6 Input isolation: unchanged, by construction

Two facts combine favorably:
- portal-wlr implements no RemoteDesktop portal (README scope), and Sunshine's `portalgrab` contains
  no portal/EIS input path (no `EIS`/`NotifyPointer`/libei usage anywhere in the file) — keyboard,
  mouse, and gamepads stay on the current `/dev/uinput` (`inputtino`) path.
- Therefore the entire existing isolation contract transfers verbatim: udev rule for the Sunshine
  vendor/product IDs, guest-side `input * events disabled` + passthrough enables in `sway_config`,
  host-side runtime disable via the KWin D-Bus script on Plasma. Portal changes *pixels*, not *input*.

## 4. What portal actually buys (honest ledger)

For: standardized, versioned capture API instead of the unstable `wlr-export-dmabuf`/`wlr-screencopy`
protocols; restore-token persistence across compositor restarts; server-side `max_fps` wired to
display-sync state; `exec_before/after` hooks; cleaner Flatpak story (a sandboxed Sunshine needs only
the private-bus socket forwarded, not `WAYLAND_DISPLAY`+`SWAYSOCK`+runtime-dir — or, with the bus
forwarded, the same files serve both); PipeWire DMA-BUF zero-copy path identical to today's.
Against: three always-on processes (private bus, portal, portal-wlr) in the supervision tree; new
package deps and version floors (portal with ScreenCast v4+ restore semantics, `libpipewire-0.3 ≥
0.3.64` per portal-wlr's `meson.build`); portal-wlr's maintenance pace (single-maintainer project —
  pin or vendor expectations accordingly); a wider debug surface (portal logs join sway+sunshine in
  `display logs`); and no input-path improvement whatsoever.

## 5. Explicit non-goal: portal `VIRTUAL` source type

The ScreenCast spec advertises `AvailableSourceTypes = 4 (VIRTUAL)`: "Extend with new virtual
monitor." portal-wlr does **not** implement it (no `VIRTUAL` handling anywhere in its `src/`), and
Sunshine requests `MONITOR` only. A host-compositor virtual monitor would also break isolation (it
would live in the host session). Plan A stays: Sway owns the virtual output; portal only transports it.

## 6. Build plan for this repo (if adopted)

1. `display/state.py`: session-bus address file path, portal enable flag, portal-wlr config path.
2. `display/scripts_render.py` + `display/scripts/`: `portal_bus.sh` (private `dbus-daemon` + address
   file), `portal_start.sh` (portal daemon + backend with `XDG_CONFIG_HOME` pointed at managed files),
   `sway-portals.conf` and `portal-wlr config` templates (`output_name=HEADLESS-1`,
   `chooser_type=none`, `max_fps` from sync state).
3. `display/manager.py`: order bus → sway → portal → sunshine in `start_display`; extend `setup_display`
   dependency check (`dbus-daemon`, `xdg-desktop-portal`, `xdg-desktop-portal-wlr`, running user
   PipeWire); pin `capture = portal` + `output_name = HEADLESS-1` in managed Sunshine config
   reconciliation.
4. `display/diagnostics.py`: checks for bus liveness, portal names on the private bus, enumerated
   portal streams containing `HEADLESS-1`, Sunshine build portal support; surface in doctor + status.
5. Tests: render tests for the new templates, lifecycle tests with patched seams (same pattern as the
   existing `sunshine_service` seams), config-content assertions for `chooser_type=none`.

## 7. Prototype recipe (validate before coding)

```bash
# 1. deps
sudo pacman -S xdg-desktop-portal xdg-desktop-portal-wlr pipewire wireplumber  # or distro equivalent
# 2. private bus + env for the headless session
export $(dbus-daemon --session --fork --print-address=1 --print-pid=1 | tr '\n' ' ' | sed 's/^/DBUS_SESSION_BUS_ADDRESS=… /')
# 3. portal routing + non-interactive backend config
mkdir -p ~/.config/xdg-desktop-portal ~/.config/xdg-desktop-portal-wlr
printf '[preferred]\ndefault=wlr\norg.freedesktop.impl.portal.ScreenCast=wlr\norg.freedesktop.impl.portal.Screenshot=wlr\n' \
  > ~/.config/xdg-desktop-portal/sway-portals.conf
printf '[screencast]\noutput_name=HEADLESS-1\nchooser_type=none\n' \
  > ~/.config/xdg-desktop-portal-wlr/config
# 4. start headless sway (existing sway_start.sh), then on the private bus:
xdg-desktop-portal & xdg-desktop-portal-wlr &
# 5. run sunshine with WAYLAND_DISPLAY + private DBUS_SESSION_BUS_ADDRESS,
#    sunshine.conf: capture = portal, output_name = HEADLESS-1
# 6. expect in sunshine.log: "Screencasting with XDG portal" + a HEADLESS-1 stream line,
#    a persisted restore token, and zero dialogs. Stream, reboot daemons, stream again
#    (second run must restore with no UI).
```

## Sources

- Portal wire protocol (session lifecycle, `SelectSources` once per session, `persist_mode` 0/1/2,
  single-use rotating restore tokens, `SOURCE_TYPE` MONITOR/WINDOW/VIRTUAL, `pipewire-serial`
  superseding node IDs):
  `https://flatpak.github.io/xdg-desktop-portal/docs/doc-org.freedesktop.portal.ScreenCast.html`
  (interface version 6)
- Backend scope (Screenshot + ScreenCast only), portal-routing file, activation env requirements:
  `https://github.com/emersion/xdg-desktop-portal-wlr` (`README.md`, `contrib/wlroots-portals.conf`,
  `contrib/systemd/xdg-desktop-portal-wlr.service.in`, `wlr.portal`)
- Non-interactive output selection (`chooser_type=none` + `output_name`, "without further interaction"),
  `max_fps`, `force_mod_linear`, `exec_before/after`, config lookup order:
  `xdg-desktop-portal-wlr.5.scd` (man source, same repo)
- Same-protocol-under-the-hood + build deps (`libpipewire-0.3 ≥ 0.3.64`, wayland-protocols, gbm,
  libdrm, sd-bus): same repo (`protocols/`, `src/screencast/`, `meson.build`)
- Sunshine portal client (session-bus connection, `SOURCE_TYPE_MONITOR`, `PERSIST_UNTIL_REVOKED`,
  on-disk restore-token rotation, ScreenCast-only fallback, stream name matching, serial-aware
  PipeWire consumer, `capture == "portal"` dispatch, backend order KMS→NvFBC→WAYLAND→X11→PORTAL→KWIN):
  `src/platform/linux/portalgrab.cpp`, `pipewire.cpp`, `misc.cpp`, `cmake/compile_definitions/linux.cmake`
  (master tree)
- Absent pieces that bound the design (no portal/EIS input in Sunshine, no `VIRTUAL` in portal-wlr,
  no `portal` choice in released `configuration.md`): verified by empty `grep` over those trees
- Current isolation contract this design reuses unchanged: `display/scripts/sway_config`,
  `display/scripts/kwin_input_isolation.py.template`, `display/input_isolation.py`,
  `display/scripts/sunshine_start.sh`, `display/scripts/sunshine_wrapper.sh`

---

# Implementation notes (what building it actually taught us)

Portal mode is implemented in `display/portal.py` (policy), `display/scripts/portal_bus.sh`,
`display/scripts/portal_start.sh` (runtime), `display/scripts_render.py` (blocks), with
`display/sunshine_conf.py` for Sunshine's config keys.  It is opt-in: `display capture-method portal`
(or the hub menu), `capture = portal` is pinned in `sunshine.conf` only while it is enabled, and the
previous values are restored on the way back.

Three assumptions from the design above turned out to be wrong.  Each was found by running the real
Sunshine binary against the real stack, not by reading code.

## 1. A capability-elevated Sunshine ignores `DBUS_SESSION_BUS_ADDRESS`

`/usr/bin/sunshine` ships with file capabilities (`getcap` → `cap_sys_admin,cap_sys_nice=p`), which
makes the kernel mark its processes `AT_SECURE`.  GLib then refuses environment-supplied bus addresses:

```c
/* Don’t load the addresses from the environment if running as setuid, as they
 * come from an unprivileged caller. */
case G_BUS_TYPE_SESSION:
  if (has_elevated_privileges)
    ret = NULL;
  else
    ret = g_strdup (g_getenv ("DBUS_SESSION_BUS_ADDRESS"));
  if (ret == NULL)
    ret = get_session_address_platform_specific (&local_error);
```

(`glib/gio/gdbusaddress.c`, `g_dbus_address_get_for_bus_sync()`.)

Exporting `DBUS_SESSION_BUS_ADDRESS` was therefore useless: Sunshine silently used the **host** session
bus.  The fallback that GLib does use is `$XDG_RUNTIME_DIR/bus`
(`get_session_address_xdg()` — it requires that entry to be a socket owned by the current user;
`g_stat()` follows symlinks, so a symlink to our socket qualifies), and `XDG_RUNTIME_DIR` itself is
honoured without a setuid check (`g_build_user_runtime_dir()`).

So `sunshine_start.sh`, in portal mode, gives Sunshine a **private `XDG_RUNTIME_DIR`**:
`display/default/runtime/` is rebuilt on each start with every entry of the real runtime dir symlinked
in — Wayland, PipeWire, PulseAudio and systemd keep resolving — plus `bus` pointing at the private
portal bus.  Games and prep commands are pinned back to the **real** runtime dir and the **host** bus,
exactly as before.

## 2. Restricting the implementations is what prevents host capture

On the private bus, Sunshine asks for a RemoteDesktop session first (its own fallback to ScreenCast-only
comes second).  Only `kde.portal` lists `RemoteDesktop` (`/usr/share/xdg-desktop-portal/portals/`), so
`xdg-desktop-portal` resolved the request to the KDE backend — which was D-Bus-activated **with the bus
daemon's environment**, reached KWin through the host session bus, and screencast **the host's
ultrawide output**.  Evidence from that run: `[pipewire] Streaming display ... resolution: 2560x1080`
(the host `HDMI-A-1` mode, not our 1920x1080 `HEADLESS-1`), a permission dialog on the desktop, and a
fresh entry in `~/.local/share/flatpak/db/remote-desktop`.

Two changes close this:

- `XDG_DESKTOP_PORTAL_DIR` points at a managed directory holding **only** `wlr.portal`, so no other
  implementation is even a candidate (`xdg-desktop-portal` reads this env var; the string is present in
  the installed binary and the loader logs `load portals from %s`).
- `dbus-daemon` is started under `env -i` with the headless session environment, so anything it
  activates inherits *that* bus address and cannot reach the host compositor.

With the restriction in place the private bus exposes only `PermissionStore` + `wlr`, RemoteDesktop is
unavailable (Sunshine falls back to ScreenCast-only), and the same run reports
`stream sizes: ['1920x1080']`.

## 3. The backend must start before the frontend

`xdg-desktop-portal` resolves implementations at startup.  If the wlr backend is not on the bus yet, the
frontend asks D-Bus to activate it, which spawns a **second** instance from the system service file
(with the private bus's environment, so no compositor).  That instance exits 1 and the frontend then
treats ScreenCast as unusable:

```
xdg-desktop-portal-WARNING: Choosing wlr.portal for org.freedesktop.impl.portal.ScreenCast via the deprecated UseIn key
xdg-desktop-portal-WARNING: Failed to create screen cast proxy: Failed to call StartServiceByName for
  org.freedesktop.impl.portal.desktop.wlr: Process ... exited with status 1
```

`portal_start.sh` now starts the backend first, waits for its bus name, starts the frontend, and only
writes the readiness file once `org.freedesktop.portal.ScreenCast.AvailableSourceTypes` actually
answers.  The wrapper gates Sunshine on that file.

## Permission prompt: one-time, and it is real

A portal capture request is a permission request.  On the machine used for verification a grant dialog
appeared once and, once approved, Sunshine stored a restore token (`Saved portal restore token to
disk`); the next run logged `Loaded portal restore token from disk` and streamed with no dialog.  The
first approval therefore has to be answered on the desktop; after that the virtual display starts
without interaction.  (The dialog seen during the *broken* runs came from the KDE backend that is now
excluded — with only the wlr backend configured, no selection UI appears at all, since
`chooser_type=none`.)

## Verified behaviour

Rendered production scripts, wrapper driven exactly as the systemd unit drives it, real
`/usr/bin/sunshine`, `capture = portal`, one headless Sway output:

- `HEADLESS-1 mode: 1920x1080` and `sunshine stream sizes: ['1920x1080']` → the headless output is what
  gets captured, not the host desktop.
- Portal traffic (`CreateSession`, `SelectSources`, `Start`, `OpenPipeWireRemote`) observed on the
  private bus.
- The private runtime dir exists with 33 entries, including the Wayland and PipeWire sockets, and
  `bus -> /run/user/<uid>/lutristosunshine-portal-bus`.
- Input isolation is untouched: Sunshine's virtual devices still come from `/dev/uinput`, the guest side
  still disables all inputs and re-enables only the passthrough devices, and the KWin host-side filter is
  unchanged.

## Two collateral bugs found and fixed

- `setup_display` wrote the managed files (including the systemd override that points the unit at the
  wrapper) *before* the privileged udev-rule step.  A failing udev step therefore left the unit pointing
  at a half-written stack; in the observed case the portal scripts were removed while the portal-mode
  wrapper stayed, and Sunshine died with `status=127` on every restart.  The udev step now runs first.
- `setup_display` rebuilt **every** path from the detected unit name (`state.build_paths`), which made
  redirected installs leak into the real `~/.config/lutristosunshine` (a test written for this work did
  exactly that).  `display.state.with_sunshine_unit()` now rebuilds only the unit-derived entries, and a
  regression test asserts a redirected setup cannot touch the real install.

## Sources

- GLib session-bus resolution (AT_SECURE behaviour, `XDG_RUNTIME_DIR/bus` fallback, socket ownership
  check): `gnome/glib` — `gio/gdbusaddress.c`, `glib/gutils.c`
- File-capability `AT_SECURE` semantics: `getcap(8)` / `capabilities(7)`
- Portal implementation set (only KDE ships RemoteDesktop) and `XDG_DESKTOP_PORTAL_DIR`:
  `/usr/share/xdg-desktop-portal/portals/*.portal`, `/usr/libexec/xdg-desktop-portal`
- ScreenCast readiness property and stream metadata:
  `https://flatpak.github.io/xdg-desktop-portal/docs/doc-org.freedesktop.portal.ScreenCast.html`
- Non-interactive backend config, config lookup order, PipeWire dependency:
  `xdg-desktop-portal-wlr` (`README.md`, `xdg-desktop-portal-wlr.5.scd`, `meson.build`)
- Sunshine portal client (RemoteDesktop-first, restore-token persistence, name matching, PipeWire
  consumer): `src/platform/linux/portalgrab.cpp`, `pipewire.cpp`, `misc.cpp`

---

# Second round: the config-pinning hole, the probe storm, and what the host saw

Live testing with a real client found two more problems, both in the tool rather
than in the design.

## 1. A stale `capture` value silently streamed the host desktop

Symptom: a session that started fine (audio isolated, game on the virtual
display, frame rate negotiated) streamed **the host monitor** instead.  The
Sunshine log showed the giveaway:

```
[portalgrab] Found stream for display id/name: '' position: 0x0 resolution: 2560x1080
[portalgrab] Found stream for display id/name: '' position: 2560x0 resolution: 1920x1080
[portalgrab] Using first available stream as no matching stream was found for: ''
```

Those are the host's two monitors, i.e. Sunshine was talking to the **host**
`xdg-desktop-portal` (KDE), not to the private restricted one.  Its environment
confirmed it: `XDG_RUNTIME_DIR=/run/user/1000` and
`DBUS_SESSION_BUS_ADDRESS=unix:path=/run/user/1000/bus` — no private runtime dir,
so no private bus.

Cause: `sunshine.conf` contained `capture = portal` while the tool's state said
`wlr`.  The tool had rendered *wlr-mode* scripts (no private bus blocks, no
runtime-dir farm), and its pinning policy at the time only owned the `capture`
key in portal mode: in wlr mode it left a foreign value alone and even **removed
`output_name = HEADLESS-1`** (a key wlr mode needs).  Sunshine therefore did
exactly what it was told — portal capture — against the only portal it could
reach: the host's.

Fixed by making the pinning policy match the display's needs:

- while the virtual display is enabled, the tool owns `capture` **and**
  `output_name` for both backends (`capture = wlr|portal`,
  `output_name = HEADLESS-1`);
- the values found before it pinned are remembered once and restored when the
  display is disabled or removed (`clear_capture_config`), and values this tool
  itself wrote are never remembered as a user preference;
- drift is no longer silent: `sync_capture_config` prints what it corrected, and
  `display doctor` reports
  `Sunshine's config does not match this display's capture backend: capture = portal (expected wlr); …`.

## 2. Sunshine's encoder probe killed the backend

With the farm and the private bus finally in place, Sunshine reached the managed
portal — and every call returned
`Could not create ScreenCast session: … UnknownMethod … org.freedesktop.portal.ScreenCast`,
because the wlroots backend had already exited:

```
2026/09/15 11:35:58 [ERROR] - pipewire: fatal error event from core
```

The backend is *not* broken in isolation: started by hand with the same
environment it stays up, answers `AvailableSourceTypes`, and survives a fresh
frontend resolving it.  The trigger is specifically Sunshine's start-up encoder
probe, which creates and tears down one capture session per encoder it tests.
When one of those session teardowns/links fails (`no more input formats` between
Sunshine's consumer and the wlroots producer), the backend exits, and the
frontend then caches the missing implementation, so every later request fails —
including the client's.

`portal_start.sh` now supervises the pair: if either daemon exits it tears the
other down and starts a fresh generation (bounded at 10 attempts, 1s apart, ready
file cleared while down), so a single bad session costs one restart instead of the
whole stream.  Verified on the live setup: one restart, then

```
Info: [pipewire] Streaming display 'HEADLESS-1' offset: 0x0 resolution: 1920x1080
Info: Found H.264 encoder: h264_vulkan [vulkan]
Info: Found HEVC encoder: hevc_vulkan [vulkan]
```

and `display doctor` reporting `[PASS] Portal capture: Private portal session is
serving the headless output.`

Related operational note: with no `encoder` key in `sunshine.conf`, Sunshine
probes nvenc (fails: no CUDA), vulkan, software and the vaapi encoders — far more
sessions than pinning `encoder = vaapi`.  Fewer sessions means fewer chances to
hit the teardown bug above.

## Still open

- Our private frontend spawns its own document portal, which can stack a FUSE
  mount over the host session's `$XDG_RUNTIME_DIR/doc`.  The runtime-dir builder
  never stats mount points (see above), so it cannot hang on it, and the host
  mount was observed to stay the only one after the restarts; isolating the
  daemons in a private `XDG_RUNTIME_DIR` remains the clean fix but needs testing
  against the backend's Wayland/PipeWire needs.
- The wlroots backend's exit on a failed stream teardown is worked around, not
  fixed: a future wlroots/xdg-desktop-portal-wlr version may not need the
  supervisor.

## 3. Refresh-rate sync broke with the portal backend

`refresh_rate_sync_mode = client` sets the headless output from
`SUNSHINE_CLIENT_FPS`, and `exact` resolves the real rate from Sunshine's log.
Both were wrong in portal mode, for two independent reasons:

- `SUNSHINE_CLIENT_FPS` is `launch_session->fps`, taken from the **launch
  request's** `mode=WxHxFPS` parameter (`src/nvhttp.cpp`).  The rate the stream
  actually runs at comes from the RTSP announce
  (`x-nv-video[0].clientRefreshRateX100` → `config.monitor.framerateX100`,
  `src/rtsp.cpp`).  Moonlight can send a different value in each, e.g. a launch
  mode of 60 with a client refresh of 120.
- The log line `exact` mode parses is backend-specific.  The wlroots backend
  writes `[wlgrab] Requested frame rate [60000/1001, approx. 59.94 fps]` — which
  is what the pattern matched — while the portal backend writes
  `[pipewire] Requested frame rate: 120/1, approx. 120.00 fps`, or, for variable
  rate capture, only `[pipewire] Requested variable frame rate (Sunshine pacing
  required: 8.3333ms)`.  Neither portal form matched, so `exact` silently fell
  back to the (wrong) launch value in portal mode.

Symptom: a 2560x1440@120 stream with the virtual display left at
`2560x1440 @ 60 Hz`.

Fixed in three places:

- `resolve_stream_fps.sh` understands the portal forms, including deriving the
  rate from the pacing line (`1000 / milliseconds`);
- `apply_exact_refresh.sh` takes an optional `client` rate mode that rounds the
  resolved rate to the integer Moonlight shows (59.94 → 60, 119.88 → 120) and
  keeps applying `double-refresh` on top;
- `set_resolution.sh` runs that correction in the background for `client` mode,
  exactly like `exact` mode already did, so the display starts on the launch
  value and then follows the rate the stream really uses.

Verified live: the resolver reads the current stream's pacing line
(`16.6667ms` → 60), and running the apply script in `client` mode against a
120 fps client request sets `HEADLESS-1` to `2560x1440 @ 120 Hz`.

## Where the client's frame rate actually comes from (portal mode)

From `src/rtsp.cpp` (announce handling) and `src/video.h`:

- `x-nv-video[0].maxFPS` -> `config.monitor.framerate` is the frame rate the client
  wants the **stream** to run at.  It drives Sunshine's pacing
  (`capture_frame_interval()`), so it is the rate the game should render at.
- `x-nv-video[0].clientRefreshRateX100` -> `config.monitor.framerateX100` is the
  client's **display** refresh.  Sunshine zeroes it unless it is within 1% of
  `maxFPS`, with an upstream comment giving the reason: clients such as Moonlight
  Android report the client display's refresh here, which may differ from the
  requested streaming framerate.
- In portal mode Sunshine always negotiates variable-rate capture (the KWin
  workaround only covers KWin <= 6.7), so the backend logs
  `Requested variable frame rate (Sunshine pacing required: <delay>ms)` rather than
  `Requested frame rate [...]`.  `<delay>` comes from the same
  `framerate`/`framerateX100` pair, i.e. it equals the stream rate.

Consequences for the tool:

- Nothing else in Sunshine exposes the client's display refresh: it is not logged
  and no HTTP API reports it (checked across `src/`).  A stream running at 60 fps
  while the client's display is 120 Hz is Sunshine's intended behaviour, not a
  sync bug of this tool.
- The rates the tool *can* see are the client's requested stream rate
  (`SUNSHINE_CLIENT_FPS`, from the launch request's `mode=WxHxFPS`) and the pacing
  line above.  Both are now resolved by `resolve_stream_fps.sh`, and
  `client`/`exact` sync use the resolved value.
- Both scripts log what they were told (`lts-display: applying ...` and
  `lts-display: stream rate resolved to ...`) into Sunshine's log, since prep-cmd
  output is captured there.  That makes a rate mismatch diagnosable without
  guessing.
