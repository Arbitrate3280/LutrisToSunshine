"""Tests for the portal capture backend of the virtual display.

Portal mode changes three things that must stay in sync: Sunshine's own
``capture``/``output_name`` config keys, the private session bus the portal
stack runs on, and which bus launched games inherit.  Every test here asserts
against the rendered files or the managed config file, because those are what
actually ship to the user.
"""

import contextlib
import io
import subprocess
import unittest
from dataclasses import asdict
from pathlib import Path
from unittest.mock import Mock, patch

import lutristosunshine
from display import audio_policy, diagnostics, hub, manager, portal, scripts_render
from display import state as display_state
from display.constants import (
    CONFIG_ROOT,
    HEADLESS_OUTPUT_NAME,
    PORTAL_BUS_SOCKET_NAME,
    PORTAL_DESKTOP_NAME,
    PORTAL_WLR_BACKEND_NAME,
)
from display.state import DisplayPaths
from sunshine import installation
from tests._display_test_helpers import (
    assert_state_paths_under,
    patched,
    snapshot_fixture,
    temp_display_state,
)


def _portal_files(state) -> dict:
    return scripts_render.render_managed_files(state)


class CaptureMethodStateTests(unittest.TestCase):
    def test_unknown_capture_method_falls_back_to_wlr(self) -> None:
        self.assertEqual(display_state.normalized_capture_method("bogus"), "wlr")
        self.assertEqual(display_state.normalized_capture_method(None), "wlr")
        self.assertEqual(display_state.normalized_capture_method("PORTAL"), "portal")

    def test_load_state_normalizes_capture_method(self) -> None:
        state = manager._default_state()
        state.capture_method = "nonsense"
        with patched(display_state, load_state=lambda: state):
            normalized = display_state.load_state()
        self.assertEqual(normalized.capture_method, "nonsense")

        # Round-trip through the dataclass serializer keeps the field.
        restored = display_state.DisplayState.from_dict(
            display_state.default_state().to_dict()
        )
        self.assertEqual(restored.capture_method, "wlr")

    def test_state_file_round_trip_preserves_portal_choice(self) -> None:
        state = display_state.default_state()
        state.capture_method = "portal"
        restored = display_state.DisplayState.from_dict(state.to_dict())
        self.assertEqual(restored.capture_method, "portal")


class PortalConfigTests(unittest.TestCase):
    def test_routing_conf_only_pins_screencast_and_screenshot(self) -> None:
        content = portal.routing_conf_content()

        self.assertIn("org.freedesktop.impl.portal.ScreenCast=wlr", content)
        self.assertIn("org.freedesktop.impl.portal.Screenshot=wlr", content)
        # No global default: unrelated portal interfaces keep resolving normally.
        self.assertNotIn("default=", content)

    def test_wlr_portal_file_is_the_only_visible_implementation(self) -> None:
        content = portal.wlr_portal_content()

        self.assertIn("[portal]", content)
        self.assertIn(f"DBusName={PORTAL_WLR_BACKEND_NAME}", content)
        self.assertIn("org.freedesktop.impl.portal.ScreenCast;", content)
        # RemoteDesktop must stay out: Sunshine asks for it first, and only KDE
        # implements it, which would capture the host session instead.
        self.assertNotIn("RemoteDesktop", content)

    def test_wlr_backend_conf_is_headless_and_pinned_to_the_virtual_output(self) -> None:
        content = portal.wlr_config_content()

        self.assertIn("[screencast]", content)
        self.assertIn(f"output_name={HEADLESS_OUTPUT_NAME}", content)
        self.assertIn("chooser_type=none", content)
        # A static max_fps would fight the display-sync refresh handling.
        self.assertNotIn("max_fps", content)

    def test_managed_files_only_exist_in_portal_mode(self) -> None:
        state = manager._default_state()
        self.assertEqual(portal.managed_files(state), {})

        state.capture_method = "portal"
        files = portal.managed_files(state)
        self.assertEqual(
            sorted(Path(path).name for path in files),
            ["config", "sway-portals.conf", "wlr.portal"],
        )
        self.assertIn("xdg-desktop-portal-wlr", str(state.paths.portal_wlr_config))
        self.assertIn("xdg-desktop-portal", str(state.paths.portal_routing_conf))
        self.assertEqual(Path(state.paths.portal_wlr_portal).parent.name, "portals")


