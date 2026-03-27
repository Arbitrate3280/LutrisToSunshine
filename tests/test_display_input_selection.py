import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from display import manager


class DisplayInputSelectionTests(unittest.TestCase):
    def _temp_audio_state(self, config_text: str = "audio_sink = host-speakers\n"):
        tempdir = tempfile.TemporaryDirectory()
        self.addCleanup(tempdir.cleanup)
        conf_path = Path(tempdir.name) / "sunshine.conf"
        conf_path.write_text(config_text, encoding="utf-8")

        state = manager._default_state()
        state["enabled"] = True
        state["paths"] = dict(state["paths"])
        state["paths"]["sunshine_conf"] = str(conf_path)
        return state, conf_path

    def test_selection_id_is_stable(self) -> None:
        fingerprint = {
            "by_id": "/dev/input/by-id/bluetooth-Sony_Controller-event-joystick",
            "uniq": "aa:bb:cc:dd",
            "phys": "usb-0000:00:00.0-1/input0",
            "vendor_id": "054c",
            "product_id": "09cc",
            "name": "Wireless Controller",
        }
        self.assertEqual(
            manager._selection_id_from_fingerprint(fingerprint),
            manager._selection_id_from_fingerprint(dict(fingerprint)),
        )

    def test_normalized_selection_entry_backfills_missing_fields(self) -> None:
        normalized = manager._normalized_selection_entry(
            {
                "label": "",
                "fingerprint": {
                    "vendor_id": "054C",
                    "product_id": "09CC",
                    "name": "Wireless Controller",
                },
            }
        )

        self.assertEqual(normalized["label"], "Wireless Controller")
        self.assertEqual(normalized["fingerprint"]["vendor_id"], "054c")
        self.assertEqual(normalized["fingerprint"]["product_id"], "09cc")
        self.assertTrue(normalized["selection_id"])

    def test_selection_matches_by_id_first(self) -> None:
        selection = manager._normalized_selection_entry(
            {
                "label": "DualShock",
                "fingerprint": {
                    "by_id": "/dev/input/by-id/bluetooth-Sony_Controller-event-joystick",
                    "uniq": "aa:bb:cc:dd",
                    "phys": "ignored",
                    "vendor_id": "054c",
                    "product_id": "09cc",
                    "name": "Wireless Controller",
                },
            }
        )
        device = {
            "fingerprint": {
                "by_id": "/dev/input/by-id/bluetooth-Sony_Controller-event-joystick",
                "uniq": "different",
                "phys": "different",
                "vendor_id": "054c",
                "product_id": "09cc",
                "name": "Wireless Controller",
            }
        }

        self.assertTrue(manager._selection_matches_device(selection, device))

    def test_parse_selection_numbers_supports_ranges(self) -> None:
        parsed = manager._parse_selection_numbers("1,3-4", 4)
        self.assertEqual(parsed, [0, 2, 3])

    def test_parse_selection_toggle_numbers_keeps_existing_selection_on_blank(self) -> None:
        self.assertIsNone(manager._parse_selection_toggle_numbers("", 3))

    def test_toggle_selection_entries_adds_new_device_without_replacing_existing(self) -> None:
        wireless = {
            "selection_id": "wireless-controller",
            "label": "Wireless Controller",
            "fingerprint": {
                "by_id": "/dev/input/by-id/bluetooth-Sony_Controller-event-joystick",
                "uniq": "aa:bb:cc:dd",
                "phys": "usb-0000:00:00.0-1/input0",
                "vendor_id": "054c",
                "product_id": "09cc",
                "name": "Wireless Controller",
            },
        }
        bitdo = {
            "selection_id": "8bitdo-sn30-pro",
            "label": "8Bitdo SN30 Pro",
            "fingerprint": {
                "by_id": "/dev/input/by-id/bluetooth-8Bitdo_SN30_Pro-event-joystick",
                "uniq": "78:46:5c:ae:56:64",
                "phys": "usb-0000:02:00.0-6/input0",
                "vendor_id": "2dc8",
                "product_id": "6101",
                "name": "8Bitdo SN30 Pro",
            },
        }

        updated = manager._toggle_selection_entries([wireless], [bitdo, wireless], [0])

        self.assertEqual([entry["label"] for entry in updated], ["Wireless Controller", "8Bitdo SN30 Pro"])

    def test_toggle_selection_entries_removes_selected_device(self) -> None:
        wireless = {
            "selection_id": "wireless-controller",
            "label": "Wireless Controller",
            "fingerprint": {
                "by_id": "/dev/input/by-id/bluetooth-Sony_Controller-event-joystick",
                "uniq": "aa:bb:cc:dd",
                "phys": "usb-0000:00:00.0-1/input0",
                "vendor_id": "054c",
                "product_id": "09cc",
                "name": "Wireless Controller",
            },
        }

        updated = manager._toggle_selection_entries([wireless], [wireless], [0])

        self.assertEqual(updated, [])

    def test_bridge_phys_prefix_detection(self) -> None:
        self.assertTrue(manager._is_bridge_input_phys("lts-inputbridge/controller-1"))
        self.assertFalse(manager._is_bridge_input_phys("usb-0000:02:00.0-6/input0"))

    def test_udev_rule_does_not_match_bridge_phys_prefix(self) -> None:
        rule = manager._udev_rule()
        self.assertNotIn('ATTRS{phys}=="lts-inputbridge/*"', rule)

    def test_udev_rule_grants_uhid_access(self) -> None:
        rule = manager._udev_rule()
        self.assertIn('KERNEL=="uhid"', rule)
        self.assertIn('SUBSYSTEM=="misc"', rule)

    def test_udev_rule_grants_current_user_access_to_sunshine_inputs(self) -> None:
        original_user = manager.current_user_name
        original_group = manager.current_user_group
        try:
            manager.current_user_name = lambda: "alice"
            manager.current_user_group = lambda: "streaming"
            rule = manager._udev_rule("permissions-only")
        finally:
            manager.current_user_name = original_user
            manager.current_user_group = original_group
        self.assertIn('OWNER="alice"', rule)
        self.assertIn('GROUP="streaming"', rule)
        self.assertIn('MODE="0660"', rule)

    def test_udev_rule_preserves_input_classification_outside_plasma(self) -> None:
        rule = manager._udev_rule("permissions-only")
        self.assertNotIn('ENV{ID_INPUT}=""', rule)
        self.assertNotIn('ENV{ID_INPUT_KEYBOARD}=""', rule)
        self.assertNotIn('ENV{ID_INPUT_MOUSE}=""', rule)
        self.assertNotIn('ENV{ID_INPUT_TOUCHPAD}=""', rule)

    def test_udev_rule_preserves_input_classification_on_plasma(self) -> None:
        rule = manager._udev_rule("kwin-runtime-disable")
        self.assertNotIn('ENV{ID_INPUT}=""', rule)
        self.assertNotIn('ENV{ID_INPUT_KEYBOARD}=""', rule)
        self.assertNotIn('ENV{ID_INPUT_MOUSE}=""', rule)
        self.assertNotIn('ENV{ID_INPUT_TOUCHPAD}=""', rule)

    def test_input_isolation_mode_detects_plasma(self) -> None:
        with patch.dict(
            manager.os.environ,
            {
                "XDG_CURRENT_DESKTOP": "KDE",
                "XDG_SESSION_DESKTOP": "KDE",
                "DESKTOP_SESSION": "plasma",
                "KDE_FULL_SESSION": "true",
            },
            clear=False,
        ):
            self.assertEqual(manager._input_isolation_mode(), "kwin-runtime-disable")

    def test_kwin_input_isolation_status_defaults_when_missing(self) -> None:
        state = manager._default_state()
        state["paths"] = dict(state["paths"])
        state["paths"]["kwin_input_isolation_status_file"] = str(Path(tempfile.mkdtemp()) / "missing-status.json")
        status = manager._kwin_input_isolation_status(state)
        self.assertEqual(status["state"], "inactive")
        self.assertEqual(status["disabled_devices"], [])
        self.assertEqual(status["failed_devices"], [])
        self.assertEqual(status["last_error"], "")

    def test_kwin_input_isolation_script_bakes_host_session_family(self) -> None:
        with patch.dict(
            manager.os.environ,
            {
                "XDG_CURRENT_DESKTOP": "KDE",
                "DESKTOP_SESSION": "plasma",
                "KDE_FULL_SESSION": "true",
            },
            clear=False,
        ):
            script = manager._kwin_input_isolation_script(manager._default_state())
        self.assertIn("HOST_SESSION_FAMILY = 'plasma'", script)
        self.assertIn('write_status(state="starting")', script)
        self.assertIn('DEVICE_INTERFACE,\n        "enabled",', script)
        self.assertIn('return f"{value:04x}"', script)
        self.assertIn('replace("_", " ")', script)

    def test_sunshine_unit_falls_back_to_legacy_name_when_preferred_unit_missing(self) -> None:
        original_systemctl_user = manager._systemctl_user
        try:
            def fake_systemctl_user(*args, check=False):
                unit = args[-1]
                if args[:3] == ("show", "--property=LoadState", "--value"):
                    if unit == manager.SUNSHINE_UNIT:
                        return subprocess.CompletedProcess(list(args), 1, "", "missing")
                    if unit == manager.FALLBACK_SUNSHINE_UNIT:
                        return subprocess.CompletedProcess(list(args), 0, "loaded\n", "")
                return subprocess.CompletedProcess(list(args), 0, "", "")

            manager._systemctl_user = fake_systemctl_user
            self.assertEqual(manager._sunshine_unit(), manager.FALLBACK_SUNSHINE_UNIT)
        finally:
            manager._systemctl_user = original_systemctl_user

    def test_sunshine_unit_prefers_canonical_active_unit_id_over_alias(self) -> None:
        original_systemctl_user = manager._systemctl_user
        try:
            def fake_systemctl_user(*args, check=False):
                unit = args[-1]
                if args[:3] == ("show", "--property=LoadState", "--value"):
                    return subprocess.CompletedProcess(list(args), 0, "loaded\n", "")
                if args[:3] == ("show", "--property=Id", "--value"):
                    if unit == manager.FALLBACK_SUNSHINE_UNIT:
                        return subprocess.CompletedProcess(list(args), 0, f"{manager.SUNSHINE_UNIT}\n", "")
                    return subprocess.CompletedProcess(list(args), 0, f"{unit}\n", "")
                if args[:1] == ("is-active",):
                    return subprocess.CompletedProcess(list(args), 0, "active\n", "")
                return subprocess.CompletedProcess(list(args), 0, "", "")

            manager._systemctl_user = fake_systemctl_user
            self.assertEqual(manager._sunshine_unit(), manager.SUNSHINE_UNIT)
        finally:
            manager._systemctl_user = original_systemctl_user

    def test_sunshine_virtual_input_devices_detects_beef_dead_entries(self) -> None:
        input_listing = """
I: Bus=0003 Vendor=beef Product=dead Version=0111
N: Name="Mouse passthrough"
H: Handlers=mouse2 event27

I: Bus=0003 Vendor=1234 Product=5678 Version=0001
N: Name="Other device"
H: Handlers=kbd event2

I: Bus=0003 Vendor=beef Product=dead Version=0111
N: Name="Keyboard passthrough"
H: Handlers=sysrq kbd event29
"""
        with patch.object(manager.Path, "read_text", return_value=input_listing):
            devices = manager._sunshine_virtual_input_devices()
        self.assertEqual(
            devices,
            [
                {"name": "Mouse passthrough", "event_path": "/dev/input/event27"},
                {"name": "Keyboard passthrough", "event_path": "/dev/input/event29"},
            ],
        )

    def test_ensure_dependencies_requires_setfacl(self) -> None:
        original = manager.shutil.which
        try:
            manager.shutil.which = lambda name: None if name == "setfacl" else "/usr/bin/fake"
            missing = manager._ensure_dependencies()
        finally:
            manager.shutil.which = original
        self.assertIn("setfacl", missing)

    def test_udev_rule_no_longer_grants_bridged_hidraw_access_by_phys(self) -> None:
        rule = manager._udev_rule()
        self.assertNotIn('KERNEL=="hidraw*"', rule)
        self.assertNotIn('SUBSYSTEM=="hidraw"', rule)

    def test_sunshine_audio_capture_target_uses_sink_name(self) -> None:
        state = manager._default_state()
        self.assertEqual(
            manager._sunshine_audio_capture_target(state),
            "lts-sunshine-stereo",
        )

    def test_setup_tracks_original_sunshine_audio_sink_without_overwriting_it(self) -> None:
        state, conf_path = self._temp_audio_state()
        original_ensure_dependencies = manager._ensure_dependencies
        original_load_state = manager.load_state
        original_sunshine_service_active = manager._sunshine_service_active
        original_refresh_managed_files = manager.refresh_managed_files
        original_save_state = manager.save_state
        original_install_udev_rule = manager._install_udev_rule
        original_cleanup_legacy_display_units = manager._cleanup_legacy_display_units
        original_daemon_reload = manager._daemon_reload
        try:
            manager._ensure_dependencies = lambda: []
            manager.load_state = lambda: state
            manager._sunshine_service_active = lambda: False
            manager.refresh_managed_files = lambda current=None: current if current is not None else state
            manager.save_state = lambda current: None
            manager._install_udev_rule = lambda current: True
            manager._cleanup_legacy_display_units = lambda current: None
            manager._daemon_reload = lambda: None

            result = manager.setup_display()
        finally:
            manager._ensure_dependencies = original_ensure_dependencies
            manager.load_state = original_load_state
            manager._sunshine_service_active = original_sunshine_service_active
            manager.refresh_managed_files = original_refresh_managed_files
            manager.save_state = original_save_state
            manager._install_udev_rule = original_install_udev_rule
            manager._cleanup_legacy_display_units = original_cleanup_legacy_display_units
            manager._daemon_reload = original_daemon_reload

        self.assertEqual(result, 0)
        self.assertEqual(
            state["sunshine_audio_sink"],
            {"present": True, "value": "host-speakers"},
        )
        self.assertEqual(conf_path.read_text(encoding="utf-8"), "audio_sink = host-speakers\n")

    def test_runtime_audio_sink_uses_sink_name_and_restores_original(self) -> None:
        state, conf_path = self._temp_audio_state()
        manager._set_runtime_sunshine_audio_sink(state)
        self.assertEqual(
            conf_path.read_text(encoding="utf-8"),
            "audio_sink = lts-sunshine-stereo\n",
        )

        manager._restore_sunshine_audio_sink(state)
        self.assertEqual(conf_path.read_text(encoding="utf-8"), "audio_sink = host-speakers\n")

    def test_snapshot_host_audio_defaults_ignores_managed_defaults(self) -> None:
        state = manager._default_state()
        original_pactl_info_value = manager._pactl_info_value
        try:
            values = {
                "Default Sink": "lts-sunshine-stereo",
                "Default Source": "lts-sunshine-stereo.monitor",
            }
            manager._pactl_info_value = lambda key: values.get(key, "")
            manager._snapshot_host_audio_defaults(state)
        finally:
            manager._pactl_info_value = original_pactl_info_value

        self.assertEqual(state["host_audio_defaults"], {"sink": "", "source": ""})

    def test_start_display_snapshots_host_audio_defaults_before_service_start(self) -> None:
        state, _conf_path = self._temp_audio_state()
        original_load_state = manager.load_state
        original_refresh_managed_files = manager.refresh_managed_files
        original_save_state = manager.save_state
        original_snapshot_host_audio_defaults = manager._snapshot_host_audio_defaults
        original_systemctl_user = manager._systemctl_user
        try:
            manager.load_state = lambda: state
            manager.refresh_managed_files = lambda current=None: current if current is not None else state
            manager.save_state = lambda current: None
            manager._snapshot_host_audio_defaults = lambda current: current.update(
                {"host_audio_defaults": {"sink": "host-sink", "source": "host-source"}}
            )
            manager._systemctl_user = lambda *args, check=False: subprocess.CompletedProcess(list(args), 0, "", "")

            result = manager.start_display()
        finally:
            manager.load_state = original_load_state
            manager.refresh_managed_files = original_refresh_managed_files
            manager.save_state = original_save_state
            manager._snapshot_host_audio_defaults = original_snapshot_host_audio_defaults
            manager._systemctl_user = original_systemctl_user

        self.assertEqual(result, 0)
        self.assertEqual(state["host_audio_defaults"], {"sink": "host-sink", "source": "host-source"})

    def test_start_display_restores_audio_on_sunshine_start_failure(self) -> None:
        state, conf_path = self._temp_audio_state()
        original_load_state = manager.load_state
        original_refresh_managed_files = manager.refresh_managed_files
        original_save_state = manager.save_state
        original_bridge_runtime_enabled = manager._bridge_runtime_enabled
        original_snapshot_host_audio_defaults = manager._snapshot_host_audio_defaults
        original_restore_host_audio_defaults = manager._restore_host_audio_defaults
        original_systemctl_user = manager._systemctl_user
        try:
            manager.load_state = lambda: state
            manager.refresh_managed_files = lambda current=None: current if current is not None else state
            manager.save_state = lambda current: None
            manager._bridge_runtime_enabled = lambda current: False
            manager._snapshot_host_audio_defaults = lambda current: current.update(
                {"host_audio_defaults": {"sink": "host-sink", "source": "host-source"}}
            )
            manager._restore_host_audio_defaults = lambda current: current.update(
                {"host_audio_defaults": {"sink": "", "source": ""}}
            )

            def fake_systemctl_user(*args, check=False):
                action = args[0]
                unit = args[-1]
                if action == "start" and unit == manager.SUNSHINE_UNIT:
                    return subprocess.CompletedProcess(list(args), 1, "", "boom")
                if args[:3] == ("show", "--property=LoadState", "--value") and unit == manager.SUNSHINE_UNIT:
                    return subprocess.CompletedProcess(list(args), 0, "loaded\n", "")
                return subprocess.CompletedProcess(list(args), 0, "", "")

            manager._systemctl_user = fake_systemctl_user

            result = manager.start_display()
        finally:
            manager.load_state = original_load_state
            manager.refresh_managed_files = original_refresh_managed_files
            manager.save_state = original_save_state
            manager._bridge_runtime_enabled = original_bridge_runtime_enabled
            manager._snapshot_host_audio_defaults = original_snapshot_host_audio_defaults
            manager._restore_host_audio_defaults = original_restore_host_audio_defaults
            manager._systemctl_user = original_systemctl_user

        self.assertEqual(result, 1)
        self.assertEqual(conf_path.read_text(encoding="utf-8"), "audio_sink = host-speakers\n")
        self.assertEqual(state["host_audio_defaults"], {"sink": "", "source": ""})

    def test_stop_display_restores_original_audio_target(self) -> None:
        state, conf_path = self._temp_audio_state("audio_sink = lts-sunshine-stereo\n")
        state["sunshine_audio_sink"] = {"present": True, "value": "host-speakers"}
        original_load_state = manager.load_state
        original_save_state = manager.save_state
        original_systemctl_user = manager._systemctl_user
        original_clear_input_bridge_status_file = manager._clear_input_bridge_status_file
        original_restore_host_audio_defaults = manager._restore_host_audio_defaults
        try:
            manager.load_state = lambda: state
            manager.save_state = lambda current: None
            manager._systemctl_user = lambda *args, check=False: subprocess.CompletedProcess(list(args), 0, "", "")
            manager._clear_input_bridge_status_file = lambda current: None
            manager._restore_host_audio_defaults = lambda current: None

            result = manager.stop_display()
        finally:
            manager.load_state = original_load_state
            manager.save_state = original_save_state
            manager._systemctl_user = original_systemctl_user
            manager._clear_input_bridge_status_file = original_clear_input_bridge_status_file
            manager._restore_host_audio_defaults = original_restore_host_audio_defaults

        self.assertEqual(result, 0)
        self.assertEqual(conf_path.read_text(encoding="utf-8"), "audio_sink = host-speakers\n")

    def test_remove_display_deletes_override_files(self) -> None:
        state = manager._default_state()
        state["enabled"] = True
        tempdir = tempfile.TemporaryDirectory()
        self.addCleanup(tempdir.cleanup)
        base = Path(tempdir.name)
        override_dir = base / f"{manager.SUNSHINE_UNIT}.d"
        override_dir.mkdir(parents=True)

        state["paths"] = dict(state["paths"])
        state["paths"]["sunshine_override_dir"] = str(override_dir)
        state["paths"]["sunshine_override"] = str(override_dir / "override.conf")
        state["paths"]["input_bridge_script"] = str(base / "lutristosunshine-input-bridge.py")
        state["paths"]["kwin_input_isolation_script"] = str(base / "lutristosunshine-kwin-input-isolation.py")
        state["paths"]["audio_guard_script"] = str(base / "lutristosunshine-guard-audio-defaults.sh")
        state["paths"]["sunshine_wrapper_script"] = str(base / "lutristosunshine-run-display-service.sh")
        state["paths"]["portal_active_file"] = str(base / "portal-active")
        state["paths"]["portal_lock_file"] = str(base / "portal-lock")
        state["paths"]["input_bridge_status_file"] = str(base / "input-bridge-status.json")
        state["paths"]["kwin_input_isolation_status_file"] = str(base / "kwin-input-isolation-status.json")
        state["paths"]["wayland_display_file"] = str(base / "wayland-display")
        state["paths"]["audio_module_file"] = str(base / "audio-module-id")

        for key in [
            "sunshine_override",
            "input_bridge_script",
            "kwin_input_isolation_script",
            "audio_guard_script",
            "sunshine_wrapper_script",
            "portal_active_file",
            "portal_lock_file",
            "input_bridge_status_file",
            "kwin_input_isolation_status_file",
            "wayland_display_file",
            "audio_module_file",
        ]:
            Path(state["paths"][key]).write_text("managed\n", encoding="utf-8")

        original_load_state = manager.load_state
        original_save_state = manager.save_state
        original_stop_display = manager.stop_display
        original_remove_udev_rule = manager._remove_udev_rule
        original_cleanup_legacy_display_units = manager._cleanup_legacy_display_units
        original_daemon_reload = manager._daemon_reload
        cleanup_calls = []
        try:
            manager.load_state = lambda: state
            manager.save_state = lambda current: None
            manager.stop_display = lambda: 0
            manager._remove_udev_rule = lambda current: True
            manager._cleanup_legacy_display_units = lambda current: cleanup_calls.append(True)
            manager._daemon_reload = lambda: None

            result = manager.remove_display()
        finally:
            manager.load_state = original_load_state
            manager.save_state = original_save_state
            manager.stop_display = original_stop_display
            manager._remove_udev_rule = original_remove_udev_rule
            manager._cleanup_legacy_display_units = original_cleanup_legacy_display_units
            manager._daemon_reload = original_daemon_reload

        self.assertEqual(result, 0)
        self.assertEqual(cleanup_calls, [True])
        self.assertFalse(Path(state["paths"]["sunshine_override"]).exists())
        self.assertFalse(Path(state["paths"]["sunshine_wrapper_script"]).exists())
        self.assertFalse(override_dir.exists())

    def test_display_snapshot_not_configured_has_enable_next_step(self) -> None:
        state = manager._default_state()
        original_load_state = manager.load_state
        original_ensure_dependencies = manager._ensure_dependencies
        try:
            manager.load_state = lambda: state
            manager._ensure_dependencies = lambda: []
            snapshot = manager.display_snapshot()
        finally:
            manager.load_state = original_load_state
            manager._ensure_dependencies = original_ensure_dependencies

        self.assertFalse(snapshot["configured"])
        self.assertIn("display enable", snapshot["next_step"])
        self.assertEqual(snapshot["current_headless_mode"], "")
        self.assertEqual(snapshot["refresh_rate_sync_mode"], "client")
        self.assertEqual(snapshot["custom_display_mode"]["width"], manager.FALLBACK_WIDTH)
        self.assertEqual(snapshot["custom_display_mode"]["height"], manager.FALLBACK_HEIGHT)
        self.assertEqual(snapshot["custom_display_mode"]["refresh"], float(manager.FALLBACK_FPS))
        self.assertEqual(snapshot["current_mangohud_config"], "")

    def test_display_snapshot_reads_active_mangohud_config(self) -> None:
        tempdir = tempfile.TemporaryDirectory()
        self.addCleanup(tempdir.cleanup)
        state = manager._default_state()
        state["paths"] = dict(state["paths"])
        state["paths"]["portal_active_file"] = str(Path(tempdir.name) / "portal-active")

        Path(state["paths"]["portal_active_file"]).write_text(
            "phase=running\nmangohud_config=read_cfg,fps_limit=59.94\n",
            encoding="utf-8",
        )

        original_load_state = manager.load_state
        original_ensure_dependencies = manager._ensure_dependencies
        try:
            manager.load_state = lambda: state
            manager._ensure_dependencies = lambda: []
            snapshot = manager.display_snapshot()
        finally:
            manager.load_state = original_load_state
            manager._ensure_dependencies = original_ensure_dependencies

        self.assertEqual(snapshot["current_mangohud_config"], "read_cfg,fps_limit=59.94")

    def test_current_headless_mode_formats_refresh_in_millihz(self) -> None:
        state = manager._default_state()
        state["enabled"] = True
        state["sway_socket"] = "/tmp/lts-sway.sock"

        original_run = manager._run
        original_path_exists = manager.Path.exists
        try:
            manager._run = lambda command, **kwargs: subprocess.CompletedProcess(
                command,
                0,
                stdout=json.dumps(
                    [
                        {
                            "name": "HEADLESS-1",
                            "current_mode": {
                                "width": 2560,
                                "height": 1440,
                                "refresh": 119987,
                            },
                        }
                    ]
                ),
                stderr="",
            )
            manager.Path.exists = lambda path: str(path) == state["sway_socket"]

            mode = manager._current_headless_mode(state, sunshine_active=True, sway_active=True)
        finally:
            manager._run = original_run
            manager.Path.exists = original_path_exists

        self.assertEqual(mode, "2560x1440 @ 119.99 Hz")

    def test_current_headless_mode_returns_empty_when_headless_output_missing(self) -> None:
        state = manager._default_state()
        state["enabled"] = True
        state["sway_socket"] = "/tmp/lts-sway.sock"

        original_run = manager._run
        original_path_exists = manager.Path.exists
        try:
            manager._run = lambda command, **kwargs: subprocess.CompletedProcess(
                command,
                0,
                stdout=json.dumps([{"name": "HDMI-A-1", "current_mode": {"width": 1920, "height": 1080, "refresh": 60000}}]),
                stderr="",
            )
            manager.Path.exists = lambda path: str(path) == state["sway_socket"]

            mode = manager._current_headless_mode(state, sunshine_active=True, sway_active=True)
        finally:
            manager._run = original_run
            manager.Path.exists = original_path_exists

        self.assertEqual(mode, "")

    def test_current_headless_mode_returns_empty_when_swaymsg_fails(self) -> None:
        state = manager._default_state()
        state["enabled"] = True
        state["sway_socket"] = "/tmp/lts-sway.sock"

        original_run = manager._run
        original_path_exists = manager.Path.exists
        try:
            manager._run = lambda command, **kwargs: subprocess.CompletedProcess(
                command,
                1,
                stdout="",
                stderr="failed",
            )
            manager.Path.exists = lambda path: str(path) == state["sway_socket"]

            mode = manager._current_headless_mode(state, sunshine_active=True, sway_active=True)
        finally:
            manager._run = original_run
            manager.Path.exists = original_path_exists

        self.assertEqual(mode, "")

    def test_display_doctor_report_flags_missing_dependencies(self) -> None:
        state = manager._default_state()
        original_load_state = manager.load_state
        original_ensure_dependencies = manager._ensure_dependencies
        try:
            manager.load_state = lambda: state
            manager._ensure_dependencies = lambda: ["sway", "setfacl"]
            report = manager.display_doctor_report()
        finally:
            manager.load_state = original_load_state
            manager._ensure_dependencies = original_ensure_dependencies

        self.assertEqual(report["summary"], "needs_attention")
        self.assertTrue(any(check["status"] == "fail" for check in report["checks"]))

    def test_display_doctor_report_reports_kwin_runtime_state(self) -> None:
        tempdir = tempfile.TemporaryDirectory()
        self.addCleanup(tempdir.cleanup)
        base = Path(tempdir.name)
        rule_path = base / "85-lutristosunshine-sunshine-input.rules"
        rule_path.write_text(manager._udev_rule("permissions-only"), encoding="utf-8")
        kwin_status_path = base / "kwin-input-isolation-status.json"
        kwin_status_path.write_text(
            json.dumps(
                {
                    "state": "active",
                    "service": "org.kde.KWin",
                    "disabled_devices": [{"path": "/org/kde/KWin/InputDevice/event30", "name": "Keyboard_passthrough"}],
                    "seen_device_count": 1,
                    "last_error": "",
                }
            ),
            encoding="utf-8",
        )

        state = manager._default_state()
        state["enabled"] = True
        state["udev_rule_path"] = str(rule_path)
        state["paths"] = dict(state["paths"])
        state["paths"]["kwin_input_isolation_status_file"] = str(kwin_status_path)

        original_load_state = manager.load_state
        original_ensure_dependencies = manager._ensure_dependencies
        original_sunshine_service_active = manager._sunshine_service_active
        original_bridge_service_state = manager._bridge_service_state
        original_sunshine_virtual_input_devices = manager._sunshine_virtual_input_devices
        try:
            manager.load_state = lambda: state
            manager._ensure_dependencies = lambda: []
            manager._sunshine_service_active = lambda: False
            manager._bridge_service_state = lambda: "inactive"
            manager._sunshine_virtual_input_devices = lambda: [{"name": "Keyboard passthrough", "event_path": "/dev/input/event29"}]
            with patch.dict(
                manager.os.environ,
                {
                    "XDG_CURRENT_DESKTOP": "KDE",
                    "XDG_SESSION_DESKTOP": "KDE",
                    "DESKTOP_SESSION": "plasma",
                    "KDE_FULL_SESSION": "true",
                },
                clear=False,
            ):
                report = manager.display_doctor_report()
        finally:
            manager.load_state = original_load_state
            manager._ensure_dependencies = original_ensure_dependencies
            manager._sunshine_service_active = original_sunshine_service_active
            manager._bridge_service_state = original_bridge_service_state
            manager._sunshine_virtual_input_devices = original_sunshine_virtual_input_devices

        self.assertEqual(report["summary"], "degraded")
        kwin_check = next(check for check in report["checks"] if check["label"] == "KWin isolation")
        self.assertEqual(kwin_check["status"], "pass")
        self.assertIn("disabled 1 of 1 Sunshine input device", kwin_check["message"])

    def test_display_doctor_report_warns_when_host_has_sunshine_inputs_but_kwin_disabled_zero(self) -> None:
        tempdir = tempfile.TemporaryDirectory()
        self.addCleanup(tempdir.cleanup)
        base = Path(tempdir.name)
        rule_path = base / "85-lutristosunshine-sunshine-input.rules"
        rule_path.write_text(manager._udev_rule("permissions-only"), encoding="utf-8")
        kwin_status_path = base / "kwin-input-isolation-status.json"
        kwin_status_path.write_text(
            json.dumps(
                {
                    "state": "active",
                    "service": "org.kde.KWin",
                    "disabled_devices": [],
                    "failed_devices": [],
                    "seen_device_count": 5,
                    "last_error": "",
                }
            ),
            encoding="utf-8",
        )

        state = manager._default_state()
        state["enabled"] = True
        state["udev_rule_path"] = str(rule_path)
        state["paths"] = dict(state["paths"])
        state["paths"]["kwin_input_isolation_status_file"] = str(kwin_status_path)

        original_load_state = manager.load_state
        original_ensure_dependencies = manager._ensure_dependencies
        original_sunshine_service_active = manager._sunshine_service_active
        original_bridge_service_state = manager._bridge_service_state
        original_sunshine_virtual_input_devices = manager._sunshine_virtual_input_devices
        try:
            manager.load_state = lambda: state
            manager._ensure_dependencies = lambda: []
            manager._sunshine_service_active = lambda: False
            manager._bridge_service_state = lambda: "inactive"
            manager._sunshine_virtual_input_devices = lambda: [
                {"name": "Mouse passthrough", "event_path": "/dev/input/event27"},
                {"name": "Mouse passthrough (absolute)", "event_path": "/dev/input/event28"},
                {"name": "Keyboard passthrough", "event_path": "/dev/input/event29"},
                {"name": "Touch passthrough", "event_path": "/dev/input/event30"},
                {"name": "Pen passthrough", "event_path": "/dev/input/event31"},
            ]
            with patch.dict(
                manager.os.environ,
                {
                    "XDG_CURRENT_DESKTOP": "KDE",
                    "XDG_SESSION_DESKTOP": "KDE",
                    "DESKTOP_SESSION": "plasma",
                    "KDE_FULL_SESSION": "true",
                },
                clear=False,
            ):
                report = manager.display_doctor_report()
        finally:
            manager.load_state = original_load_state
            manager._ensure_dependencies = original_ensure_dependencies
            manager._sunshine_service_active = original_sunshine_service_active
            manager._bridge_service_state = original_bridge_service_state
            manager._sunshine_virtual_input_devices = original_sunshine_virtual_input_devices

        kwin_check = next(check for check in report["checks"] if check["label"] == "KWin isolation")
        self.assertEqual(kwin_check["status"], "warn")
        self.assertIn("matched 5 of 5 host Sunshine input device(s), but disabled 0", kwin_check["message"])

    def test_managed_sunshine_templates_do_not_force_global_pulse_sink(self) -> None:
        state = manager._default_state()
        scripts = manager._script_templates(state)
        units = manager._systemd_templates(state)

        sway_start = scripts[Path(state["paths"]["sway_start_script"])]
        sunshine_start = scripts[Path(state["paths"]["sunshine_start_script"])]
        sunshine_wrapper = scripts[Path(state["paths"]["sunshine_wrapper_script"])]
        audio_create = scripts[Path(state["paths"]["audio_create_script"])]
        audio_cleanup = scripts[Path(state["paths"]["audio_cleanup_script"])]
        audio_guard = scripts[Path(state["paths"]["audio_guard_script"])]
        launch_script = scripts[Path(state["paths"]["launch_app_script"])]
        sunshine_override = units[Path(state["paths"]["sunshine_override"])]

        self.assertNotIn('PULSE_SINK="lts-sunshine-stereo"', sway_start)
        self.assertIn("sleep 0.1", sway_start)
        self.assertNotIn('PULSE_SINK="lts-sunshine-stereo"', sunshine_start)
        self.assertIn('audio_sink = {managed_sink}', sunshine_wrapper)
        self.assertIn('export XDG_RUNTIME_DIR="$runtime_dir"', sunshine_wrapper)
        self.assertIn('export DBUS_SESSION_BUS_ADDRESS="$dbus_value"', sunshine_wrapper)
        self.assertIn('env=pactl_env()', sunshine_wrapper)
        self.assertIn('run_audio_command() {', audio_create)
        self.assertIn('local command=(/usr/bin/env', audio_create)
        self.assertIn('command+=("PULSE_SERVER=$pulse_server_value")', audio_create)
        self.assertIn('command+=("PULSE_CLIENTCONFIG=$pulse_clientconfig_value")', audio_create)
        self.assertIn('run_audio_command pactl list short sinks', audio_create)
        self.assertIn('run_audio_command pactl load-module', audio_create)
        self.assertIn('run_audio_command() {', audio_cleanup)
        self.assertIn('local command=(/usr/bin/env', audio_cleanup)
        self.assertIn('run_audio_command pactl unload-module', audio_cleanup)
        self.assertIn("sink-sunshine-stereo", audio_guard)
        self.assertIn("sink-sunshine-surround51", audio_guard)
        self.assertIn("sink-sunshine-surround71", audio_guard)
        self.assertIn('poll_interval="0.5"', audio_guard)
        self.assertIn('run_audio_command() {', audio_guard)
        self.assertIn('local command=(/usr/bin/env', audio_guard)
        self.assertIn('"DBUS_SESSION_BUS_ADDRESS=$dbus_value"', audio_guard)
        self.assertIn('command+=("PULSE_SERVER=$pulse_server_value")', audio_guard)
        self.assertIn('command+=("PULSE_CLIENTCONFIG=$pulse_clientconfig_value")', audio_guard)
        self.assertIn("enforce_host_defaults", audio_guard)
        self.assertIn("while true; do", audio_guard)
        self.assertIn("run_audio_command pactl info", audio_guard)
        self.assertIn("run_audio_command pactl set-default-sink", audio_guard)
        self.assertIn("run_audio_command pactl set-default-source", audio_guard)
        self.assertNotIn("pactl subscribe", audio_guard)
        self.assertIn('PULSE_SINK="lts-sunshine-stereo"', launch_script)
        self.assertNotIn("MANGOHUD_CONFIG", launch_script)
        self.assertIn("ExecStart=", sunshine_override)
        self.assertIn(state["paths"]["sunshine_wrapper_script"], sunshine_override)
        self.assertNotIn("Environment=PULSE_SINK=", sunshine_override)

    def test_launch_script_can_inject_dynamic_mangohud_fps_limit(self) -> None:
        state = manager._default_state()
        state["dynamic_mangohud_fps_limit"] = True

        scripts = manager._script_templates(state)
        launch_script = scripts[Path(state["paths"]["launch_app_script"])]

        self.assertIn('mangohud_config_value=""', launch_script)
        self.assertNotIn('local mangohud_config_value=""', launch_script)
        self.assertIn(
            f'resolved_stream_fps="$("{state["paths"]["resolve_stream_fps_script"]}" "client" fallback)"',
            launch_script,
        )
        self.assertIn('mangohud_config_value="read_cfg,fps_limit=$resolved_stream_fps"', launch_script)
        self.assertIn('launch_command+=("MANGOHUD_CONFIG=$mangohud_config_value")', launch_script)

    def test_launch_script_waits_for_exact_stream_fps_before_launch(self) -> None:
        state = manager._default_state()
        state["dynamic_mangohud_fps_limit"] = True
        state["refresh_rate_sync_mode"] = "exact"

        scripts = manager._script_templates(state)
        launch_script = scripts[Path(state["paths"]["launch_app_script"])]

        self.assertIn(
            f'resolved_stream_fps="$("{state["paths"]["resolve_stream_fps_script"]}" "exact" fallback)"',
            launch_script,
        )
        self.assertNotIn('stream_sync_since', launch_script)
        self.assertIn('mangohud_config_value=""', launch_script)
        self.assertNotIn('local mangohud_config_value=""', launch_script)

    def test_set_resolution_script_uses_resolved_stream_fps(self) -> None:
        state = manager._default_state()
        state["refresh_rate_sync_mode"] = "exact"

        scripts = manager._script_templates(state)
        set_resolution_script = scripts[Path(state["paths"]["set_resolution_script"])]

        self.assertIn(
            'swaymsg "output HEADLESS-1 mode ${target_width}x${target_height}@${target_fps}Hz"',
            set_resolution_script,
        )
        self.assertIn('target_width="${SUNSHINE_CLIENT_WIDTH:-}"', set_resolution_script)
        self.assertIn('target_height="${SUNSHINE_CLIENT_HEIGHT:-}"', set_resolution_script)
        self.assertIn('target_fps="${SUNSHINE_CLIENT_FPS:-}"', set_resolution_script)
        self.assertIn('sync_since="$(python3 - <<\'PY\'', set_resolution_script)
        self.assertIn('print(f"{time.time():.6f}")', set_resolution_script)
        self.assertIn(
            f'setsid "{state["paths"]["apply_exact_refresh_script"]}" "${{target_width}}" "${{target_height}}" "$sync_since" >/dev/null 2>&1 &',
            set_resolution_script,
        )

    def test_custom_mode_set_resolution_script_uses_fixed_target(self) -> None:
        state = manager._default_state()
        state["refresh_rate_sync_mode"] = "custom"
        state["custom_display_mode"] = {
            "width": 3440,
            "height": 1440,
            "refresh": 59.94,
        }

        scripts = manager._script_templates(state)
        set_resolution_script = scripts[Path(state["paths"]["set_resolution_script"])]
        resolver_script = scripts[Path(state["paths"]["resolve_stream_fps_script"])]

        self.assertIn('target_width="3440"', set_resolution_script)
        self.assertIn('target_height="1440"', set_resolution_script)
        self.assertIn('target_fps="59.94"', set_resolution_script)
        self.assertIn('if [ "$mode" = "exact" ]; then', set_resolution_script)
        self.assertIn('custom_fps="59.94"', resolver_script)

    def test_resolve_stream_fps_script_uses_exact_mode_when_requested(self) -> None:
        state = manager._default_state()
        state["refresh_rate_sync_mode"] = "exact"

        scripts = manager._script_templates(state)
        resolver_script = scripts[Path(state["paths"]["resolve_stream_fps_script"])]

        self.assertIn('mode="exact"', resolver_script)
        self.assertIn("Requested frame rate", resolver_script)
        self.assertIn('print(f"{fps:.2f}")', resolver_script)
        self.assertIn('attempts=100', resolver_script)
        self.assertIn('if [ "$fallback_mode" = "none" ]; then', resolver_script)
        self.assertIn('since_time="${3:-}"', resolver_script)
        self.assertIn('journalctl --user -u "', resolver_script)
        self.assertIn('line_epoch is not None and line_epoch >= since_epoch', resolver_script)

    def test_apply_exact_refresh_script_waits_for_exact_fps(self) -> None:
        state = manager._default_state()

        scripts = manager._script_templates(state)
        apply_exact_refresh_script = scripts[Path(state["paths"]["apply_exact_refresh_script"])]

        self.assertIn('since_time="${3:-}"', apply_exact_refresh_script)
        self.assertIn(
            f'exact_stream_fps="$("{state["paths"]["resolve_stream_fps_script"]}" exact none "$since_time")"',
            apply_exact_refresh_script,
        )
        self.assertIn(
            'swaymsg "output HEADLESS-1 mode ${width}x${height}@${exact_stream_fps}Hz"',
            apply_exact_refresh_script,
        )

    def test_input_bridge_script_includes_acl_user_helpers(self) -> None:
        state = manager._default_state()
        script = manager._input_bridge_script(state)

        self.assertIn("import grp", script)
        self.assertIn("def current_user_name():", script)
        self.assertIn("def current_user_group():", script)


if __name__ == "__main__":
    unittest.main()
