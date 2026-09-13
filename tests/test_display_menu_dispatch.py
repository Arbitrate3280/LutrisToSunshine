"""Dispatch tests for every interactive menu option.

The hub menu is the primary interface, but only "exit" and "open More tools"
were covered: a renamed handler, a wrong menu index, or a typo in the
dispatch chain would go unnoticed. These tests drive the real menus with the
handler bodies recorded, so each option is asserted to reach the function it
promises (and cancel paths to reach nothing).
"""

import contextlib
import io
import unittest
from unittest.mock import patch

from display import hub
from tests._display_test_helpers import snapshot_fixture


SNAPSHOT = snapshot_fixture(
    configured=True,
    status_summary="STOPPED",
    next_step="Run start.",
)

# Every module-level entry point the two menus can reach.
HANDLERS = (
    "enable",
    "print_dashboard",
    "set_sync_mode",
    "configure_custom_mode",
    "configure_gpu",
    "configure_renderer_mode",
    "start",
    "stop_display",
    "restart",
    "set_fps_limit",
    "display_logs",
    "reset",
)


class MenuDispatchTests(unittest.TestCase):
    def _drive(self, choices, *, confirm=True, handler_results=None):
        """Run the real menus, feeding ``choices``; return (result, calls)."""
        self.choices = iter(choices)
        self.calls = []
        handler_results = handler_results or {}

        def recorder(name):
            def fn(*args, **kwargs):
                self.calls.append((name, args, kwargs))
                return handler_results.get(name, 0)
            return fn

        with contextlib.ExitStack() as stack:
            stack.enter_context(patch.object(hub, "print_hub_overview", lambda: None))
            stack.enter_context(patch.object(hub, "display_snapshot", lambda: dict(SNAPSHOT)))
            stack.enter_context(
                patch.object(hub, "get_menu_choice", lambda prompt, valid: next(self.choices))
            )
            stack.enter_context(
                patch.object(hub, "get_yes_no_input", lambda prompt, default=True: confirm)
            )
            for name in HANDLERS:
                stack.enter_context(patch.object(hub, name, recorder(name)))
            with contextlib.redirect_stdout(io.StringIO()):
                result = hub.run_hub()
        return result, self.calls

    def _only(self, calls):
        return [name for name, _, _ in calls]

    def test_exit_does_nothing(self) -> None:
        result, calls = self._drive(["0"])

        self.assertEqual(result, 0)
        self.assertEqual(calls, [])

    def test_option_1_sets_up_headless_streaming(self) -> None:
        result, calls = self._drive(["1", "0"])

        self.assertEqual(result, 0)
        self.assertEqual(self._only(calls), ["enable"])

    def test_option_1_propagates_failure(self) -> None:
        result, calls = self._drive(["1", "0"], handler_results={"enable": 1})

        self.assertEqual(result, 1)
        self.assertEqual(self._only(calls), ["enable"])

    def test_option_2_shows_full_status(self) -> None:
        result, calls = self._drive(["2", "0"])

        self.assertEqual(result, 0)
        self.assertEqual(self._only(calls), ["print_dashboard"])

    def test_option_3_sync_modes(self) -> None:
        for menu_choice, expected_mode in (("1", "client"), ("2", "exact")):
            with self.subTest(mode=expected_mode):
                result, calls = self._drive(["3", menu_choice, "0"])

                self.assertEqual(result, 0)
                self.assertEqual(self._only(calls), ["set_sync_mode"])
                self.assertEqual(calls[0][1], (expected_mode,))

    def test_option_3_custom_mode_opens_the_prompt(self) -> None:
        result, calls = self._drive(["3", "3", "0"])

        self.assertEqual(result, 0)
        self.assertEqual(self._only(calls), ["configure_custom_mode"])
        self.assertEqual(calls[0][2], {"interactive": True})

    def test_option_3_cancel_does_nothing(self) -> None:
        result, calls = self._drive(["3", "0", "0"])

        self.assertEqual(result, 0)
        self.assertEqual(calls, [])

    def test_option_4_chooses_gpu(self) -> None:
        result, calls = self._drive(["4", "0"])

        self.assertEqual(result, 0)
        self.assertEqual(self._only(calls), ["configure_gpu"])

    def test_option_5_chooses_renderer(self) -> None:
        result, calls = self._drive(["5", "0"])

        self.assertEqual(result, 0)
        self.assertEqual(self._only(calls), ["configure_renderer_mode"])

    def test_tools_1_start_sunshine(self) -> None:
        result, calls = self._drive(["6", "1", "0", "0"])

        self.assertEqual(result, 0)
        self.assertEqual(self._only(calls), ["start"])

    def test_tools_2_stop_sunshine(self) -> None:
        result, calls = self._drive(["6", "2", "0", "0"])

        self.assertEqual(result, 0)
        self.assertEqual(self._only(calls), ["stop_display"])

    def test_tools_3_restart_sunshine(self) -> None:
        result, calls = self._drive(["6", "3", "0", "0"])

        self.assertEqual(result, 0)
        self.assertEqual(self._only(calls), ["restart"])

    def test_tools_4_toggles_auto_fps_limit(self) -> None:
        result, calls = self._drive(["6", "4", "0", "0"])

        self.assertEqual(result, 0)
        self.assertEqual(self._only(calls), ["set_fps_limit"])
        # Snapshot reports the limit is off, so the tool enables it.
        self.assertEqual(calls[0][1], (True,))

    def test_tools_4_declined_toggle_does_nothing(self) -> None:
        result, calls = self._drive(["6", "4", "0", "0"], confirm=False)

        self.assertEqual(result, 0)
        self.assertEqual(calls, [])

    def test_tools_5_shows_logs(self) -> None:
        result, calls = self._drive(["6", "5", "0", "0"])

        self.assertEqual(result, 0)
        self.assertEqual(self._only(calls), ["display_logs"])
        self.assertEqual(calls[0][1], (80,))

    def test_tools_6_removes_headless_streaming(self) -> None:
        result, calls = self._drive(["6", "6", "0"])

        self.assertEqual(result, 0)
        self.assertEqual(self._only(calls), ["reset"])

    def test_tools_6_declined_removal_does_nothing(self) -> None:
        result, calls = self._drive(["6", "6", "0", "0"], confirm=False)

        self.assertEqual(result, 0)
        self.assertEqual(calls, [])

    def test_tools_back_returns_to_the_actions_menu(self) -> None:
        result, calls = self._drive(["6", "0", "2", "0"])

        self.assertEqual(result, 0)
        self.assertEqual(self._only(calls), ["print_dashboard"])

    def test_menu_offers_only_rendered_options(self) -> None:
        # The prompt validator gets exactly the options the menu printed.
        seen = []
        self.choices = iter(["0"])
        with contextlib.ExitStack() as stack:
            stack.enter_context(patch.object(hub, "print_hub_overview", lambda: None))
            stack.enter_context(patch.object(hub, "display_snapshot", lambda: dict(SNAPSHOT)))
            stack.enter_context(
                patch.object(
                    hub,
                    "get_menu_choice",
                    lambda prompt, valid: seen.append(list(valid)) or "0",
                )
            )
            with contextlib.redirect_stdout(io.StringIO()):
                hub.run_hub()

        self.assertEqual(seen, [["0", "1", "2", "3", "4", "5", "6"]])


if __name__ == "__main__":
    unittest.main()