class SunshineCapturePinningTests(unittest.TestCase):
    def _state(self, conf_text: str = "") -> tuple:
        state = manager._default_state()
        state.enabled = True
        state.paths = DisplayPaths(**asdict(state.paths))
        conf_path = Path(state.paths.portal_routing_conf).parent.parent / "sunshine.conf"
        conf_path.parent.mkdir(parents=True, exist_ok=True)
        conf_path.write_text(conf_text, encoding="utf-8")
        state.paths.sunshine_conf = str(conf_path)
        return state, conf_path

    def test_portal_mode_pins_capture_and_output(self) -> None:
        state, conf_path = self._state("encoder = nvenc\n")

        state.capture_method = "portal"
        portal.sync_capture_config(state)

        text = conf_path.read_text(encoding="utf-8")
        self.assertIn("capture = portal", text)
        self.assertIn(f"output_name = {HEADLESS_OUTPUT_NAME}", text)
        # Unrelated settings are preserved.
        self.assertIn("encoder = nvenc", text)

    def test_wlr_mode_pins_the_managed_display_and_remembers_the_user_value(self) -> None:
        state, conf_path = self._state("capture = kms\noutput_name = 2\n")

        portal.sync_capture_config(state)

        text = conf_path.read_text(encoding="utf-8")
        self.assertIn("capture = wlr", text)
        self.assertIn(f"output_name = {HEADLESS_OUTPUT_NAME}", text)
        self.assertNotIn("capture = kms", text)
        # The user's own values are kept for when the display is removed.
        self.assertEqual(
            state.sunshine_capture_override["capture"]["value"], "kms"
        )
        self.assertEqual(state.sunshine_capture_override["output_name"]["value"], "2")

    def test_disabling_restores_the_user_values(self) -> None:
        state, conf_path = self._state("capture = kms\noutput_name = 2\n")
        portal.sync_capture_config(state)

        portal.clear_capture_config(state)

        text = conf_path.read_text(encoding="utf-8")
        self.assertIn("capture = kms", text)
        self.assertIn("output_name = 2", text)
        self.assertIsNone(state.sunshine_capture_override)

    def test_disabling_removes_keys_the_tool_added(self) -> None:
        state, conf_path = self._state("encoder = nvenc\n")
        portal.sync_capture_config(state)

        portal.clear_capture_config(state)

        text = conf_path.read_text(encoding="utf-8")
        self.assertNotIn("capture", text)
        self.assertNotIn("output_name", text)
        self.assertIn("encoder = nvenc", text)

    def test_switching_backends_repins_and_still_restores_later(self) -> None:
        state, conf_path = self._state("capture = kms\n")

        state.capture_method = "portal"
        portal.sync_capture_config(state)
        self.assertIn("capture = portal", conf_path.read_text(encoding="utf-8"))

        state.capture_method = "wlr"
        portal.sync_capture_config(state)
        self.assertIn("capture = wlr", conf_path.read_text(encoding="utf-8"))

        portal.clear_capture_config(state)
        self.assertIn("capture = kms", conf_path.read_text(encoding="utf-8"))

    def test_capture_setting_drift_is_pinned_again_and_reported(self) -> None:
        # Regression: a leftover capture value from the other backend (or one set
        # in Sunshine's own UI) sent the stream to the host desktop instead of
        # the virtual display, with no warning anywhere.
        state, conf_path = self._state("capture = portal\n")
        state.capture_method = "wlr"

        self.assertEqual(
            portal.capture_config_drift(state),
            [("capture", "wlr", "portal"), ("output_name", HEADLESS_OUTPUT_NAME, "")],
        )

        buffer = io.StringIO()
        with contextlib.redirect_stdout(buffer):
            portal.sync_capture_config(state)

        self.assertIn("did not match the virtual display", buffer.getvalue())
        text = conf_path.read_text(encoding="utf-8")
        self.assertIn("capture = wlr", text)
        self.assertIn(f"output_name = {HEADLESS_OUTPUT_NAME}", text)

    def test_pinned_values_are_never_remembered_as_user_preferences(self) -> None:
        # A value this tool wrote must not come back as a "user preference"
        # when the display is removed.
        state, conf_path = self._state("capture = portal\n")
        state.capture_method = "portal"

        portal.sync_capture_config(state)

        self.assertNotIn("capture", state.sunshine_capture_override)
        portal.clear_capture_config(state)
        self.assertNotIn("capture", conf_path.read_text(encoding="utf-8"))

    def test_user_override_after_portal_mode_is_kept_on_reset(self) -> None:
        state, conf_path = self._state("")
        state.capture_method = "portal"
        portal.sync_capture_config(state)

        # The user takes over the key while portal mode is enabled.
        conf_path.write_text("capture = kms\n", encoding="utf-8")
        portal.clear_capture_config(state)

        self.assertIn("capture = kms", conf_path.read_text(encoding="utf-8"))


