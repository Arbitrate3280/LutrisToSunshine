import io
import unittest
from contextlib import redirect_stdout

import lutristosunshine


class VirtualDisplayCliTests(unittest.TestCase):
    def test_parse_virtualdisplay_without_subcommand(self) -> None:
        args = lutristosunshine.parse_args(["virtualdisplay"])

        self.assertEqual(args.command, "virtualdisplay")
        self.assertIsNone(args.virtualdisplay_action)

    def test_parse_virtualdisplay_enable_command(self) -> None:
        args = lutristosunshine.parse_args(["virtualdisplay", "enable"])

        self.assertEqual(args.command, "virtualdisplay")
        self.assertEqual(args.virtualdisplay_action, "enable")

    def test_parse_virtualdisplay_start_command(self) -> None:
        args = lutristosunshine.parse_args(["virtualdisplay", "start"])

        self.assertEqual(args.command, "virtualdisplay")
        self.assertEqual(args.virtualdisplay_action, "start")

    def test_parse_virtualdisplay_restart_command(self) -> None:
        args = lutristosunshine.parse_args(["virtualdisplay", "restart"])

        self.assertEqual(args.command, "virtualdisplay")
        self.assertEqual(args.virtualdisplay_action, "restart")

    def test_parse_virtualdisplay_mangohud_fps_limit_enable_command(self) -> None:
        args = lutristosunshine.parse_args(["virtualdisplay", "mangohud-fps-limit", "enable"])

        self.assertEqual(args.command, "virtualdisplay")
        self.assertEqual(args.virtualdisplay_action, "mangohud-fps-limit")
        self.assertEqual(args.mangohud_fps_limit_action, "enable")

    def test_parse_virtualdisplay_refresh_rate_mode_exact_command(self) -> None:
        args = lutristosunshine.parse_args(["virtualdisplay", "refresh-rate-mode", "exact"])

        self.assertEqual(args.command, "virtualdisplay")
        self.assertEqual(args.virtualdisplay_action, "refresh-rate-mode")
        self.assertEqual(args.mode, "exact")

    def test_parse_virtualdisplay_refresh_rate_mode_custom_command(self) -> None:
        args = lutristosunshine.parse_args(
            ["virtualdisplay", "refresh-rate-mode", "custom", "--width", "3440", "--height", "1440", "--refresh", "59.94"]
        )

        self.assertEqual(args.command, "virtualdisplay")
        self.assertEqual(args.virtualdisplay_action, "refresh-rate-mode")
        self.assertEqual(args.mode, "custom")
        self.assertEqual(args.width, 3440)
        self.assertEqual(args.height, 1440)
        self.assertEqual(args.refresh, 59.94)

    def test_virtualdisplay_help_describes_reset_clearly(self) -> None:
        output = io.StringIO()
        with redirect_stdout(output):
            with self.assertRaises(SystemExit):
                lutristosunshine.parse_args(["virtualdisplay", "--help"])

        normalized = " ".join(output.getvalue().split())
        self.assertIn(
            "restore Sunshine app launches to normal mode",
            normalized,
        )

    def test_handle_virtualdisplay_enable_runs_setup_start_and_sync(self) -> None:
        args = lutristosunshine.parse_args(["virtualdisplay", "enable"])
        calls = []

        original_setup_virtual_display = lutristosunshine.setup_virtual_display
        original_start_virtual_display = lutristosunshine.start_virtual_display
        original_reconcile_virtual_display_apps = lutristosunshine.reconcile_virtual_display_apps
        original_is_server_running = lutristosunshine.is_server_running
        original_virtual_display_is_enabled = lutristosunshine.virtual_display_is_enabled
        original_refresh_managed_files = lutristosunshine.refresh_managed_files
        original_get_virtual_display_blocked_apps = lutristosunshine.get_virtual_display_blocked_apps
        original_get_yes_no_input = lutristosunshine.get_yes_no_input
        try:
            lutristosunshine.setup_virtual_display = lambda: calls.append("setup") or 0
            lutristosunshine.start_virtual_display = lambda: calls.append("start") or 0
            lutristosunshine.reconcile_virtual_display_apps = (
                lambda enable_virtual_display: calls.append(("sync", enable_virtual_display)) or (2, None)
            )
            lutristosunshine.is_server_running = lambda name=None: True
            lutristosunshine.virtual_display_is_enabled = lambda: True
            lutristosunshine.refresh_managed_files = lambda: calls.append("refresh")
            lutristosunshine.get_virtual_display_blocked_apps = lambda: ([], None)
            lutristosunshine.get_yes_no_input = lambda prompt, default=None: False

            result = lutristosunshine.handle_virtualdisplay_command(args)
        finally:
            lutristosunshine.setup_virtual_display = original_setup_virtual_display
            lutristosunshine.start_virtual_display = original_start_virtual_display
            lutristosunshine.reconcile_virtual_display_apps = original_reconcile_virtual_display_apps
            lutristosunshine.is_server_running = original_is_server_running
            lutristosunshine.virtual_display_is_enabled = original_virtual_display_is_enabled
            lutristosunshine.refresh_managed_files = original_refresh_managed_files
            lutristosunshine.get_virtual_display_blocked_apps = original_get_virtual_display_blocked_apps
            lutristosunshine.get_yes_no_input = original_get_yes_no_input

        self.assertEqual(result, 0)
        self.assertEqual(calls, ["setup", "start", "refresh", ("sync", True)])

    def test_handle_virtualdisplay_reset_restores_apps_then_removes_setup(self) -> None:
        args = lutristosunshine.parse_args(["virtualdisplay", "reset"])
        calls = []

        original_reconcile_virtual_display_apps = lutristosunshine.reconcile_virtual_display_apps
        original_remove_virtual_display = lutristosunshine.remove_virtual_display
        original_is_server_running = lutristosunshine.is_server_running
        try:
            lutristosunshine.reconcile_virtual_display_apps = (
                lambda enable_virtual_display: calls.append(("sync", enable_virtual_display)) or (4, None)
            )
            lutristosunshine.remove_virtual_display = lambda: calls.append("remove") or 0
            lutristosunshine.is_server_running = lambda name=None: True

            result = lutristosunshine.handle_virtualdisplay_command(args)
        finally:
            lutristosunshine.reconcile_virtual_display_apps = original_reconcile_virtual_display_apps
            lutristosunshine.remove_virtual_display = original_remove_virtual_display
            lutristosunshine.is_server_running = original_is_server_running

        self.assertEqual(result, 0)
        self.assertEqual(calls, [("sync", False), "remove"])

    def test_handle_virtualdisplay_reset_prints_clear_summary(self) -> None:
        args = lutristosunshine.parse_args(["virtualdisplay", "reset"])

        original_reconcile_virtual_display_apps = lutristosunshine.reconcile_virtual_display_apps
        original_remove_virtual_display = lutristosunshine.remove_virtual_display
        original_is_server_running = lutristosunshine.is_server_running
        try:
            lutristosunshine.reconcile_virtual_display_apps = lambda enable_virtual_display: (1, None)
            lutristosunshine.remove_virtual_display = lambda: 0
            lutristosunshine.is_server_running = lambda name=None: True

            output = io.StringIO()
            with redirect_stdout(output):
                result = lutristosunshine.handle_virtualdisplay_command(args)
        finally:
            lutristosunshine.reconcile_virtual_display_apps = original_reconcile_virtual_display_apps
            lutristosunshine.remove_virtual_display = original_remove_virtual_display
            lutristosunshine.is_server_running = original_is_server_running

        rendered = output.getvalue()
        self.assertEqual(result, 0)
        self.assertIn("Sunshine app launches were restored to normal mode", rendered)

    def test_handle_virtualdisplay_start_runs_service_start_only(self) -> None:
        args = lutristosunshine.parse_args(["virtualdisplay", "start"])
        calls = []

        original_start_virtual_display = lutristosunshine.start_virtual_display
        try:
            lutristosunshine.start_virtual_display = lambda: calls.append("start") or 0

            result = lutristosunshine.handle_virtualdisplay_command(args)
        finally:
            lutristosunshine.start_virtual_display = original_start_virtual_display

        self.assertEqual(result, 0)
        self.assertEqual(calls, ["start"])

    def test_handle_virtualdisplay_restart_runs_service_restart_only(self) -> None:
        args = lutristosunshine.parse_args(["virtualdisplay", "restart"])
        calls = []

        original_restart_virtual_display = lutristosunshine.restart_virtual_display
        try:
            lutristosunshine.restart_virtual_display = lambda: calls.append("restart") or 0

            result = lutristosunshine.handle_virtualdisplay_command(args)
        finally:
            lutristosunshine.restart_virtual_display = original_restart_virtual_display

        self.assertEqual(result, 0)
        self.assertEqual(calls, ["restart"])

    def test_handle_virtualdisplay_mangohud_fps_limit_enable_updates_setting(self) -> None:
        args = lutristosunshine.parse_args(["virtualdisplay", "mangohud-fps-limit", "enable"])
        calls = []

        original_dynamic_mangohud_fps_limit_enabled = lutristosunshine.dynamic_mangohud_fps_limit_enabled
        original_set_dynamic_mangohud_fps_limit = lutristosunshine.set_dynamic_mangohud_fps_limit
        try:
            lutristosunshine.dynamic_mangohud_fps_limit_enabled = lambda: False
            lutristosunshine.set_dynamic_mangohud_fps_limit = lambda enabled: calls.append(enabled) or {
                "dynamic_mangohud_fps_limit": enabled
            }

            output = io.StringIO()
            with redirect_stdout(output):
                result = lutristosunshine.handle_virtualdisplay_command(args)
        finally:
            lutristosunshine.dynamic_mangohud_fps_limit_enabled = original_dynamic_mangohud_fps_limit_enabled
            lutristosunshine.set_dynamic_mangohud_fps_limit = original_set_dynamic_mangohud_fps_limit

        rendered = output.getvalue()
        self.assertEqual(result, 0)
        self.assertEqual(calls, [True])
        self.assertIn("Dynamic MangoHud FPS limit enabled", rendered)

    def test_handle_virtualdisplay_refresh_rate_mode_exact_updates_setting(self) -> None:
        args = lutristosunshine.parse_args(["virtualdisplay", "refresh-rate-mode", "exact"])
        calls = []

        original_refresh_rate_sync_mode = lutristosunshine.refresh_rate_sync_mode
        original_set_refresh_rate_sync_mode = lutristosunshine.set_refresh_rate_sync_mode
        try:
            lutristosunshine.refresh_rate_sync_mode = lambda: "client"
            lutristosunshine.set_refresh_rate_sync_mode = lambda mode: calls.append(mode) or {
                "refresh_rate_sync_mode": mode
            }

            output = io.StringIO()
            with redirect_stdout(output):
                result = lutristosunshine.handle_virtualdisplay_command(args)
        finally:
            lutristosunshine.refresh_rate_sync_mode = original_refresh_rate_sync_mode
            lutristosunshine.set_refresh_rate_sync_mode = original_set_refresh_rate_sync_mode

        rendered = output.getvalue()
        self.assertEqual(result, 0)
        self.assertEqual(calls, ["exact"])
        self.assertIn("Refresh rate sync mode set to client's refresh rate", rendered)

    def test_handle_virtualdisplay_refresh_rate_mode_custom_updates_setting(self) -> None:
        args = lutristosunshine.parse_args(
            ["virtualdisplay", "refresh-rate-mode", "custom", "--width", "3440", "--height", "1440", "--refresh", "59.94"]
        )
        calls = []

        original_refresh_rate_sync_mode = lutristosunshine.refresh_rate_sync_mode
        original_set_refresh_rate_sync_mode = lutristosunshine.set_refresh_rate_sync_mode
        original_set_custom_display_mode = lutristosunshine.set_custom_display_mode
        original_custom_display_mode = lutristosunshine.custom_display_mode
        try:
            lutristosunshine.refresh_rate_sync_mode = lambda: "client"
            lutristosunshine.set_custom_display_mode = lambda width, height, refresh: calls.append(
                ("custom", width, height, refresh)
            ) or {"custom_display_mode": {"width": width, "height": height, "refresh": refresh}}
            lutristosunshine.set_refresh_rate_sync_mode = lambda mode: calls.append(("mode", mode)) or {
                "refresh_rate_sync_mode": mode
            }
            lutristosunshine.custom_display_mode = lambda: {"width": 3440, "height": 1440, "refresh": 59.94}

            output = io.StringIO()
            with redirect_stdout(output):
                result = lutristosunshine.handle_virtualdisplay_command(args)
        finally:
            lutristosunshine.refresh_rate_sync_mode = original_refresh_rate_sync_mode
            lutristosunshine.set_refresh_rate_sync_mode = original_set_refresh_rate_sync_mode
            lutristosunshine.set_custom_display_mode = original_set_custom_display_mode
            lutristosunshine.custom_display_mode = original_custom_display_mode

        rendered = output.getvalue()
        self.assertEqual(result, 0)
        self.assertEqual(calls, [("custom", 3440, 1440, 59.94), ("mode", "custom")])
        self.assertIn("Refresh rate sync mode set to custom fixed display mode", rendered)
        self.assertIn("Custom display target: 3440x1440 @ 59.94 Hz", rendered)

    def test_handle_virtualdisplay_without_subcommand_opens_hub(self) -> None:
        args = lutristosunshine.parse_args(["virtualdisplay"])

        original_virtual_display_snapshot = lutristosunshine.virtual_display_snapshot
        original_get_virtual_display_blocked_apps = lutristosunshine.get_virtual_display_blocked_apps
        original_get_menu_choice = lutristosunshine.get_menu_choice
        try:
            lutristosunshine.virtual_display_snapshot = lambda: {
                "configured": False,
                "dynamic_mangohud_fps_limit": False,
                "current_mangohud_config": "",
                "refresh_rate_sync_mode": "client",
                "host_session": "unknown",
                "input_isolation_mode": "permissions-only",
                "sunshine_active": False,
                "sway_active": False,
                "bridge_state": "inactive",
                "audio_guard_state": "inactive",
                "portal_handoff_active": False,
                "dependencies_missing": [],
                "wayland_display": "",
                "current_headless_mode": "",
                "controller_detection_error": None,
                "controller_count": 0,
                "controllers": [],
                "next_step": "Run enable.",
            }
            lutristosunshine.get_virtual_display_blocked_apps = lambda: ([], None)
            lutristosunshine.get_menu_choice = lambda prompt, valid_choices: "0"
            output = io.StringIO()
            with redirect_stdout(output):
                result = lutristosunshine.handle_virtualdisplay_command(args)
        finally:
            lutristosunshine.virtual_display_snapshot = original_virtual_display_snapshot
            lutristosunshine.get_virtual_display_blocked_apps = original_get_virtual_display_blocked_apps
            lutristosunshine.get_menu_choice = original_get_menu_choice

        rendered = output.getvalue()
        self.assertEqual(result, 0)
        self.assertIn("Show full status", rendered)
        self.assertIn("Advanced tools", rendered)
        self.assertNotIn("Run doctor", rendered)
        self.assertNotIn("Host session:", rendered)

    def test_handle_virtualdisplay_advanced_tools_lists_service_controls_together(self) -> None:
        args = lutristosunshine.parse_args(["virtualdisplay"])

        original_virtual_display_snapshot = lutristosunshine.virtual_display_snapshot
        original_get_virtual_display_blocked_apps = lutristosunshine.get_virtual_display_blocked_apps
        original_get_menu_choice = lutristosunshine.get_menu_choice
        choices = iter(["5", "0", "0"])
        try:
            lutristosunshine.virtual_display_snapshot = lambda: {
                "configured": True,
                "dynamic_mangohud_fps_limit": False,
                "current_mangohud_config": "",
                "refresh_rate_sync_mode": "client",
                "custom_display_mode": {"width": 1920, "height": 1080, "refresh": 60.0},
                "host_session": "unknown",
                "input_isolation_mode": "permissions-only",
                "sunshine_active": False,
                "sway_active": False,
                "bridge_state": "inactive",
                "audio_guard_state": "inactive",
                "portal_handoff_active": False,
                "dependencies_missing": [],
                "wayland_display": "",
                "current_headless_mode": "",
                "controller_detection_error": None,
                "controller_count": 0,
                "controllers": [],
                "next_step": "Run start.",
            }
            lutristosunshine.get_virtual_display_blocked_apps = lambda: ([], None)
            lutristosunshine.get_menu_choice = lambda prompt, valid_choices: next(choices)
            output = io.StringIO()
            with redirect_stdout(output):
                result = lutristosunshine.handle_virtualdisplay_command(args)
        finally:
            lutristosunshine.virtual_display_snapshot = original_virtual_display_snapshot
            lutristosunshine.get_virtual_display_blocked_apps = original_get_virtual_display_blocked_apps
            lutristosunshine.get_menu_choice = original_get_menu_choice

        rendered = output.getvalue()
        self.assertEqual(result, 0)
        self.assertIn("Advanced tools", rendered)
        self.assertIn("Start Sunshine service", rendered)
        self.assertIn("Stop Sunshine service", rendered)
        self.assertIn("Restart Sunshine service", rendered)

    def test_handle_virtualdisplay_status_renders_plain_dashboard_without_ansi(self) -> None:
        args = lutristosunshine.parse_args(["virtualdisplay", "status"])

        original_virtual_display_snapshot = lutristosunshine.virtual_display_snapshot
        original_get_virtual_display_blocked_apps = lutristosunshine.get_virtual_display_blocked_apps
        try:
            lutristosunshine.virtual_display_snapshot = lambda: {
                "configured": True,
                "dynamic_mangohud_fps_limit": True,
                "current_mangohud_config": "read_cfg,fps_limit=59.94",
                "refresh_rate_sync_mode": "exact",
                "host_session": "plasma",
                "input_isolation_mode": "kwin-runtime-disable",
                "sunshine_active": True,
                "sway_active": False,
                "bridge_state": "starting",
                "audio_guard_state": "inactive",
                "portal_handoff_active": False,
                "dependencies_missing": [],
                "wayland_display": "",
                "current_headless_mode": "2560x1440 @ 120 Hz",
                "kwin_isolation_error": "",
                "kwin_isolation_state": "inactive",
                "kwin_isolation_devices": [],
                "kwin_isolation_seen_device_count": 0,
                "sunshine_input_device_count": 0,
                "kwin_isolation_failed_devices": [],
                "controller_detection_error": None,
                "controller_count": 1,
                "controllers": [
                    {
                        "label": "Wireless Controller",
                        "state": "detected",
                        "details": "/dev/input/event256",
                    }
                ],
                "next_step": "Run doctor.",
            }
            lutristosunshine.get_virtual_display_blocked_apps = lambda: ([], None)

            output = io.StringIO()
            with redirect_stdout(output):
                result = lutristosunshine.handle_virtualdisplay_command(args)
        finally:
            lutristosunshine.virtual_display_snapshot = original_virtual_display_snapshot
            lutristosunshine.get_virtual_display_blocked_apps = original_get_virtual_display_blocked_apps

        rendered = output.getvalue()
        self.assertEqual(result, 0)
        self.assertIn("Virtual display", rendered)
        self.assertIn("Dynamic MangoHud FPS limit: [ENABLED]", rendered)
        self.assertIn("MangoHud env value: read_cfg,fps_limit=59.94", rendered)
        self.assertIn("Refresh rate sync mode: client's refresh rate", rendered)
        self.assertIn("Current headless mode: 2560x1440 @ 120 Hz", rendered)
        self.assertIn("Dependencies: [OK]", rendered)
        self.assertIn("Wireless Controller [DETECTED]", rendered)
        self.assertNotIn("\033[", rendered)


if __name__ == "__main__":
    unittest.main()
