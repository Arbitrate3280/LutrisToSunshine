import io
import unittest

from utils import terminal


class TerminalHelpersTests(unittest.TestCase):
    def test_supports_color_false_when_no_color_is_set(self) -> None:
        stream = io.StringIO()
        stream.isatty = lambda: True  # type: ignore[attr-defined]

        self.assertFalse(terminal.supports_color(stream=stream, environ={"NO_COLOR": "1", "TERM": "xterm-256color"}))

    def test_supports_color_true_for_tty(self) -> None:
        stream = io.StringIO()
        stream.isatty = lambda: True  # type: ignore[attr-defined]

        self.assertTrue(terminal.supports_color(stream=stream, environ={"TERM": "xterm-256color"}))

    def test_colorize_respects_enabled_override(self) -> None:
        colored = terminal.colorize("OK", "success", enabled=True)
        plain = terminal.colorize("OK", "success", enabled=False)

        self.assertIn("\033[", colored)
        self.assertEqual(plain, "OK")


if __name__ == "__main__":
    unittest.main()