class PortalScriptRenderingTests(unittest.TestCase):
    def _portal_state(self):
        state = manager._default_state()
        state.enabled = True
        state.capture_method = "portal"
        return state

    def test_wlr_mode_renders_no_portal_scripts(self) -> None:
        state = manager._default_state()
        state.enabled = True

        rendered = _portal_files(state)
        self.assertNotIn(Path(state.paths.portal_bus_script), rendered)
        self.assertNotIn(Path(state.paths.portal_start_script), rendered)

    def test_portal_mode_renders_bus_and_daemon_scripts(self) -> None:
        state = self._portal_state()
        rendered = _portal_files(state)

        bus = rendered[Path(state.paths.portal_bus_script)]
        self.assertIn("dbus-daemon --session --fork", bus)
        self.assertIn(PORTAL_BUS_SOCKET_NAME, bus)
        self.assertIn(state.paths.portal_bus_address_file, bus)

        daemon = rendered[Path(state.paths.portal_start_script)]
        self.assertIn(PORTAL_DESKTOP_NAME, daemon)
        self.assertIn(PORTAL_WLR_BACKEND_NAME, daemon)
        self.assertIn('export XDG_CONFIG_HOME="$config_home"', daemon)
        self.assertIn(state.paths.portal_ready_file, daemon)

    def test_wrapper_orders_bus_sway_portal_then_sunshine(self) -> None:
        state = self._portal_state()
        wrapper = _portal_files(state)[Path(state.paths.sunshine_wrapper_script)]

        positions = [
            wrapper.index('"$portal_bus_script"'),
            wrapper.index('setsid "$sway_start_script"'),
            wrapper.index('setsid "$portal_start_script"'),
            wrapper.index('setsid "$sunshine_start_script"'),
        ]
        self.assertEqual(positions, sorted(positions))
        # Sunshine must not start before the portal answered on the bus.
        self.assertLess(
            wrapper.index("Virtual display portal stack did not become ready."),
            wrapper.index('setsid "$sunshine_start_script"'),
        )
        self.assertIn('stop_child "$portal_pid"', wrapper)
        self.assertIn("stop_portal_bus", wrapper)
        # The portal bus is a managed resource: the pid file is its handle.
        self.assertIn(state.paths.portal_bus_pid_file, wrapper)

    def test_portal_stack_starts_the_backend_before_the_frontend(self) -> None:
        state = self._portal_state()
        daemon = _portal_files(state)[Path(state.paths.portal_start_script)]

        # xdg-desktop-portal resolves implementations at startup.  If the backend
        # is not on the bus yet it asks D-Bus to activate one, which spawns a
        # second instance from the system service file (no compositor in this
        # environment) and marks ScreenCast unusable.
        self.assertLess(daemon.index('"$backend_binary"'), daemon.index('"$portal_binary"'))
        self.assertIn('wait_for_name "$backend_name" "$backend_pid"', daemon)

    def test_readiness_requires_a_working_screencast_interface(self) -> None:
        state = self._portal_state()
        daemon = _portal_files(state)[Path(state.paths.portal_start_script)]

        self.assertIn("screencast_available()", daemon)
        self.assertIn("org.freedesktop.portal.ScreenCast", daemon)
        self.assertIn('if name_owned "$portal_name" && screencast_available; then', daemon)

    def test_audio_sink_script_reports_why_creation_failed(self) -> None:
        state = self._portal_state()
        script = audio_policy.managed_files(state)[
            Path(state.paths.audio_create_script)
        ]

        # The sink script's exit code aborts the whole app launch (Sunshine's
        # proc_t::execute() returns -1 on a failing prep command), so its output
        # has to say what went wrong.
        self.assertIn('module_output="$(run_audio_command pactl load-module', script)
        self.assertIn('printf \'%s\\n\' "$module_output" >&2', script)
        self.assertIn("grep -E '^[0-9]+$'", script)

    def test_portal_stack_supervises_the_daemon_pair(self) -> None:
        # Sunshine's encoder probe creates and tears down a capture session per
        # encoder; a backend that dies on one of them must not leave the session
        # without a portal (the frontend caches the missing implementation).
        state = self._portal_state()
        daemon = _portal_files(state)[Path(state.paths.portal_start_script)]

        self.assertIn("start_portal_generation()", daemon)
        self.assertIn("backoff=1", daemon)
        self.assertIn("while true; do", daemon)
        self.assertIn("sleep \"$backoff\"", daemon)
        self.assertIn('wait -n "$portal_pid" "$backend_pid"', daemon)
        self.assertIn('rm -f "$ready_file"', daemon)

    def test_sunshine_start_uses_the_private_bus_only_in_portal_mode(self) -> None:
        portal_state = self._portal_state()
        portal_start = _portal_files(portal_state)[Path(portal_state.paths.sunshine_start_script)]

        # Sunshine carries file capabilities, so GLib ignores
        # DBUS_SESSION_BUS_ADDRESS (AT_SECURE) and only accepts the bus found at
        # $XDG_RUNTIME_DIR/bus: the private runtime dir is the actual redirect.
        self.assertIn(portal_state.paths.portal_bus_address_file, portal_start)
        self.assertIn(f'portal_runtime_dir="{portal_state.paths.portal_runtime_dir}"', portal_start)
        self.assertIn('ln -s "$portal_bus_socket" "$portal_runtime_dir/bus"', portal_start)
        self.assertIn('runtime_export_value="$portal_runtime_dir"', portal_start)
        self.assertIn('export XDG_RUNTIME_DIR="$runtime_export_value"', portal_start)
        # Every other runtime entry stays reachable through a symlink.
        self.assertIn('ln -sf "$entry" "$portal_runtime_dir/$entry_name"', portal_start)
        # Directories are mirrored (real dir, linked entries) because libpulse
        # refuses a symlinked runtime dir; its failing pactl call in turn aborts
        # Sunshine's app launch.  One level only: $XDG_RUNTIME_DIR/doc is a FUSE
        # mount and walking deeper stalls the Sunshine start.
        self.assertIn('if [ -d "$entry" ] && [ ! -L "$entry" ]; then', portal_start)
        self.assertIn(
            'mkdir -m 700 "$portal_runtime_dir/$entry_name" 2>/dev/null || true',
            portal_start,
        )
        self.assertIn(
            'ln -sf "$nested" "$portal_runtime_dir/$entry_name/$(basename "$nested")"',
            portal_start,
        )
        # No recursion: a single loop body, no self-call.
        self.assertNotIn("link_runtime_contents", portal_start)

        wlr_state = manager._default_state()
        wlr_state.enabled = True
        wlr_start = _portal_files(wlr_state)[Path(wlr_state.paths.sunshine_start_script)]
        self.assertNotIn("portal-bus-address", wlr_start)
        self.assertIn('export XDG_RUNTIME_DIR="$runtime_export_value"', wlr_start)
        self.assertIn('runtime_export_value="$runtime_dir"', wlr_start)

    def test_bus_daemon_env_cannot_reach_the_host_session(self) -> None:
        state = self._portal_state()
        bus = _portal_files(state)[Path(state.paths.portal_bus_script)]

        # Activated services inherit the bus daemon's environment; it must be the
        # headless session, never the host's display or session bus.
        self.assertIn("dbus-daemon --session --fork", bus)
        self.assertIn('"DBUS_SESSION_BUS_ADDRESS=$bus_address"', bus)
        self.assertIn('"XDG_CONFIG_HOME=$config_home"', bus)
        self.assertNotIn("WAYLAND_DISPLAY", bus)
        self.assertNotIn("DISPLAY=", bus)
        self.assertIn("env -i", bus)
        # The standard session config is kept so activation still works.
        self.assertNotIn("--systemd-activation", bus)

    def test_launched_games_keep_the_host_session_bus(self) -> None:
        state = self._portal_state()
        rendered = _portal_files(state)
        host_address = portal.host_bus_address()

        for key in ("headless_prep_script", "launch_app_script"):
            script = rendered[Path(getattr(state.paths, key))]
            self.assertIn(f'dbus_value="{host_address}"', script)
            self.assertNotIn(state.paths.portal_bus_address_file, script)
            self.assertNotIn('dbus_value="${DBUS_SESSION_BUS_ADDRESS', script)
            # Games must also keep the real runtime dir (Sunshine has a private one).
            self.assertIn(f'runtime_dir="{portal.host_runtime_dir()}"', script)
            self.assertNotIn(state.paths.portal_runtime_dir, script)

        # The Flatpak handoff helpers must talk to the host bus explicitly.
        prep = rendered[Path(state.paths.headless_prep_script)]
        self.assertIn('DBUS_SESSION_BUS_ADDRESS="$dbus_value" dbus-update-activation-environment', prep)
        self.assertIn('DBUS_SESSION_BUS_ADDRESS="$dbus_value" stdbuf', prep)

    def test_headless_output_name_is_rendered_everywhere_it_is_used(self) -> None:
        state = self._portal_state()
        rendered = _portal_files(state)

        for key in (
            "sway_config",
            "set_resolution_script",
            "apply_exact_refresh_script",
            "reset_resolution_script",
            "launch_app_script",
        ):
            content = rendered[Path(getattr(state.paths, key))]
            self.assertNotIn("HEADLESS-1", content.replace(HEADLESS_OUTPUT_NAME, ""))
            self.assertIn(HEADLESS_OUTPUT_NAME, content)

    def test_no_unsubstituted_placeholders_in_portal_mode(self) -> None:
        state = self._portal_state()
        state.refresh_rate_sync_mode = "exact"
        state.custom_display_mode = {"width": 3440, "height": 1440, "refresh": 59.94}
        state.gpu_mode = "manual"
        state.gpu_card_path = "/dev/dri/card0"
        state.gpu_render_path = "/dev/dri/renderD128"
        state.renderer_mode = "vulkan"

        for path, content in _portal_files(state).items():
            self.assertNotRegex(
                content,
                r"@[A-Z][A-Z0-9_]*@",
                f"unsubstituted placeholder in {path.name}",
            )


