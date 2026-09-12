import io
import unittest
from contextlib import redirect_stdout

import lutristosunshine
from display import hub
from sunshine import catalog
from sunshine.connection import CONNECTION


class DisplayCliTests(unittest.TestCase):
    def test_parse_display_without_subcommand(self) -> None:
        args = lutristosunshine.parse_args(["display"])

        self.assertEqual(args.command, "display")
        self.assertIsNone(args.display_action)

    def test_parse_display_enable_command(self) -> None:
        args = lutristosunshine.parse_args(["display", "enable"])

        self.assertEqual(args.command, "display")
        self.assertEqual(args.display_action, "enable")

    def test_parse_display_start_command(self) -> None:
        args = lutristosunshine.parse_args(["display", "start"])

        self.assertEqual(args.command, "display")
        self.assertEqual(args.display_action, "start")

    def test_parse_display_restart_command(self) -> None:
        args = lutristosunshine.parse_args(["display", "restart"])

        self.assertEqual(args.command, "display")
        self.assertEqual(args.display_action, "restart")

    def test_parse_display_mangohud_fps_limit_enable_command(self) -> None:
        args = lutristosunshine.parse_args(["display", "mangohud-fps-limit", "enable"])

        self.assertEqual(args.command, "display")
        self.assertEqual(args.display_action, "mangohud-fps-limit")
        self.assertEqual(args.mangohud_fps_limit_action, "enable")

    def test_parse_display_refresh_rate_mode_exact_command(self) -> None:
        args = lutristosunshine.parse_args(["display", "refresh-rate-mode", "exact"])

        self.assertEqual(args.command, "display")
        self.assertEqual(args.display_action, "refresh-rate-mode")
        self.assertEqual(args.mode, "exact")

    def test_parse_display_refresh_rate_mode_custom_command(self) -> None:
        args = lutristosunshine.parse_args(
            ["display", "refresh-rate-mode", "custom", "--width", "3440", "--height", "1440", "--refresh", "59.94"]
        )

        self.assertEqual(args.command, "display")
        self.assertEqual(args.display_action, "refresh-rate-mode")
        self.assertEqual(args.mode, "custom")
        self.assertEqual(args.width, 3440)
        self.assertEqual(args.height, 1440)
        self.assertEqual(args.refresh, 59.94)

    def test_display_help_describes_reset_clearly(self) -> None:
        output = io.StringIO()
        with redirect_stdout(output):
            with self.assertRaises(SystemExit):
                lutristosunshine.parse_args(["display", "--help"])

        normalized = " ".join(output.getvalue().split())
        self.assertIn(
            "restore Sunshine app launches to normal mode",
            normalized,
        )

    def test_handle_display_enable_runs_setup_start_and_sync(self) -> None:
        args = lutristosunshine.parse_args(["display", "enable"])
        calls = []

        original_setup_display = hub.setup_display
        original_start_display = hub.start_display
        original_reconcile_display_apps = catalog.reconcile_display_apps
        original_is_server_running = CONNECTION.is_server_running
        original_display_is_enabled = hub.is_enabled
        original_refresh_managed_files = hub.refresh_managed_files
        original_get_display_blocked_apps = catalog.get_display_blocked_apps
        original_get_yes_no_input = hub.get_yes_no_input
        try:
            hub.setup_display = lambda: calls.append("setup") or 0
            hub.start_display = lambda: calls.append("start") or 0
            catalog.reconcile_display_apps = (
                lambda enable_display: calls.append(("sync", enable_display)) or (2, None)
            )
            CONNECTION.is_server_running = lambda name=None: True
            hub.is_enabled = lambda: True
            hub.refresh_managed_files = lambda: calls.append("refresh")
            catalog.get_display_blocked_apps = lambda: ([], None)
            hub.get_yes_no_input = lambda prompt, default=None: False

            result = lutristosunshine.handle_display_command(args)
        finally:
            hub.setup_display = original_setup_display
            hub.start_display = original_start_display
            catalog.reconcile_display_apps = original_reconcile_display_apps
            CONNECTION.is_server_running = original_is_server_running
            hub.is_enabled = original_display_is_enabled
            hub.refresh_managed_files = original_refresh_managed_files
            catalog.get_display_blocked_apps = original_get_display_blocked_apps
            hub.get_yes_no_input = original_get_yes_no_input

        self.assertEqual(result, 0)
        self.assertEqual(calls, ["setup", "start", "refresh", ("sync", True)])

    def test_handle_display_reset_restores_apps_then_removes_setup(self) -> None:
        args = lutristosunshine.parse_args(["display", "reset"])
        calls = []

        original_reconcile_display_apps = catalog.reconcile_display_apps
        original_remove_display = hub.remove_display
        original_is_server_running = CONNECTION.is_server_running
        try:
            catalog.reconcile_display_apps = (
                lambda enable_display: calls.append(("sync", enable_display)) or (4, None)
            )
            hub.remove_display = lambda: calls.append("remove") or 0
            CONNECTION.is_server_running = lambda name=None: True

            result = lutristosunshine.handle_display_command(args)
        finally:
            catalog.reconcile_display_apps = original_reconcile_display_apps
            hub.remove_display = original_remove_display
            CONNECTION.is_server_running = original_is_server_running

        self.assertEqual(result, 0)
        self.assertEqual(calls, [("sync", False), "remove"])

    def test_handle_display_reset_prints_clear_summary(self) -> None:
        args = lutristosunshine.parse_args(["display", "reset"])

        original_reconcile_display_apps = catalog.reconcile_display_apps
        original_remove_display = hub.remove_display
        original_is_server_running = CONNECTION.is_server_running
        try:
            catalog.reconcile_display_apps = lambda enable_display: (1, None)
            hub.remove_display = lambda: 0
            CONNECTION.is_server_running = lambda name=None: True

            output = io.StringIO()
            with redirect_stdout(output):
                result = lutristosunshine.handle_display_command(args)
        finally:
            catalog.reconcile_display_apps = original_reconcile_display_apps
            hub.remove_display = original_remove_display
            CONNECTION.is_server_running = original_is_server_running

        rendered = output.getvalue()
        self.assertEqual(result, 0)
        self.assertIn("Headless streaming removed. Sunshine apps restored to normal.", rendered)

    def test_handle_display_start_runs_service_start_only(self) -> None:
        args = lutristosunshine.parse_args(["display", "start"])
        calls = []

        original_start_display = hub.start_display
        try:
            hub.start_display = lambda: calls.append("start") or 0

            result = lutristosunshine.handle_display_command(args)
        finally:
            hub.start_display = original_start_display

        self.assertEqual(result, 0)
        self.assertEqual(calls, ["start"])

    def test_handle_display_restart_runs_service_restart_only(self) -> None:
        args = lutristosunshine.parse_args(["display", "restart"])
        calls = []

        original_restart_display = hub.restart_display
        try:
            hub.restart_display = lambda: calls.append("restart") or 0

            result = lutristosunshine.handle_display_command(args)
        finally:
            hub.restart_display = original_restart_display

        self.assertEqual(result, 0)
        self.assertEqual(calls, ["restart"])

    def test_handle_display_mangohud_fps_limit_enable_updates_setting(self) -> None:
        args = lutristosunshine.parse_args(["display", "mangohud-fps-limit", "enable"])
        calls = []

        original_set_dynamic_mangohud_fps_limit = hub.set_dynamic_mangohud_fps_limit
        try:
            hub.set_dynamic_mangohud_fps_limit = lambda enabled: calls.append(enabled) or (
                False,
                {"dynamic_mangohud_fps_limit": enabled},
            )

            output = io.StringIO()
            with redirect_stdout(output):
                result = lutristosunshine.handle_display_command(args)
        finally:
            hub.set_dynamic_mangohud_fps_limit = original_set_dynamic_mangohud_fps_limit

        rendered = output.getvalue()
        self.assertEqual(result, 0)
        self.assertEqual(calls, [True])
        self.assertIn("Auto FPS limit turned on.", rendered)

    def test_handle_display_refresh_rate_mode_exact_updates_setting(self) -> None:
        args = lutristosunshine.parse_args(["display", "refresh-rate-mode", "exact"])
        calls = []

        original_set_refresh_rate_sync_mode = hub.set_refresh_rate_sync_mode
        try:
            hub.set_refresh_rate_sync_mode = lambda mode: calls.append(mode) or (
                "client",
                {"refresh_rate_sync_mode": mode},
            )

            output = io.StringIO()
            with redirect_stdout(output):
                result = lutristosunshine.handle_display_command(args)
        finally:
            hub.set_refresh_rate_sync_mode = original_set_refresh_rate_sync_mode

        rendered = output.getvalue()
        self.assertEqual(result, 0)
        self.assertEqual(calls, ["exact"])
        self.assertIn(
            "Refresh rate sync mode set to client's exact refresh rate (fractional, e.g. 59.94/119.88)",
            rendered,
        )

    def test_handle_display_refresh_rate_mode_custom_updates_setting(self) -> None:
        args = lutristosunshine.parse_args(
            ["display", "refresh-rate-mode", "custom", "--width", "3440", "--height", "1440", "--refresh", "59.94"]
        )
        calls = []

        original_set_refresh_rate_sync_mode = hub.set_refresh_rate_sync_mode
        original_set_custom_display_mode = hub.set_custom_display_mode
        original_display_snapshot = hub.display_snapshot
        try:
            hub.set_custom_display_mode = lambda width, height, refresh: calls.append(
                ("custom", width, height, refresh)
            ) or ({"width": 1920, "height": 1080, "refresh": 60.0}, {"custom_display_mode": {"width": width, "height": height, "refresh": refresh}})
            hub.set_refresh_rate_sync_mode = lambda mode: calls.append(("mode", mode)) or (
                "client",
                {"refresh_rate_sync_mode": mode},
            )
            hub.display_snapshot = lambda: {
                "custom_display_mode_summary": "3440x1440 @ 59.94 Hz"
            }

            output = io.StringIO()
            with redirect_stdout(output):
                result = lutristosunshine.handle_display_command(args)
        finally:
            hub.set_refresh_rate_sync_mode = original_set_refresh_rate_sync_mode
            hub.set_custom_display_mode = original_set_custom_display_mode
            hub.display_snapshot = original_display_snapshot

        rendered = output.getvalue()
        self.assertEqual(result, 0)
        self.assertEqual(calls, [("custom", 3440, 1440, 59.94), ("mode", "custom")])
        self.assertIn("Refresh rate sync mode set to custom fixed display mode", rendered)
        self.assertIn("Custom display target: 3440x1440 @ 59.94 Hz", rendered)

    def test_handle_display_without_subcommand_opens_hub(self) -> None:
        args = lutristosunshine.parse_args(["display"])

        original_display_snapshot = hub.display_snapshot
        original_get_display_blocked_apps = catalog.get_display_blocked_apps
        original_get_menu_choice = hub.get_menu_choice
        try:
            hub.display_snapshot = lambda: {
                "configured": False,
                "dynamic_mangohud_fps_limit": False,
                "current_mangohud_config": "",
                "refresh_rate_sync_mode": "client",
                "host_session": "unknown",
                "input_isolation_mode": "permissions-only",
                "sunshine_active": False,
                "sway_active": False,
                "bridge_state": "inactive",
                "wireplumber_policy": "absent",
                "portal_handoff_active": False,
                "dependencies_missing": [],
                "wayland_display": "",
                "current_headless_mode": "",
                "controller_detection_error": None,
                "controller_count": 0,
                "controllers": [],
                "gpu_status_label": "[AUTO] wlroots chooses GPU",
                "renderer_mode": "default",
                "renderer_status_label": "[DEFAULT] wlroots default renderer",
                "next_step": "Run enable.",
                "status_summary": "NOT SET UP",
                "display_sync_summary": "Moonlight's requested FPS (integer, e.g. 60/90/120); MangoHud FPS sync off",
                "refresh_rate_sync_mode_summary": "Moonlight's requested FPS (integer, e.g. 60/90/120)",
                "isolation_summary": "rule ready",
                "isolation_level": "success",
            }
            catalog.get_display_blocked_apps = lambda: ([], None)
            hub.get_menu_choice = lambda prompt, valid_choices: "0"
            output = io.StringIO()
            with redirect_stdout(output):
                result = lutristosunshine.handle_display_command(args)
        finally:
            hub.display_snapshot = original_display_snapshot
            catalog.get_display_blocked_apps = original_get_display_blocked_apps
            hub.get_menu_choice = original_get_menu_choice

        rendered = output.getvalue()
        self.assertEqual(result, 0)
        self.assertIn("Show full status", rendered)
        self.assertIn("More tools", rendered)
        self.assertNotIn("Run doctor", rendered)
        self.assertNotIn("Host session:", rendered)

    def test_handle_display_advanced_tools_lists_service_controls_together(self) -> None:
        args = lutristosunshine.parse_args(["display"])

        original_display_snapshot = hub.display_snapshot
        original_get_display_blocked_apps = catalog.get_display_blocked_apps
        original_get_menu_choice = hub.get_menu_choice
        choices = iter(["6", "0", "0"])
        try:
            hub.display_snapshot = lambda: {
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
                "wireplumber_policy": "installed",
                "portal_handoff_active": False,
                "dependencies_missing": [],
                "wayland_display": "",
                "current_headless_mode": "",
                "gpu_status_label": "[AUTO] wlroots chooses GPU",
                "renderer_mode": "default",
                "renderer_status_label": "[DEFAULT] wlroots default renderer",
                "next_step": "Run start.",
                "status_summary": "STOPPED",
                "display_sync_summary": "Moonlight's requested FPS (integer, e.g. 60/90/120); MangoHud FPS sync off",
                "refresh_rate_sync_mode_summary": "Moonlight's requested FPS (integer, e.g. 60/90/120)",
                "isolation_summary": "rule ready",
                "isolation_level": "success",
            }
            catalog.get_display_blocked_apps = lambda: ([], None)
            hub.get_menu_choice = lambda prompt, valid_choices: next(choices)
            output = io.StringIO()
            with redirect_stdout(output):
                result = lutristosunshine.handle_display_command(args)
        finally:
            hub.display_snapshot = original_display_snapshot
            catalog.get_display_blocked_apps = original_get_display_blocked_apps
            hub.get_menu_choice = original_get_menu_choice

        rendered = output.getvalue()
        self.assertEqual(result, 0)
        self.assertIn("More tools", rendered)
        self.assertIn("Start Sunshine", rendered)
        self.assertIn("Stop Sunshine", rendered)
        self.assertIn("Restart Sunshine", rendered)

    def test_handle_display_status_renders_plain_dashboard_without_ansi(self) -> None:
        args = lutristosunshine.parse_args(["display", "status"])

        original_display_snapshot = hub.display_snapshot
        original_get_display_blocked_apps = catalog.get_display_blocked_apps
        try:
            hub.display_snapshot = lambda: {
                "configured": True,
                "dynamic_mangohud_fps_limit": True,
                "current_mangohud_config": "read_cfg,fps_limit=59.94",
                "refresh_rate_sync_mode": "exact",
                "host_session": "plasma",
                "input_isolation_mode": "kwin-runtime-disable",
                "sunshine_active": True,
                "sway_active": False,
                "bridge_state": "starting",
                "wireplumber_policy": "installed",
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
                "gpu_status_label": "[AUTO] wlroots chooses GPU",
                "renderer_mode": "default",
                "renderer_status_label": "[DEFAULT] wlroots default renderer",
                "next_step": "Run doctor.",
                "status_summary": "PARTIAL",
                "display_sync_summary": "client's exact refresh rate (fractional, e.g. 59.94/119.88); MangoHud FPS sync on",
                "refresh_rate_sync_mode_summary": "client's exact refresh rate (fractional, e.g. 59.94/119.88)",
                "isolation_summary": "KWin helper is not running",
                "isolation_level": "warning",
            }
            catalog.get_display_blocked_apps = lambda: ([], None)

            output = io.StringIO()
            with redirect_stdout(output):
                result = lutristosunshine.handle_display_command(args)
        finally:
            hub.display_snapshot = original_display_snapshot
            catalog.get_display_blocked_apps = original_get_display_blocked_apps

        rendered = output.getvalue()
        self.assertEqual(result, 0)
        self.assertIn("Virtual display", rendered)
        self.assertIn("Auto FPS limit (MangoHud): [ENABLED]", rendered)
        self.assertIn("MangoHud env value: read_cfg,fps_limit=59.94", rendered)
        self.assertIn(
            "Refresh rate sync mode: client's exact refresh rate (fractional, e.g. 59.94/119.88)",
            rendered,
        )
        self.assertIn("Current headless mode: 2560x1440 @ 120 Hz", rendered)
        self.assertIn("Dependencies: [OK]", rendered)
        self.assertNotIn("\033[", rendered)


if __name__ == "__main__":
    unittest.main()
