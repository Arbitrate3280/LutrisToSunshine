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

    def test_handle_virtualdisplay_without_subcommand_opens_hub(self) -> None:
        args = lutristosunshine.parse_args(["virtualdisplay"])

        original_virtual_display_snapshot = lutristosunshine.virtual_display_snapshot
        original_get_virtual_display_blocked_apps = lutristosunshine.get_virtual_display_blocked_apps
        original_get_menu_choice = lutristosunshine.get_menu_choice
        try:
            lutristosunshine.virtual_display_snapshot = lambda: {
                "configured": False,
                "sunshine_active": False,
                "sway_active": False,
                "bridge_state": "inactive",
                "audio_guard_state": "inactive",
                "portal_handoff_active": False,
                "dependencies_missing": [],
                "wayland_display": "",
                "controller_detection_error": None,
                "controller_count": 0,
                "controllers": [],
                "next_step": "Run enable.",
            }
            lutristosunshine.get_virtual_display_blocked_apps = lambda: ([], None)
            lutristosunshine.get_menu_choice = lambda prompt, valid_choices: "0"

            result = lutristosunshine.handle_virtualdisplay_command(args)
        finally:
            lutristosunshine.virtual_display_snapshot = original_virtual_display_snapshot
            lutristosunshine.get_virtual_display_blocked_apps = original_get_virtual_display_blocked_apps
            lutristosunshine.get_menu_choice = original_get_menu_choice

        self.assertEqual(result, 0)

    def test_handle_virtualdisplay_status_renders_plain_dashboard_without_ansi(self) -> None:
        args = lutristosunshine.parse_args(["virtualdisplay", "status"])

        original_virtual_display_snapshot = lutristosunshine.virtual_display_snapshot
        original_get_virtual_display_blocked_apps = lutristosunshine.get_virtual_display_blocked_apps
        try:
            lutristosunshine.virtual_display_snapshot = lambda: {
                "configured": True,
                "sunshine_active": True,
                "sway_active": False,
                "bridge_state": "starting",
                "audio_guard_state": "inactive",
                "portal_handoff_active": False,
                "dependencies_missing": [],
                "wayland_display": "",
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
        self.assertIn("Dependencies: [OK]", rendered)
        self.assertIn("Wireless Controller [DETECTED]", rendered)
        self.assertNotIn("\033[", rendered)


if __name__ == "__main__":
    unittest.main()
