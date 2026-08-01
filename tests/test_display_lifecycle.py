import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from display import audio_policy
from display import manager
from display import scripts_render
from display import sunshine_service
from tests._display_test_helpers import temp_display_state


class DisplayLifecycleTests(unittest.TestCase):
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

    def test_udev_rule_does_not_match_bridge_phys_prefix(self) -> None:
        rule = manager._udev_rule()
        self.assertNotIn('ATTRS{phys}=="lts-inputbridge/*"', rule)

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
        state = manager._default_state()
        with patch.dict(
            manager.os.environ,
            {
                "XDG_CURRENT_DESKTOP": "KDE",
                "DESKTOP_SESSION": "plasma",
                "KDE_FULL_SESSION": "true",
            },
            clear=False,
        ):
            rendered = scripts_render.render_managed_files(state)
        script = rendered[Path(state["paths"]["kwin_input_isolation_script"])]
        self.assertIn("def is_plasma_session():", script)
        self.assertIn("plasma_markers", script)
        self.assertIn('write_status(state="starting")', script)
        self.assertIn('DEVICE_INTERFACE = "', script)
        self.assertIn('return f"{value:04x}"', script)
        self.assertIn('replace("_", " ")', script)

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
        with patch("pathlib.Path.read_text", return_value=input_listing):
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

    def test_setup_tracks_original_sunshine_audio_sink_without_overwriting_it(self) -> None:
        state, conf_path = self._temp_audio_state()
        original_ensure_dependencies = manager._ensure_dependencies
        original_load_state = manager.load_state
        original_sunshine_service_active = sunshine_service.is_sunshine_service_active
        original_state_paths = manager._state_paths
        original_refresh_managed_files = manager.refresh_managed_files
        original_save_state = manager.save_state
        original_install_udev_rule = manager._install_udev_rule
        original_cleanup_legacy_display_units = sunshine_service.cleanup_managed_overrides
        original_daemon_reload = manager._daemon_reload
        try:
            manager._ensure_dependencies = lambda: []
            manager.load_state = lambda: state
            sunshine_service.is_sunshine_service_active = lambda: False
            manager._state_paths = lambda _unit: state["paths"]
            manager.refresh_managed_files = lambda current=None: current if current is not None else state
            manager.save_state = lambda current: None
            manager._install_udev_rule = lambda current: True
            sunshine_service.cleanup_managed_overrides = lambda current: None
            manager._daemon_reload = lambda: None

            with patch.object(audio_policy.subprocess, "run") as fake_run, \
                 patch.object(audio_policy.shutil, "which", return_value=None):
                result = manager.setup_display()
        finally:
            manager._ensure_dependencies = original_ensure_dependencies
            manager.load_state = original_load_state
            sunshine_service.is_sunshine_service_active = original_sunshine_service_active
            manager._state_paths = original_state_paths
            manager.refresh_managed_files = original_refresh_managed_files
            manager.save_state = original_save_state
            manager._install_udev_rule = original_install_udev_rule
            sunshine_service.cleanup_managed_overrides = original_cleanup_legacy_display_units
            manager._daemon_reload = original_daemon_reload

        self.assertEqual(result, 0)
        self.assertEqual(
            state["sunshine_audio_sink"],
            {"present": True, "value": "host-speakers"},
        )
        self.assertEqual(conf_path.read_text(encoding="utf-8"), "audio_sink = host-speakers\n")

    def test_start_display_restores_audio_on_sunshine_start_failure(self) -> None:
        state, conf_path = self._temp_audio_state()
        original_load_state = manager.load_state
        original_refresh_managed_files = manager.refresh_managed_files
        original_save_state = manager.save_state
        original_sunshine_unit = sunshine_service.sunshine_unit
        original_start_sunshine_unit = sunshine_service.start_sunshine_unit
        original_stop_sunshine_unit = sunshine_service.stop_sunshine_unit
        try:
            manager.load_state = lambda: state
            manager.refresh_managed_files = lambda current=None: current if current is not None else state
            manager.save_state = lambda current: None
            sunshine_service.sunshine_unit = lambda: sunshine_service.SUNSHINE_UNIT
            sunshine_service.start_sunshine_unit = lambda unit: subprocess.CompletedProcess(
                ["systemctl", "--user", "start", unit], 1, "", "boom"
            )
            sunshine_service.stop_sunshine_unit = lambda unit: subprocess.CompletedProcess(
                ["systemctl", "--user", "stop", unit], 0, "", ""
            )

            with patch.object(audio_policy.subprocess, "run") as fake_run, \
                 patch.object(audio_policy.shutil, "which", return_value=None):
                result = manager.start_display()
        finally:
            manager.load_state = original_load_state
            manager.refresh_managed_files = original_refresh_managed_files
            manager.save_state = original_save_state
            sunshine_service.sunshine_unit = original_sunshine_unit
            sunshine_service.start_sunshine_unit = original_start_sunshine_unit
            sunshine_service.stop_sunshine_unit = original_stop_sunshine_unit

        self.assertEqual(result, 1)
        self.assertEqual(conf_path.read_text(encoding="utf-8"), "audio_sink = host-speakers\n")

    def test_stop_display_restores_original_audio_target(self) -> None:
        state, conf_path = self._temp_audio_state("audio_sink = lts-sunshine-stereo\n")
        state["sunshine_audio_sink"] = {"present": True, "value": "host-speakers"}
        original_load_state = manager.load_state
        original_save_state = manager.save_state
        original_sunshine_unit = sunshine_service.sunshine_unit
        original_stop_sunshine_unit = sunshine_service.stop_sunshine_unit
        try:
            manager.load_state = lambda: state
            manager.save_state = lambda current: None
            sunshine_service.sunshine_unit = lambda: sunshine_service.SUNSHINE_UNIT
            sunshine_service.stop_sunshine_unit = lambda unit: subprocess.CompletedProcess(
                ["systemctl", "--user", "stop", unit], 0, "", ""
            )

            result = manager.stop_display()
        finally:
            manager.load_state = original_load_state
            manager.save_state = original_save_state
            sunshine_service.sunshine_unit = original_sunshine_unit
            sunshine_service.stop_sunshine_unit = original_stop_sunshine_unit

        self.assertEqual(result, 0)
        self.assertEqual(conf_path.read_text(encoding="utf-8"), "audio_sink = host-speakers\n")

    def test_remove_display_deletes_override_files(self) -> None:
        state = manager._default_state()
        state["enabled"] = True
        state["sunshine_unit_name"] = sunshine_service.SUNSHINE_UNIT
        tempdir = tempfile.TemporaryDirectory()
        self.addCleanup(tempdir.cleanup)
        base = Path(tempdir.name)
        override_dir = base / f"{sunshine_service.SUNSHINE_UNIT}.d"
        override_dir.mkdir(parents=True)

        state["paths"] = dict(state["paths"])
        state["paths"]["state_path"] = str(base / "display.json")
        state["paths"]["systemd_user_dir"] = str(base)
        state["paths"]["sunshine_override_dir"] = str(override_dir)
        state["paths"]["sunshine_override"] = str(override_dir / "override.conf")
        state["paths"]["kwin_input_isolation_script"] = str(base / "lutristosunshine-kwin-input-isolation.py")
        state["paths"]["sunshine_wrapper_script"] = str(base / "lutristosunshine-run-display-service.sh")
        state["paths"]["portal_active_file"] = str(base / "portal-active")
        state["paths"]["portal_lock_file"] = str(base / "portal-lock")
        state["paths"]["kwin_input_isolation_status_file"] = str(base / "kwin-input-isolation-status.json")
        state["paths"]["wayland_display_file"] = str(base / "wayland-display")
        state["paths"]["audio_module_file"] = str(base / "audio-module-id")
        state["paths"]["sway_config"] = str(base / "sway.conf")
        state["paths"]["sway_start_script"] = str(base / "lutristosunshine-start-headless-sway.sh")
        state["paths"]["sunshine_start_script"] = str(base / "lutristosunshine-start-display-sunshine.sh")
        state["paths"]["audio_create_script"] = str(base / "lutristosunshine-create-audio-sink.sh")
        state["paths"]["audio_cleanup_script"] = str(base / "lutristosunshine-cleanup-audio-sink.sh")
        state["paths"]["launch_app_script"] = str(base / "lutristosunshine-launch-app.sh")
        state["paths"]["resolve_stream_fps_script"] = str(base / "lutristosunshine-resolve-stream-fps.sh")
        state["paths"]["apply_exact_refresh_script"] = str(base / "lutristosunshine-apply-exact-refresh.sh")
        state["paths"]["headless_prep_script"] = str(base / "lutristosunshine-run-headless-prep.sh")
        state["paths"]["set_resolution_script"] = str(base / "lutristosunshine-set-resolution.sh")
        state["paths"]["reset_resolution_script"] = str(base / "lutristosunshine-reset-resolution.sh")
        state["paths"]["get_gpu_addr"] = str(base / "lutristosunshine-get-gpu-addr.sh")
        state["paths"]["wireplumber_policy_script"] = str(base / "lts-audio-policy.lua")
        state["paths"]["wireplumber_policy_conf"] = str(base / "54-lts-audio-policy.conf")

        for key in [
            "sunshine_override",
            "sway_config", "sway_start_script", "sunshine_start_script",
            "kwin_input_isolation_script",
            "audio_create_script", "audio_cleanup_script",
            "sunshine_wrapper_script",
            "launch_app_script", "resolve_stream_fps_script",
            "apply_exact_refresh_script", "headless_prep_script",
            "set_resolution_script", "reset_resolution_script", "get_gpu_addr",
            "portal_active_file",
            "portal_lock_file",
            "kwin_input_isolation_status_file",
            "wayland_display_file",
            "audio_module_file",
        ]:
            Path(state["paths"][key]).write_text("managed\n", encoding="utf-8")

        Path(state["paths"]["sunshine_override"]).write_text(
            f"[Service]\nExecStart=\nExecStart={state['paths']['sunshine_wrapper_script']}\n",
            encoding="utf-8",
        )

        original_load_state = manager.load_state
        original_save_state = manager.save_state
        original_stop_display = manager.stop_display
        original_remove_udev_rule = manager._remove_udev_rule
        original_daemon_reload = manager._daemon_reload
        original_remove_wireplumber_policy = audio_policy.remove
        try:
            manager.load_state = lambda: state
            manager.save_state = lambda current: None
            manager.stop_display = lambda: 0
            manager._remove_udev_rule = lambda current: True
            manager._daemon_reload = lambda: None
            audio_policy.remove = lambda current: None

            result = manager.remove_display()
        finally:
            manager.load_state = original_load_state
            manager.save_state = original_save_state
            manager.stop_display = original_stop_display
            manager._remove_udev_rule = original_remove_udev_rule
            manager._daemon_reload = original_daemon_reload
            audio_policy.remove = original_remove_wireplumber_policy

        self.assertEqual(result, 0)
        self.assertFalse(Path(state["paths"]["sunshine_override"]).exists())
        self.assertFalse(Path(state["paths"]["sunshine_wrapper_script"]).exists())
        self.assertFalse(override_dir.exists())

    def test_display_snapshot_not_configured_has_enable_next_step(self) -> None:
        tempdir = tempfile.TemporaryDirectory()
        self.addCleanup(tempdir.cleanup)
        state = manager._default_state()
        state["paths"] = dict(state["paths"])
        state["paths"]["portal_active_file"] = str(Path(tempdir.name) / "portal-active")
        original_load_state = manager.load_state
        original_ensure_dependencies = manager._ensure_dependencies
        try:
            manager.load_state = lambda: state
            manager._ensure_dependencies = lambda: []
            with patch.dict(
                os.environ,
                {
                    "XDG_CURRENT_DESKTOP": "GNOME",
                    "XDG_SESSION_DESKTOP": "gnome",
                    "DESKTOP_SESSION": "gnome",
                    "KDE_FULL_SESSION": "",
                },
                clear=False,
            ):
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
        self.assertEqual(snapshot["status_summary"], "NOT SET UP")
        self.assertEqual(
            snapshot["display_sync_summary"], "follow Moonlight requested FPS; MangoHud FPS sync off"
        )
        self.assertEqual(snapshot["refresh_rate_sync_mode_summary"], "follow Moonlight requested FPS")
        self.assertEqual(snapshot["isolation_level"], "success")
        self.assertEqual(snapshot["isolation_summary"], "rule ready")

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

        original_run = manager.run_command
        original_path_exists = manager.Path.exists
        try:
            manager.run_command = lambda command, **kwargs: subprocess.CompletedProcess(
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
            manager.run_command = original_run
            manager.Path.exists = original_path_exists

        self.assertEqual(mode, "2560x1440 @ 119.99 Hz")

    def test_current_headless_mode_returns_empty_when_headless_output_missing(self) -> None:
        state = manager._default_state()
        state["enabled"] = True
        state["sway_socket"] = "/tmp/lts-sway.sock"

        original_run = manager.run_command
        original_path_exists = manager.Path.exists
        try:
            manager.run_command = lambda command, **kwargs: subprocess.CompletedProcess(
                command,
                0,
                stdout=json.dumps([{"name": "HDMI-A-1", "current_mode": {"width": 1920, "height": 1080, "refresh": 60000}}]),
                stderr="",
            )
            manager.Path.exists = lambda path: str(path) == state["sway_socket"]

            mode = manager._current_headless_mode(state, sunshine_active=True, sway_active=True)
        finally:
            manager.run_command = original_run
            manager.Path.exists = original_path_exists

        self.assertEqual(mode, "")

    def test_current_headless_mode_returns_empty_when_swaymsg_fails(self) -> None:
        state = manager._default_state()
        state["enabled"] = True
        state["sway_socket"] = "/tmp/lts-sway.sock"

        original_run = manager.run_command
        original_path_exists = manager.Path.exists
        try:
            manager.run_command = lambda command, **kwargs: subprocess.CompletedProcess(
                command,
                1,
                stdout="",
                stderr="failed",
            )
            manager.Path.exists = lambda path: str(path) == state["sway_socket"]

            mode = manager._current_headless_mode(state, sunshine_active=True, sway_active=True)
        finally:
            manager.run_command = original_run
            manager.Path.exists = original_path_exists

        self.assertEqual(mode, "")

    def test_display_doctor_report_flags_missing_dependencies(self) -> None:
        state = manager._default_state()
        original_load_state = manager.load_state
        original_ensure_dependencies = manager._ensure_dependencies
        original_installation_audit = sunshine_service.sunshine_installation_audit
        try:
            manager.load_state = lambda: state
            manager._ensure_dependencies = lambda: ["sway", "setfacl"]
            sunshine_service.sunshine_installation_audit = lambda unit=None: sunshine_service.make_sunshine_install_audit(
                managed_unit=sunshine_service.SUNSHINE_UNIT,
                managed_type="native",
                detected_types=["native"],
            )
            report = manager.display_doctor_report()
        finally:
            manager.load_state = original_load_state
            manager._ensure_dependencies = original_ensure_dependencies
            sunshine_service.sunshine_installation_audit = original_installation_audit

        self.assertEqual(report["summary"], "needs_attention")
        self.assertTrue(any(check["status"] == "fail" for check in report["checks"]))

    def test_display_doctor_report_warns_when_install_probe_disagrees_with_managed_unit(self) -> None:
        state = manager._default_state()
        state["enabled"] = True
        original_load_state = manager.load_state
        original_ensure_dependencies = manager._ensure_dependencies
        original_sunshine_service_active = sunshine_service.is_sunshine_service_active
        original_installation_audit = sunshine_service.sunshine_installation_audit
        try:
            manager.load_state = lambda: state
            manager._ensure_dependencies = lambda: []
            sunshine_service.is_sunshine_service_active = lambda: True
            sunshine_service.sunshine_installation_audit = lambda unit=None: sunshine_service.make_sunshine_install_audit(
                managed_unit=sunshine_service.SUNSHINE_UNIT,
                managed_type="native",
                detected_types=["homebrew", "native"],
                package_probe_type="homebrew",
                resolved_type="native",
            )

            report = manager.display_doctor_report()
        finally:
            manager.load_state = original_load_state
            manager._ensure_dependencies = original_ensure_dependencies
            sunshine_service.is_sunshine_service_active = original_sunshine_service_active
            sunshine_service.sunshine_installation_audit = original_installation_audit

        install_check = next(check for check in report["checks"] if check["label"] == "Sunshine installation")
        self.assertEqual(install_check["status"], "warn")
        self.assertIn("Multiple Sunshine installs detected (homebrew, native)", install_check["message"])
        self.assertIn("manages app-dev.lizardbyte.app.Sunshine.service (native) and resolves Sunshine as native", install_check["message"])
        self.assertIn("package-only probing would resolve homebrew", install_check["message"])

    def test_display_doctor_report_skips_install_warning_when_probe_matches_managed_unit(self) -> None:
        state = manager._default_state()
        state["enabled"] = True
        original_load_state = manager.load_state
        original_ensure_dependencies = manager._ensure_dependencies
        original_sunshine_service_active = sunshine_service.is_sunshine_service_active
        original_installation_audit = sunshine_service.sunshine_installation_audit
        try:
            manager.load_state = lambda: state
            manager._ensure_dependencies = lambda: []
            sunshine_service.is_sunshine_service_active = lambda: True
            sunshine_service.sunshine_installation_audit = lambda unit=None: sunshine_service.make_sunshine_install_audit(
                managed_unit=sunshine_service.SUNSHINE_UNIT,
                managed_type="native",
                detected_types=["native"],
            )

            report = manager.display_doctor_report()
        finally:
            manager.load_state = original_load_state
            manager._ensure_dependencies = original_ensure_dependencies
            sunshine_service.is_sunshine_service_active = original_sunshine_service_active
            sunshine_service.sunshine_installation_audit = original_installation_audit

        self.assertFalse(any(check["label"] == "Sunshine installation" for check in report["checks"]))

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
        original_sunshine_service_active = sunshine_service.is_sunshine_service_active
        original_sunshine_virtual_input_devices = manager._sunshine_virtual_input_devices
        try:
            manager.load_state = lambda: state
            manager._ensure_dependencies = lambda: []
            sunshine_service.is_sunshine_service_active = lambda: False
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
            sunshine_service.is_sunshine_service_active = original_sunshine_service_active
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
        original_sunshine_service_active = sunshine_service.is_sunshine_service_active
        original_sunshine_virtual_input_devices = manager._sunshine_virtual_input_devices
        try:
            manager.load_state = lambda: state
            manager._ensure_dependencies = lambda: []
            sunshine_service.is_sunshine_service_active = lambda: False
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
            sunshine_service.is_sunshine_service_active = original_sunshine_service_active
            manager._sunshine_virtual_input_devices = original_sunshine_virtual_input_devices

        kwin_check = next(check for check in report["checks"] if check["label"] == "KWin isolation")
        self.assertEqual(kwin_check["status"], "warn")
        self.assertIn("matched 5 of 5 host Sunshine input device(s), but disabled 0", kwin_check["message"])

    def test_managed_sunshine_templates_do_not_force_global_pulse_sink(self) -> None:
        state = manager._default_state()
        scripts = scripts_render.render_managed_files(state)
        audio_files = audio_policy.managed_files(state)

        sway_start = scripts[Path(state["paths"]["sway_start_script"])]
        sunshine_start = scripts[Path(state["paths"]["sunshine_start_script"])]
        sunshine_wrapper = scripts[Path(state["paths"]["sunshine_wrapper_script"])]
        audio_create = audio_files[Path(state["paths"]["audio_create_script"])]
        audio_cleanup = audio_files[Path(state["paths"]["audio_cleanup_script"])]
        launch_script = scripts[Path(state["paths"]["launch_app_script"])]
        headless_prep_script = scripts[Path(state["paths"]["headless_prep_script"])]
        sunshine_override = scripts[Path(state["paths"]["sunshine_override"])]

        self.assertNotIn('PULSE_SINK="lts-sunshine-stereo"', sway_start)
        self.assertIn("sleep 0.1", sway_start)
        self.assertNotIn('PULSE_SINK="lts-sunshine-stereo"', sunshine_start)
        self.assertNotIn("PIPEWIRE_PROPS", sunshine_start)
        # The Sunshine-config snapshot now lives in the audio create script,
        # not the service wrapper.
        self.assertNotIn('audio_sink = {managed_sink}', sunshine_wrapper)
        self.assertIn('audio_sink = {managed_sink}', audio_create)
        self.assertIn('export XDG_RUNTIME_DIR="$runtime_dir"', sunshine_wrapper)
        self.assertIn('export DBUS_SESSION_BUS_ADDRESS="$dbus_value"', sunshine_wrapper)
        self.assertIn('run_audio_command() {', audio_create)
        self.assertIn('local command=(/usr/bin/env', audio_create)
        self.assertIn('command+=("PULSE_SERVER=$pulse_server_value")', audio_create)
        self.assertIn('command+=("PULSE_CLIENTCONFIG=$pulse_clientconfig_value")', audio_create)
        self.assertIn('run_audio_command pactl list sinks short', audio_create)
        self.assertIn('run_audio_command pactl load-module', audio_create)
        self.assertIn('run_audio_command() {', audio_cleanup)
        self.assertIn('local command=(/usr/bin/env', audio_cleanup)
        self.assertIn('run_audio_command pactl unload-module', audio_cleanup)
        # Per-process env -i still routes native (non-flatpak) games to the
        # managed sink and tags their streams as games.
        self.assertIn('"PULSE_SINK=lts-sunshine-stereo"', launch_script)
        self.assertIn('"PULSE_PROP=lutristosunshine.stream=game"', launch_script)
        self.assertIn('"PULSE_SINK=lts-sunshine-stereo"', headless_prep_script)
        self.assertIn('"PULSE_PROP=lutristosunshine.stream=game"', headless_prep_script)
        self.assertIn('"PIPEWIRE_PROPS={ lutristosunshine.stream = "game" }"', launch_script)
        self.assertIn('"PIPEWIRE_PROPS={ lutristosunshine.stream = "game" }"', headless_prep_script)
        # The global Flatpak portal handoff must NOT export audio vars: doing so
        # poisoned the systemd/DBus activation env and permanently tagged host
        # apps (browsers, speech-dispatcher, ...) as games, stranding their audio
        # on the managed sink even after the stream ended.
        self.assertNotIn("export PULSE_SINK=", launch_script)
        self.assertNotIn("export PULSE_PROP=", launch_script)
        self.assertNotIn("export PIPEWIRE_PROPS=", launch_script)
        self.assertNotIn("export PULSE_SINK=", headless_prep_script)
        self.assertNotIn("export PULSE_PROP=", headless_prep_script)
        self.assertNotIn("export PIPEWIRE_PROPS=", headless_prep_script)
        # apply_headless_portal_env drains stale audio vars from the activation
        # environment (leftover from earlier versions that leaked them globally).
        self.assertIn("systemctl --user unset-environment PULSE_SINK PULSE_PROP PIPEWIRE_PROPS", launch_script)
        self.assertIn("systemctl --user unset-environment PULSE_SINK PULSE_PROP PIPEWIRE_PROPS", headless_prep_script)
        self.assertIn("dbus-update-activation-environment --systemd", launch_script)
        self.assertIn("dbus-update-activation-environment --systemd", headless_prep_script)
        # Flatpak apps get the audio routing scoped per-launch via
        # inject_flatpak_audio_env (flatpak run --env=...), never globally.
        self.assertIn("inject_flatpak_audio_env() {", launch_script)
        self.assertIn("inject_flatpak_audio_env() {", headless_prep_script)
        self.assertIn('--env=PULSE_SINK=" + sink', launch_script)
        self.assertIn('--env=PULSE_SINK=" + sink', headless_prep_script)
        self.assertNotIn("MANGOHUD_CONFIG", launch_script)
        self.assertIn("ExecStart=", sunshine_override)
        self.assertIn(state["paths"]["sunshine_wrapper_script"], sunshine_override)
        self.assertNotIn("Environment=PULSE_SINK=", sunshine_override)

    def test_rendered_managed_files_have_no_unsubstituted_placeholders(self) -> None:
        state = manager._default_state()
        state["dynamic_mangohud_fps_limit"] = True
        state["refresh_rate_sync_mode"] = "exact"
        state["custom_display_mode"] = {"width": 3440, "height": 1440, "refresh": 59.94}
        state["gpu_mode"] = "manual"
        state["gpu_card_path"] = "/dev/dri/card0"
        state["gpu_render_path"] = "/dev/dri/renderD128"
        state["renderer_mode"] = "vulkan"

        for path, content in scripts_render.render_managed_files(state).items():
            self.assertNotRegex(
                content,
                r"@[A-Z][A-Z0-9_]*@",
                f"unsubstituted placeholder in {path.name}",
            )
        for path, content in scripts_render.render_managed_files(manager._default_state()).items():
            self.assertNotRegex(
                content,
                r"@[A-Z][A-Z0-9_]*@",
                f"unsubstituted placeholder in default state {path.name}",
            )

    def test_launch_script_can_inject_dynamic_mangohud_fps_limit(self) -> None:
        state = manager._default_state()
        state["dynamic_mangohud_fps_limit"] = True

        scripts = scripts_render.render_managed_files(state)
        launch_script = scripts[Path(state["paths"]["launch_app_script"])]

        self.assertIn('mangohud_config_value=""', launch_script)
        self.assertNotIn('local mangohud_config_value=""', launch_script)
        self.assertIn(
            f'resolved_stream_fps="$("{state["paths"]["resolve_stream_fps_script"]}" "client" fallback)"',
            launch_script,
        )
        self.assertIn('mangohud_config_value="read_cfg,fps_limit=$resolved_stream_fps"', launch_script)
        self.assertIn('launch_command+=("MANGOHUD_CONFIG=$mangohud_config_value")', launch_script)
        self.assertIn('is_flatpak_command "$command_to_run"', launch_script)
        self.assertIn("--env=MANGOHUD_CONFIG=", launch_script)

    def test_launch_script_waits_for_exact_stream_fps_before_launch(self) -> None:
        state = manager._default_state()
        state["dynamic_mangohud_fps_limit"] = True
        state["refresh_rate_sync_mode"] = "exact"

        scripts = scripts_render.render_managed_files(state)
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

        scripts = scripts_render.render_managed_files(state)
        set_resolution_script = scripts[Path(state["paths"]["set_resolution_script"])]

        self.assertIn(
            'swaymsg_cmd "output HEADLESS-1 mode ${target_width}x${target_height}@${target_fps}Hz"',
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

        scripts = scripts_render.render_managed_files(state)
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

        scripts = scripts_render.render_managed_files(state)
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

        scripts = scripts_render.render_managed_files(state)
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

    def test_restart_display_returns_stop_failure_without_starting(self) -> None:
        state = manager._default_state()
        state["enabled"] = True
        start_calls = []
        original_load_state = manager.load_state
        original_stop_display = manager.stop_display
        original_start_display = manager.start_display
        try:
            manager.load_state = lambda: state
            manager.stop_display = lambda: 1
            manager.start_display = lambda: start_calls.append(True) or 0

            result = manager.restart_display()
        finally:
            manager.load_state = original_load_state
            manager.stop_display = original_stop_display
            manager.start_display = original_start_display

        self.assertEqual(result, 1)
        self.assertEqual(start_calls, [])

    def test_restart_display_proceeds_to_start_when_stop_succeeds(self) -> None:
        state = manager._default_state()
        state["enabled"] = True
        original_load_state = manager.load_state
        original_stop_display = manager.stop_display
        original_start_display = manager.start_display
        try:
            manager.load_state = lambda: state
            manager.stop_display = lambda: 0
            manager.start_display = lambda: 0

            result = manager.restart_display()
        finally:
            manager.load_state = original_load_state
            manager.stop_display = original_stop_display
            manager.start_display = original_start_display

        self.assertEqual(result, 0)


    def test_remove_display_continues_cleanup_when_stop_fails(self) -> None:
        state = manager._default_state()
        state["enabled"] = True
        _remove_tempdir = tempfile.TemporaryDirectory()
        self.addCleanup(_remove_tempdir.cleanup)
        _remove_base = Path(_remove_tempdir.name)
        state["paths"] = dict(state["paths"])
        for _key in (
            "state_path", "sway_config", "sway_start_script", "sunshine_start_script",
            "sunshine_wrapper_script",
            "kwin_input_isolation_script",
            "audio_create_script", "audio_cleanup_script",
            "launch_app_script", "resolve_stream_fps_script",
            "apply_exact_refresh_script", "headless_prep_script",
            "set_resolution_script", "reset_resolution_script", "get_gpu_addr",
            "portal_active_file", "portal_lock_file",
            "kwin_input_isolation_status_file",
            "wayland_display_file", "audio_module_file",
            "wireplumber_policy_script", "wireplumber_policy_conf",
        ):
            if state["paths"].get(_key):
                _p = _remove_base / Path(state["paths"][_key]).name
                _p.write_text("managed\n", encoding="utf-8")
                state["paths"][_key] = str(_p)
        cleanup_calls = []
        original_load_state = manager.load_state
        original_stop_display = manager.stop_display
        original_remove_udev_rule = manager._remove_udev_rule
        original_cleanup_managed_overrides = sunshine_service.cleanup_managed_overrides
        original_daemon_reload = manager._daemon_reload
        original_save_state = manager.save_state
        original_remove_wireplumber_policy = audio_policy.remove
        try:
            manager.load_state = lambda: state
            manager.stop_display = lambda: 1
            manager._remove_udev_rule = lambda current: cleanup_calls.append("udev") or True
            sunshine_service.cleanup_managed_overrides = lambda current: cleanup_calls.append("overrides")
            manager._daemon_reload = lambda: None
            manager.save_state = lambda current: None
            audio_policy.remove = lambda current: None

            result = manager.remove_display()
        finally:
            manager.load_state = original_load_state
            manager.stop_display = original_stop_display
            manager._remove_udev_rule = original_remove_udev_rule
            sunshine_service.cleanup_managed_overrides = original_cleanup_managed_overrides
            manager._daemon_reload = original_daemon_reload
            manager.save_state = original_save_state
            audio_policy.remove = original_remove_wireplumber_policy

        self.assertEqual(result, 0)
        self.assertIn("udev", cleanup_calls)
        self.assertIn("overrides", cleanup_calls)


    def test_wireplumber_policy_script_names_managed_sinks(self) -> None:
        state = manager._default_state()
        files = audio_policy.managed_files(state)
        script = files[Path(state["paths"]["wireplumber_policy_script"])]

        self.assertIn('audio_sink = "lts-sunshine-stereo"', script)
        for name in ("sink-sunshine-stereo", "sink-sunshine-surround51", "sink-sunshine-surround71"):
            self.assertIn(f'["{name}"] = true', script)
        self.assertIn('name = "lts-audio/guard-default-sink"', script)
        self.assertIn('name = "lts-audio/route-game-and-recorder"', script)

    def test_wireplumber_route_hook_overrides_preset_target(self) -> None:
        # Regression for the strand-on-host bug: when a client disconnects and
        # reconnects, stream-restore / the Flatpak portal rewrites a game
        # stream's effective target.object to the host default, and the old
        # route hook bailed on `if target then return end`, leaving game audio
        # on the host. The hook must pin tagged streams authoritatively, so the
        # defer line must be gone; untagged streams are skipped by the want-guard.
        state = manager._default_state()
        files = audio_policy.managed_files(state)
        script = files[Path(state["paths"]["wireplumber_policy_script"])]
        self.assertNotIn("if target then return end", script)

    def test_wireplumber_policy_conf_registers_component_and_profile(self) -> None:
        state = manager._default_state()
        files = audio_policy.managed_files(state)
        conf = files[Path(state["paths"]["wireplumber_policy_conf"])]

        self.assertIn(audio_policy.WIREPLUMBER_POLICY_SCRIPT_NAME, conf)
        self.assertIn("script/lua", conf)
        self.assertIn("script.lts-audio-policy", conf)
        self.assertIn("script.lts-audio-policy = required", conf)

    def test_wireplumber_policy_files_in_managed_paths(self) -> None:
        state = manager._default_state()
        paths = manager._managed_setup_paths(state)
        path_names = [p.name for p in paths]

        self.assertIn(audio_policy.WIREPLUMBER_POLICY_SCRIPT_NAME, path_names)
        self.assertIn(audio_policy.WIREPLUMBER_POLICY_CONF_NAME, path_names)

    def test_temp_display_state_redirects_wireplumber_paths(self) -> None:
        # remove_display unlinks the WP policy files; if the helper ever stops
        # redirecting these paths to the temp dir, that unlink hits the real
        # ~/.config/wireplumber install. Guard the redirect in CI.
        with temp_display_state(manager) as (state, base):
            for key in ("wireplumber_policy_script", "wireplumber_policy_conf"):
                self.assertTrue(
                    state["paths"][key].startswith(str(base)),
                    f"{key} leaked to real path: {state['paths'][key]}",
                )

    def test_write_and_remove_wireplumber_policy_files_round_trip(self) -> None:
        with temp_display_state(manager) as (state, base):
            script_path = Path(state["paths"]["wireplumber_policy_script"])
            conf_path = Path(state["paths"]["wireplumber_policy_conf"])
            # remove() also strips legacy prep hooks, so point sunshine_conf at
            # a temp file: the real user config must stay untouched.
            sunshine_conf = base / "sunshine.conf"
            sunshine_conf.write_text(
                "global_prep_cmd = [{\"do\": \"user-cmd\", \"undo\": \"user-undo\"}]\n",
                encoding="utf-8",
            )
            state["paths"]["sunshine_conf"] = str(sunshine_conf)
            files = audio_policy.managed_files(state)
            manager._write_file(script_path, files[script_path])
            manager._write_file(conf_path, files[conf_path])
            self.assertTrue(script_path.exists())
            self.assertTrue(conf_path.exists())
            self.assertIn("LTS audio policy registered", script_path.read_text())
            self.assertIn("script.lts-audio-policy = required", conf_path.read_text())
            with patch.object(audio_policy.subprocess, "run") as fake_run:
                audio_policy.remove(state)
            self.assertFalse(script_path.exists())
            self.assertFalse(conf_path.exists())
            self.assertIn("user-cmd", sunshine_conf.read_text(encoding="utf-8"))

    def test_setup_removes_lts_entries_from_global_prep_cmd(self) -> None:
        user_entry = {"do": "user-cmd", "undo": "user-undo"}
        lts_entry = {
            "do": "/opt/lutristosunshine/bin/lutristosunshine-stream-audio-start.sh",
            "undo": "/opt/lutristosunshine/bin/lutristosunshine-stream-audio-stop.sh",
        }
        combined = json.dumps([user_entry, lts_entry])
        with temp_display_state(manager) as (state, base):
            conf_path = base / "sunshine.conf"
            conf_path.write_text(f"global_prep_cmd = {combined}\n", encoding="utf-8")
            state["paths"]["sunshine_conf"] = str(conf_path)

            with patch.object(audio_policy.subprocess, "run") as fake_run, \
                 patch.object(audio_policy.shutil, "which", return_value=None):
                audio_policy.setup(state)

            conf_text = conf_path.read_text(encoding="utf-8")
            self.assertIn("global_prep_cmd", conf_text)
            self.assertIn("user-cmd", conf_text)
            self.assertNotIn("stream-audio-start", conf_text)
            self.assertNotIn("stream-audio-stop", conf_text)

    def test_remove_removes_global_prep_cmd_key_when_only_lts_entries_remain(self) -> None:
        lts_entry = {
            "do": "/opt/lutristosunshine/bin/lutristosunshine-stream-audio-start.sh",
            "undo": "/opt/lutristosunshine/bin/lutristosunshine-stream-audio-stop.sh",
        }
        combined = json.dumps([lts_entry])
        with temp_display_state(manager) as (state, base):
            conf_path = base / "sunshine.conf"
            conf_path.write_text(f"global_prep_cmd = {combined}\n", encoding="utf-8")
            state["paths"]["sunshine_conf"] = str(conf_path)

            with patch.object(audio_policy.subprocess, "run") as fake_run:
                audio_policy.remove(state)

            self.assertNotIn("global_prep_cmd", conf_path.read_text(encoding="utf-8"))

if __name__ == "__main__":
    unittest.main()
