import subprocess
import tempfile
import unittest
from pathlib import Path

from virtualdisplay import manager


class VirtualDisplayInputSelectionTests(unittest.TestCase):
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
            rule = manager._udev_rule()
        finally:
            manager.current_user_name = original_user
            manager.current_user_group = original_group
        self.assertIn('OWNER="alice"', rule)
        self.assertIn('GROUP="streaming"', rule)
        self.assertIn('MODE="0660"', rule)

    def test_udev_rule_preserves_input_classification(self) -> None:
        rule = manager._udev_rule()
        self.assertNotIn('ENV{ID_INPUT}=""', rule)
        self.assertNotIn('ENV{ID_INPUT_KEYBOARD}=""', rule)
        self.assertNotIn('ENV{ID_INPUT_MOUSE}=""', rule)
        self.assertNotIn('ENV{ID_INPUT_TOUCHPAD}=""', rule)

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
        original_cleanup_legacy_virtualdisplay_units = manager._cleanup_legacy_virtualdisplay_units
        original_daemon_reload = manager._daemon_reload
        try:
            manager._ensure_dependencies = lambda: []
            manager.load_state = lambda: state
            manager._sunshine_service_active = lambda: False
            manager.refresh_managed_files = lambda current=None: current if current is not None else state
            manager.save_state = lambda current: None
            manager._install_udev_rule = lambda current: True
            manager._cleanup_legacy_virtualdisplay_units = lambda current: None
            manager._daemon_reload = lambda: None

            result = manager.setup_virtual_display()
        finally:
            manager._ensure_dependencies = original_ensure_dependencies
            manager.load_state = original_load_state
            manager._sunshine_service_active = original_sunshine_service_active
            manager.refresh_managed_files = original_refresh_managed_files
            manager.save_state = original_save_state
            manager._install_udev_rule = original_install_udev_rule
            manager._cleanup_legacy_virtualdisplay_units = original_cleanup_legacy_virtualdisplay_units
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

    def test_start_virtual_display_restores_audio_on_sunshine_start_failure(self) -> None:
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

            def fake_systemctl_user(action, unit):
                if action == "start" and unit == manager.SUNSHINE_UNIT:
                    return subprocess.CompletedProcess([action, unit], 1, "", "boom")
                return subprocess.CompletedProcess([action, unit], 0, "", "")

            manager._systemctl_user = fake_systemctl_user

            result = manager.start_virtual_display()
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

    def test_stop_virtual_display_restores_original_audio_target(self) -> None:
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
            manager._systemctl_user = lambda action, unit: subprocess.CompletedProcess([action, unit], 0, "", "")
            manager._clear_input_bridge_status_file = lambda current: None
            manager._restore_host_audio_defaults = lambda current: None

            result = manager.stop_virtual_display()
        finally:
            manager.load_state = original_load_state
            manager.save_state = original_save_state
            manager._systemctl_user = original_systemctl_user
            manager._clear_input_bridge_status_file = original_clear_input_bridge_status_file
            manager._restore_host_audio_defaults = original_restore_host_audio_defaults

        self.assertEqual(result, 0)
        self.assertEqual(conf_path.read_text(encoding="utf-8"), "audio_sink = host-speakers\n")

    def test_remove_virtual_display_deletes_override_files(self) -> None:
        state = manager._default_state()
        state["enabled"] = True
        tempdir = tempfile.TemporaryDirectory()
        self.addCleanup(tempdir.cleanup)
        base = Path(tempdir.name)
        override_dir = base / "app-dev.lizardbyte.app.Sunshine.service.d"
        override_dir.mkdir(parents=True)

        state["paths"] = dict(state["paths"])
        state["paths"]["sunshine_override_dir"] = str(override_dir)
        state["paths"]["sunshine_override"] = str(override_dir / "override.conf")
        state["paths"]["input_bridge_script"] = str(base / "lutristosunshine-input-bridge.py")
        state["paths"]["audio_guard_script"] = str(base / "lutristosunshine-guard-audio-defaults.sh")
        state["paths"]["sunshine_wrapper_script"] = str(base / "lutristosunshine-run-virtualdisplay-service.sh")
        state["paths"]["portal_active_file"] = str(base / "portal-active")
        state["paths"]["portal_lock_file"] = str(base / "portal-lock")
        state["paths"]["input_bridge_status_file"] = str(base / "input-bridge-status.json")
        state["paths"]["wayland_display_file"] = str(base / "wayland-display")
        state["paths"]["audio_module_file"] = str(base / "audio-module-id")

        for key in [
            "sunshine_override",
            "input_bridge_script",
            "audio_guard_script",
            "sunshine_wrapper_script",
            "portal_active_file",
            "portal_lock_file",
            "input_bridge_status_file",
            "wayland_display_file",
            "audio_module_file",
        ]:
            Path(state["paths"][key]).write_text("managed\n", encoding="utf-8")

        original_load_state = manager.load_state
        original_save_state = manager.save_state
        original_stop_virtual_display = manager.stop_virtual_display
        original_remove_udev_rule = manager._remove_udev_rule
        original_cleanup_legacy_virtualdisplay_units = manager._cleanup_legacy_virtualdisplay_units
        original_daemon_reload = manager._daemon_reload
        cleanup_calls = []
        try:
            manager.load_state = lambda: state
            manager.save_state = lambda current: None
            manager.stop_virtual_display = lambda: 0
            manager._remove_udev_rule = lambda current: True
            manager._cleanup_legacy_virtualdisplay_units = lambda current: cleanup_calls.append(True)
            manager._daemon_reload = lambda: None

            result = manager.remove_virtual_display()
        finally:
            manager.load_state = original_load_state
            manager.save_state = original_save_state
            manager.stop_virtual_display = original_stop_virtual_display
            manager._remove_udev_rule = original_remove_udev_rule
            manager._cleanup_legacy_virtualdisplay_units = original_cleanup_legacy_virtualdisplay_units
            manager._daemon_reload = original_daemon_reload

        self.assertEqual(result, 0)
        self.assertEqual(cleanup_calls, [True])
        self.assertFalse(Path(state["paths"]["sunshine_override"]).exists())
        self.assertFalse(Path(state["paths"]["sunshine_wrapper_script"]).exists())
        self.assertFalse(override_dir.exists())

    def test_managed_sunshine_templates_do_not_force_global_pulse_sink(self) -> None:
        state = manager._default_state()
        scripts = manager._script_templates(state)
        units = manager._systemd_templates(state)

        sway_start = scripts[Path(state["paths"]["sway_start_script"])]
        sunshine_start = scripts[Path(state["paths"]["sunshine_start_script"])]
        sunshine_wrapper = scripts[Path(state["paths"]["sunshine_wrapper_script"])]
        audio_guard = scripts[Path(state["paths"]["audio_guard_script"])]
        launch_script = scripts[Path(state["paths"]["launch_app_script"])]
        sunshine_override = units[Path(state["paths"]["sunshine_override"])]

        self.assertNotIn('PULSE_SINK="lts-sunshine-stereo"', sway_start)
        self.assertIn("sleep 0.1", sway_start)
        self.assertNotIn('PULSE_SINK="lts-sunshine-stereo"', sunshine_start)
        self.assertIn('audio_sink = {managed_sink}', sunshine_wrapper)
        self.assertIn("sink-sunshine-stereo", audio_guard)
        self.assertIn("sink-sunshine-surround51", audio_guard)
        self.assertIn("sink-sunshine-surround71", audio_guard)
        self.assertIn('poll_interval="0.5"', audio_guard)
        self.assertIn('PULSE_SINK="lts-sunshine-stereo"', launch_script)
        self.assertIn("ExecStart=", sunshine_override)
        self.assertIn(state["paths"]["sunshine_wrapper_script"], sunshine_override)
        self.assertNotIn("Environment=PULSE_SINK=", sunshine_override)

    def test_input_bridge_script_includes_acl_user_helpers(self) -> None:
        state = manager._default_state()
        script = manager._input_bridge_script(state)

        self.assertIn("import grp", script)
        self.assertIn("def current_user_name():", script)
        self.assertIn("def current_user_group():", script)


if __name__ == "__main__":
    unittest.main()
