"""Tests for the Sunshine app <-> display launch transform.

Regression coverage for launch commands older releases stored behind a
``flatpak-spawn --host`` escape: ``is_wrapped_command`` compared ``parts[0]``
against the managed script path, so the escaped form looked like an ordinary
game command and got wrapped a second time. The result was a redundant extra
host hop on every launch (a bare ``flatpak-spawn`` drops the
``SUNSHINE_CLIENT_*`` env the wrapper forwards) and, after "Remove headless
streaming", an app command pointing at the managed wrapper that reset deletes.
"""

import unittest

from display import command_wrap
from display.app_transform import (
    disable_display_launch,
    enable_display_launch,
    transform_app_for_display,
)

GAME = "flatpak run com.google.Chrome --app-id=hbcaficcbgghlkcjknpnemjjlpdpjgld"


def escaped_wrapper(command: str, origin: str = "cmd", timeout: int = 5) -> str:
    """Return the host-escaped managed wrapper older releases stored."""
    inner = command_wrap.wrap_command(command, origin, timeout)
    return f"flatpak-spawn --host {inner}"


def app_with(cmd: str, detached=None, exit_timeout: int = 5) -> dict:
    return {"name": "Game", "cmd": cmd, "detached": detached or [], "exit-timeout": exit_timeout}


class EnableDisplayLaunchTests(unittest.TestCase):
    def test_plain_command_is_wrapped_once(self) -> None:
        self.assertEqual(enable_display_launch(app_with(GAME))[0], command_wrap.wrap_command(GAME, "cmd", 5))

    def test_escaped_wrapper_is_not_wrapped_again(self) -> None:
        cmd, _ = enable_display_launch(app_with(escaped_wrapper(GAME)))

        self.assertEqual(cmd, command_wrap.wrap_command(GAME, "cmd", 5))

    def test_nested_wrapper_collapses_to_a_single_one(self) -> None:
        nested = command_wrap.wrap_command(escaped_wrapper(GAME), "cmd", 5)

        cmd, _ = enable_display_launch(app_with(nested))

        self.assertEqual(cmd, command_wrap.wrap_command(GAME, "cmd", 5))

    def test_enable_is_idempotent(self) -> None:
        once, _ = enable_display_launch(app_with(GAME))
        twice, _ = enable_display_launch(app_with(once))

        self.assertEqual(twice, once)

    def test_detached_commands_keep_their_escape_free_payload(self) -> None:
        cmd, detached = enable_display_launch(
            app_with(escaped_wrapper(GAME), detached=[escaped_wrapper("notify-send hi")])
        )

        self.assertEqual(cmd, command_wrap.wrap_command(GAME, "cmd", 5))
        self.assertEqual(detached, ["notify-send hi"])


class DisableDisplayLaunchTests(unittest.TestCase):
    def test_restore_returns_the_original_game_command(self) -> None:
        cmd, detached = disable_display_launch(app_with(command_wrap.wrap_command(GAME, "cmd", 5)))

        self.assertEqual(cmd, GAME)
        self.assertEqual(detached, [])

    def test_restore_from_a_nested_command_does_not_leave_a_wrapper(self) -> None:
        # The pre-fix behaviour restored the escaped wrapper instead, which
        # remove_display() then deletes.
        nested = command_wrap.wrap_command(escaped_wrapper(GAME), "cmd", 5)

        cmd, detached = disable_display_launch(app_with(nested))

        self.assertEqual(cmd, GAME)
        self.assertEqual(detached, [])
        self.assertNotIn(command_wrap.get_launch_app_script(), cmd)

    def test_restore_keeps_detached_placement(self) -> None:
        cmd, detached = disable_display_launch(
            app_with("", detached=[command_wrap.wrap_command(GAME, "detached", 5)])
        )

        self.assertEqual(cmd, "")
        self.assertEqual(detached, [GAME])

    def test_restore_moves_cmd_origin_payload_back_to_cmd(self) -> None:
        cmd, detached = disable_display_launch(
            app_with("", detached=[command_wrap.wrap_command(GAME, "cmd", 5)])
        )

        self.assertEqual(cmd, GAME)
        self.assertEqual(detached, [])

    def test_escaped_wrapper_origin_is_read_through_the_host_escape(self) -> None:
        # Pre-fix the origin parsed as "--host", so a cmd-origin payload was
        # restored into the detached list instead of the cmd field.
        cmd, detached = disable_display_launch(
            app_with("", detached=[escaped_wrapper(GAME, "cmd")])
        )

        self.assertEqual(cmd, GAME)
        self.assertEqual(detached, [])


class TransformAppForDisplayTests(unittest.TestCase):
    def test_enable_then_disable_round_trips_the_game_command(self) -> None:
        app = app_with(GAME)

        enabled = transform_app_for_display(app, True, True)
        restored = transform_app_for_display(enabled, False, True)

        self.assertEqual(restored["cmd"], GAME)
        self.assertEqual(restored["detached"], [])

    def test_reconciling_a_nested_app_converges_to_one_wrapper(self) -> None:
        app = app_with(command_wrap.wrap_command(escaped_wrapper(GAME), "cmd", 5))

        reconciled = transform_app_for_display(app, True, True)
        again = transform_app_for_display(reconciled, True, True)

        self.assertEqual(reconciled["cmd"], command_wrap.wrap_command(GAME, "cmd", 5))
        self.assertEqual(again["cmd"], reconciled["cmd"])


if __name__ == "__main__":
    unittest.main()