class PortalStatusTests(unittest.TestCase):
    def _state_with_bus(self, tmp: Path) -> tuple:
        state = manager._default_state()
        state.capture_method = "portal"
        state.paths = DisplayPaths(**asdict(state.paths))
        socket_path = tmp / "bus-socket"
        state.paths.portal_bus_address_file = str(tmp / "portal-bus-address")
        Path(state.paths.portal_bus_address_file).write_text(
            f"unix:path={socket_path}", encoding="utf-8"
        )
        return state, socket_path

    def test_missing_bus_socket_reports_an_error(self) -> None:
        import tempfile

        with tempfile.TemporaryDirectory() as raw:
            state, _socket_path = self._state_with_bus(Path(raw))
            status = portal.portal_status(state, runner=Mock())

        self.assertFalse(status["bus_active"])
        self.assertIn("missing", str(status["error"]))

    def test_names_owned_by_both_portal_daemons_is_ready(self) -> None:
        import tempfile

        with tempfile.TemporaryDirectory() as raw:
            state, socket_path = self._state_with_bus(Path(raw))
            socket_path.touch()
            runner = Mock(return_value=subprocess.CompletedProcess([], 0, "(true,)", ""))
            status = portal.portal_status(state, runner=runner)

        self.assertTrue(status["bus_active"])
        self.assertTrue(status["names_active"])
        self.assertEqual(status["error"], "")
        self.assertEqual(runner.call_count, 2)

    def test_owning_only_one_name_is_not_ready(self) -> None:
        import tempfile

        with tempfile.TemporaryDirectory() as raw:
            state, socket_path = self._state_with_bus(Path(raw))
            socket_path.touch()
            runner = Mock(return_value=subprocess.CompletedProcess([], 0, "(false,)", ""))
            status = portal.portal_status(state, runner=runner)

        self.assertTrue(status["bus_active"])
        self.assertFalse(status["names_active"])
        self.assertIn(PORTAL_DESKTOP_NAME, str(status["error"]))

    def test_missing_dependencies_reports_portal_packages(self) -> None:
        state = manager._default_state()
        state.capture_method = "portal"

        with patch.object(portal.shutil, "which", return_value=None), \
             patch.object(portal.Path, "is_file", return_value=False):
            missing = diagnostics.missing_dependencies(
                which_fn=lambda name: None,
                capture_method=state.capture_method,
            )

        self.assertIn("xdg-desktop-portal", missing)
        self.assertIn("xdg-desktop-portal-wlr", missing)
        self.assertIn("dbus-daemon", missing)

    def test_wlr_mode_does_not_require_portal_packages(self) -> None:
        missing = diagnostics.missing_dependencies(
            which_fn=lambda name: None,
            capture_method="wlr",
        )

        self.assertNotIn("xdg-desktop-portal", missing)


