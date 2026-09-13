"""Direct tests for display/command_wrap wrapping/unwrapping logic."""

import base64
import unittest

from display import command_wrap


SCRIPT = command_wrap.get_launch_app_script()
PREP_SCRIPT = command_wrap.get_headless_prep_script()


class WrapCommandTests(unittest.TestCase):
    def test_roundtrip(self):
        self.assertEqual(command_wrap.unwrap_command(command_wrap.wrap_command("echo hi")), "echo hi")

    def test_roundtrip_preserves_origin_and_timeout(self):
        wrapped = command_wrap.wrap_command("flatpak run app", origin="steam", exit_timeout=12)
        self.assertEqual(command_wrap.get_wrapped_command_origin(wrapped), "steam")
        self.assertEqual(command_wrap.get_wrapped_command_exit_timeout(wrapped), 12)

    def test_wrap_is_idempotent(self):
        once = command_wrap.wrap_command("x")
        self.assertEqual(command_wrap.wrap_command(once), once)

    def test_none_and_empty_passthrough(self):
        self.assertIsNone(command_wrap.wrap_command(None))
        self.assertEqual(command_wrap.wrap_command(""), "")
        self.assertIsNone(command_wrap.unwrap_command(None))
        self.assertEqual(command_wrap.unwrap_command(""), "")

    def test_unwrapped_passthrough(self):
        self.assertEqual(command_wrap.unwrap_command("plain command"), "plain command")
        self.assertFalse(command_wrap.is_wrapped_command("plain command"))

    def test_exit_timeout_default_for_unwrapped(self):
        self.assertEqual(command_wrap.get_wrapped_command_exit_timeout("plain command"), 5)
        self.assertEqual(command_wrap.get_wrapped_command_exit_timeout("plain command", default=9), 9)

    def test_malformed_base64_returns_original(self):
        bad = f"{SCRIPT} cmd 5 !!!notbase64!!!"
        self.assertEqual(command_wrap.unwrap_command(bad), bad)

    def test_legacy_two_part_format_decodes(self):
        encoded = base64.b64encode(b"legacy cmd").decode("ascii")
        wrapped = f"{SCRIPT} {encoded}"
        self.assertEqual(command_wrap.unwrap_command(wrapped), "legacy cmd")
        self.assertEqual(command_wrap.get_wrapped_command_origin(wrapped), "cmd")


class HeadlessPrepWrapTests(unittest.TestCase):
    def test_roundtrip(self):
        self.assertEqual(
            command_wrap.unwrap_headless_prep_command(
                command_wrap.wrap_headless_prep_command("prep --flag")
            ),
            "prep --flag",
        )

    def test_wrap_is_idempotent(self):
        once = command_wrap.wrap_headless_prep_command("prep")
        self.assertEqual(command_wrap.wrap_headless_prep_command(once), once)

    def test_unwrapped_passthrough(self):
        self.assertEqual(command_wrap.unwrap_headless_prep_command("plain"), "plain")
        self.assertFalse(command_wrap.is_headless_prep_wrapped("plain"))


class GetAppPrepCommandsTests(unittest.TestCase):
    def test_disabled_returns_empty(self):
        self.assertEqual(command_wrap.get_app_prep_commands(False), [])

    def test_enabled_returns_resolution_scripts(self):
        self.assertEqual(
            command_wrap.get_app_prep_commands(True),
            [{"do": str(command_wrap.BIN_ROOT / "lutristosunshine-set-resolution.sh"),
              "undo": str(command_wrap.BIN_ROOT / "lutristosunshine-reset-resolution.sh")}],
        )


class HostEscapedWrapperTests(unittest.TestCase):
    """Older releases stored managed wrappers behind a host escape.

    Matching ``parts[0]`` exactly made such a command look like an ordinary
    game command, so it got wrapped a second time on every reconcile.
    """

    def _escaped(
        self,
        payload: str = "flatpak run com.google.Chrome",
        origin: str = "cmd",
        timeout: int = 5,
    ) -> str:
        inner = command_wrap.wrap_command(payload, origin, timeout)
        return f"flatpak-spawn --host {inner}"

    def test_escaped_wrapper_is_recognised(self) -> None:
        self.assertTrue(command_wrap.is_wrapped_command(self._escaped()))

    def test_wrapping_an_escaped_wrapper_is_a_no_op(self) -> None:
        escaped = self._escaped()
        self.assertEqual(command_wrap.wrap_command(escaped), escaped)

    def test_unwrap_sees_through_the_host_escape(self) -> None:
        self.assertEqual(
            command_wrap.unwrap_command(self._escaped()),
            "flatpak run com.google.Chrome",
        )

    def test_origin_and_timeout_survive_the_host_escape(self) -> None:
        escaped = self._escaped(origin="steam", timeout=12)
        self.assertEqual(command_wrap.get_wrapped_command_origin(escaped), "steam")
        self.assertEqual(command_wrap.get_wrapped_command_exit_timeout(escaped), 12)

    def test_nested_wrappers_collapse_to_the_original_command(self) -> None:
        nested = command_wrap.wrap_command(self._escaped(), "cmd", 5)
        self.assertEqual(
            command_wrap.unwrap_command(nested),
            "flatpak run com.google.Chrome",
        )

    def test_malformed_inner_payload_does_not_crash(self) -> None:
        broken = f"{command_wrap.get_launch_app_script()} cmd 5 not-base64!"
        nested = command_wrap.wrap_command(broken, "cmd", 5)
        self.assertEqual(command_wrap.unwrap_command(nested), broken)

    def test_escaped_headless_prep_wrapper_is_recognised(self) -> None:
        wrapped = command_wrap.wrap_headless_prep_command("notify-send hi")
        escaped = f"flatpak-spawn --host {wrapped}"
        self.assertTrue(command_wrap.is_headless_prep_wrapped(escaped))
        self.assertEqual(
            command_wrap.unwrap_headless_prep_command(escaped),
            "notify-send hi",
        )


class RoutesThroughManagedScriptsTests(unittest.TestCase):
    SCRIPT = str(command_wrap.BIN_ROOT / "lutristosunshine-set-resolution.sh")

    def test_matches_bare_and_escaped_and_prefixed(self):
        for command in (
            self.SCRIPT,
            f"flatpak-spawn --host {self.SCRIPT}",
            f"flatpak-spawn --host env SWAYSOCK=/run/x {self.SCRIPT}",
            f"env FOO=bar {self.SCRIPT} 2560 1440",
        ):
            with self.subTest(command=command):
                self.assertTrue(command_wrap.routes_through_managed_scripts(command))

    def test_ignores_foreign_commands(self):
        for command in (
            "",
            "lutris lutris:rungame/test",
            "notify-send hi",
            # Merely mentioning our prefix is not our script.
            "my-game lutristosunshine-fan-mod",
            f"{command_wrap.BIN_ROOT}-backup/tool.sh",
        ):
            with self.subTest(command=command):
                self.assertFalse(command_wrap.routes_through_managed_scripts(command))

    def test_unparseable_command_falls_back_to_prefix_scan(self):
        self.assertTrue(command_wrap.routes_through_managed_scripts(f"sh -c '{self.SCRIPT}"))
        self.assertFalse(command_wrap.routes_through_managed_scripts("sh -c 'echo oops"))

if __name__ == "__main__":
    unittest.main()