class PortalDiagnosticsTests(unittest.TestCase):
    def _snapshot(self, **overrides):
        state = manager._default_state()
        state.enabled = True
        state.capture_method = "portal"

        with patched(display_state, load_state=lambda: state), \
             patched(diagnostics, missing_dependencies=lambda **kwargs: []):
            snapshot = diagnostics.display_snapshot(
                load_state_fn=lambda: state,
                ensure_dependencies_fn=lambda: [],
                sunshine_active_fn=lambda: False,
                sunshine_unit_fn=lambda: "sunshine.service",
                host_session_fn=lambda: "sway",
                input_isolation_mode_fn=lambda: "permissions-only",
                kwin_status_fn=lambda current: diagnostics.empty_kwin_input_isolation_status(),
                sunshine_input_devices_fn=lambda: [],
                current_headless_mode_fn=lambda *args: "",
                portal_status_fn=lambda current: dict(
                    {"bus_active": True, "names_active": True, "error": ""},
                    **overrides,
                ),
            )
        return snapshot

    def test_snapshot_exposes_capture_method_and_portal_health(self) -> None:
        snapshot = self._snapshot()

        self.assertEqual(snapshot.capture_method, "portal")
        self.assertIn("[PORTAL]", snapshot.capture_method_label)
        self.assertTrue(snapshot.portal_bus_active)
        self.assertTrue(snapshot.portal_names_active)

    def test_doctor_reports_a_healthy_portal_session(self) -> None:
        report = diagnostics.display_doctor_report(self._snapshot())

        check = next(item for item in report.checks if item.label == "Portal capture")
        self.assertEqual(check.status, "pass")

    def test_doctor_warns_when_the_portal_session_is_not_serving(self) -> None:
        snapshot = self._snapshot(
            names_active=False,
            error=f"{PORTAL_WLR_BACKEND_NAME} is not owned on the private session bus.",
        )
        report = diagnostics.display_doctor_report(snapshot)

        check = next(item for item in report.checks if item.label == "Portal capture")
        self.assertEqual(check.status, "warn")
        self.assertIn(PORTAL_WLR_BACKEND_NAME, check.message)

    def test_doctor_omits_the_portal_check_in_wlr_mode(self) -> None:
        state = manager._default_state()
        state.enabled = True
        with patched(display_state, load_state=lambda: state):
            snapshot = diagnostics.display_snapshot(
                load_state_fn=lambda: state,
                ensure_dependencies_fn=lambda: [],
                sunshine_active_fn=lambda: False,
                host_session_fn=lambda: "sway",
                kwin_status_fn=lambda current: diagnostics.empty_kwin_input_isolation_status(),
                sunshine_input_devices_fn=lambda: [],
                current_headless_mode_fn=lambda *args: "",
            )

        report = diagnostics.display_doctor_report(snapshot)
        self.assertFalse([item for item in report.checks if item.label == "Portal capture"])


class SetCaptureMethodTests(unittest.TestCase):
    def test_switch_persists_choice_and_reconciles_files(self) -> None:
        state = manager._default_state()
        refreshed = []

        previous, returned = manager.set_capture_method(
            "portal",
            load_state_fn=lambda: state,
            refresh_managed_files_fn=lambda current: refreshed.append(current) or current,
        )

        self.assertEqual(previous, "wlr")
        self.assertEqual(state.capture_method, "portal")
        self.assertEqual(refreshed, [state])
        self.assertIs(returned, state)

    def test_unknown_method_falls_back_to_wlr(self) -> None:
        state = manager._default_state()
        state.capture_method = "portal"

        previous, _ = manager.set_capture_method(
            "bogus",
            load_state_fn=lambda: state,
            refresh_managed_files_fn=lambda current: current,
        )

        self.assertEqual(previous, "portal")
        self.assertEqual(state.capture_method, "wlr")


class SunshineUnitPathTests(unittest.TestCase):
    def test_only_unit_derived_paths_are_rebuilt(self) -> None:
        state = manager._default_state()
        state.paths = DisplayPaths(**asdict(state.paths))
        before = asdict(state.paths)

        updated = display_state.with_sunshine_unit(state.paths, "custom-sunshine.service")

        self.assertEqual(Path(updated.sunshine_override).name, "override.conf")
        self.assertEqual(Path(updated.sunshine_override_dir).name, "custom-sunshine.service.d")
        self.assertEqual(Path(updated.sunshine_override_dir).parent, Path(before["systemd_user_dir"]))
        for key, value in before.items():
            if key in {"sunshine_override", "sunshine_override_dir"}:
                continue
            self.assertEqual(getattr(updated, key), value, key)

    def test_empty_unit_falls_back_to_the_default_unit(self) -> None:
        state = manager._default_state()
        updated = display_state.with_sunshine_unit(state.paths, "")

        self.assertIn(display_state.DEFAULT_SUNSHINE_UNIT, updated.sunshine_override_dir)


class ManagerPortalLifecycleTests(unittest.TestCase):
    def test_refresh_writes_and_removes_portal_files_with_the_mode(self) -> None:
        with temp_display_state(manager) as (state, base):
            state.enabled = True
            conf_path = base / "sunshine.conf"
            conf_path.write_text("", encoding="utf-8")
            state.paths.sunshine_conf = str(conf_path)
            state.sunshine_execstart = "sunshine"

            with patched(
                display_state,
                load_state=lambda: state,
                save_state=lambda current: None,
            ), patched(manager, _daemon_reload=lambda: None):
                state.capture_method = "portal"
                manager.refresh_managed_files(state)

                self.assertTrue(Path(state.paths.portal_bus_script).exists())
                self.assertTrue(Path(state.paths.portal_start_script).exists())
                self.assertTrue(Path(state.paths.portal_routing_conf).exists())
                self.assertTrue(Path(state.paths.portal_wlr_config).exists())
                self.assertIn("capture = portal", conf_path.read_text(encoding="utf-8"))

                state.capture_method = "wlr"
                manager.refresh_managed_files(state)

            self.assertFalse(Path(state.paths.portal_bus_script).exists())
            self.assertFalse(Path(state.paths.portal_start_script).exists())
            self.assertFalse(Path(state.paths.portal_routing_conf).exists())
            self.assertFalse(Path(state.paths.portal_wlr_config).exists())
            self.assertFalse(Path(state.paths.portal_wlr_portal).exists())
            # Switching backends re-pins Sunshine: while the virtual display is
            # enabled the managed display has to own the capture method.
            self.assertIn("capture = wlr", conf_path.read_text(encoding="utf-8"))

    def test_remove_display_cleans_portal_state_and_config(self) -> None:
        with temp_display_state(manager) as (state, base):
            state.enabled = True
            state.capture_method = "portal"
            conf_path = base / "sunshine.conf"
            conf_path.write_text("encoder = nvenc\n", encoding="utf-8")
            state.paths.sunshine_conf = str(conf_path)

            portal_files = portal.managed_files(state)
            portal.sync_capture_config(state)
            for path, content in portal_files.items():
                manager._write_file(path, content)
            for key in (
                "portal_bus_address_file",
                "portal_bus_pid_file",
                "portal_ready_file",
                "portal_log_file",
            ):
                Path(getattr(state.paths, key)).write_text("x", encoding="utf-8")

            with patched(
                display_state,
                load_state=lambda: state,
                save_state=lambda current: None,
            ), patched(manager._svc, cleanup_managed_overrides=lambda current: None):
                result = manager.remove_display(
                    stop_display_fn=lambda: 0,
                    remove_udev_rule_fn=lambda current: True,
                    daemon_reload_fn=lambda: None,
                    audio_remove_fn=lambda current: None,
                )

            self.assertEqual(result, 0)
            self.assertFalse(Path(state.paths.portal_routing_conf).exists())
            self.assertFalse(Path(state.paths.portal_wlr_config).exists())
            for key in (
                "portal_bus_script",
                "portal_start_script",
                "portal_bus_address_file",
                "portal_bus_pid_file",
                "portal_ready_file",
                "portal_log_file",
            ):
                self.assertFalse(Path(getattr(state.paths, key)).exists(), key)
            self.assertNotIn("capture", conf_path.read_text(encoding="utf-8"))
            self.assertIn("encoder = nvenc", conf_path.read_text(encoding="utf-8"))

    def test_managed_setup_paths_include_portal_files(self) -> None:
        state = manager._default_state()
        paths = [path.name for path in manager._managed_setup_paths(state)]

        self.assertIn("lutristosunshine-portal-bus.sh", paths)
        self.assertIn("lutristosunshine-start-portal.sh", paths)
        self.assertIn("sway-portals.conf", paths)
        self.assertIn("wlr.portal", paths)
        self.assertIn("config", paths)

    def test_portal_setup_failure_rolls_back_config_pinning(self) -> None:
        with temp_display_state(manager) as (state, base):
            state.capture_method = "portal"
            conf_path = base / "sunshine.conf"
            conf_path.write_text("", encoding="utf-8")
            state.paths.sunshine_conf = str(conf_path)

            with patched(
                display_state,
                load_state=lambda: state,
                save_state=lambda current: None,
            ), patched(
                manager._svc,
                sunshine_unit=lambda: "sunshine.service",
                is_sunshine_service_active=lambda: False,
                remember_sunshine_execstart=lambda current: current,
            ), patched(
                installation,
                resolve_sunshine_config_root=lambda unit: base,
            ), patched(manager, _ensure_dependencies=lambda current=None: []), \
               patched(manager, _daemon_reload=lambda: None), \
               patched(manager.audio_policy, setup=lambda current: None), \
               patched(manager.audio_policy, remove=lambda current: None):
                result = manager.setup_display(
                    install_udev_rule_fn=lambda current: False,
                )

            self.assertEqual(result, 1)
            self.assertNotIn("capture", conf_path.read_text(encoding="utf-8"))
            # A failed setup must not install anything.  temp_display_state
            # pre-creates placeholder files, so the wrapper still carries the
            # placeholder text rather than generated content.
            self.assertEqual(
                Path(state.paths.sunshine_wrapper_script).read_text(encoding="utf-8"),
                "managed\n",
            )
            self.assertFalse(Path(state.paths.sunshine_override).exists())
            assert_state_paths_under(state, base)

    def test_failed_setup_never_writes_outside_a_redirected_install(self) -> None:
        # Regression guard: setup_display used to rebuild every path from the
        # detected unit name, so a redirected install leaked into the real
        # ~/.config/lutristosunshine (it once left the Sunshine unit pointing at
        # scripts that were never written there).
        real_bin = CONFIG_ROOT / "bin"
        before = sorted(p.name for p in real_bin.iterdir()) if real_bin.exists() else None
        with temp_display_state(manager) as (state, base):
            state.enabled = True
            with patched(
                display_state,
                load_state=lambda: state,
                save_state=lambda current: None,
            ), patched(
                manager._svc,
                sunshine_unit=lambda: "sunshine.service",
                is_sunshine_service_active=lambda: False,
                remember_sunshine_execstart=lambda current: current,
            ), patched(
                installation,
                resolve_sunshine_config_root=lambda unit: base,
            ), patched(manager, _ensure_dependencies=lambda current=None: []), \
               patched(manager, _daemon_reload=lambda: None), \
               patched(manager.audio_policy, setup=lambda current: None):
                manager.setup_display(install_udev_rule_fn=lambda current: True)

            assert_state_paths_under(state, base)
            self.assertTrue(
                Path(state.paths.sunshine_override_dir).parent == Path(state.paths.systemd_user_dir),
                "override dir escaped the redirected systemd user dir",
            )
            self.assertTrue(Path(state.paths.sunshine_wrapper_script).exists())

        after = sorted(p.name for p in real_bin.iterdir()) if real_bin.exists() else None
        self.assertEqual(before, after, "the real managed install was touched")


class CaptureMethodCliTests(unittest.TestCase):
    def test_parse_capture_method_command(self) -> None:
        args = lutristosunshine.parse_args(["display", "capture-method", "portal"])

        self.assertEqual(args.display_action, "capture-method")
        self.assertEqual(args.method, "portal")

    def test_handle_capture_method_updates_setting(self) -> None:
        args = lutristosunshine.parse_args(["display", "capture-method", "portal"])
        state = manager._default_state()

        with patched(display_state, load_state=lambda: state), \
             patched(display_state, save_state=lambda current: None), \
             patched(manager, refresh_managed_files=lambda current: current):
            output = io.StringIO()
            with patch("sys.stdout", output):
                result = lutristosunshine.handle_display_command(args)

        self.assertEqual(result, 0)
        self.assertEqual(state.capture_method, "portal")
        self.assertIn("pinned to 'portal'", output.getvalue())

    def test_hub_renders_capture_backend_choice(self) -> None:
        original_snapshot = hub.display_snapshot
        original_blocked = hub.catalog.get_display_blocked_apps
        original_menu = hub.get_menu_choice
        choices = iter(["0"])
        try:
            hub.display_snapshot = lambda: snapshot_fixture(
                configured=True,
                capture_method="portal",
                capture_method_label="[PORTAL] Sunshine captures the headless output through a private portal session",
                capture_method_summary="xdg-desktop-portal + PipeWire (private portal session)",
                portal_bus_active=True,
                portal_names_active=True,
            )
            hub.catalog.get_display_blocked_apps = lambda: ([], None)
            hub.get_menu_choice = lambda prompt, valid: next(choices)
            output = io.StringIO()
            with patch("sys.stdout", output):
                result = lutristosunshine.handle_display_command(
                    lutristosunshine.parse_args(["display"])
                )
        finally:
            hub.display_snapshot = original_snapshot
            hub.catalog.get_display_blocked_apps = original_blocked
            hub.get_menu_choice = original_menu

        rendered = output.getvalue()
        self.assertEqual(result, 0)
        self.assertIn("Choose capture backend (wlr / portal)", rendered)
        self.assertIn("Capture backend:", rendered)


if __name__ == "__main__":
    unittest.main()
