import os
import subprocess
import unittest
from unittest.mock import patch

from sunshine import sunshine


class SunshineDetectionTests(unittest.TestCase):
    def _run_command_fail(self, command, *args, **kwargs):
        return subprocess.CompletedProcess([], 1, "", "not installed")

    def _patched_detection(
        self,
        *,
        brew=None,
        run_command=None,
        brew_prefix_results=None,
        isfile_results=None,
        access_results=None,
    ):
        """Build a detect_sunshine_installation() call patched with the given behavior."""
        brew_prefix_results = dict(brew_prefix_results or {})
        isfile_results = dict(isfile_results or {})
        access_results = dict(access_results or {})

        def fake_which(name):
            if name == "brew":
                return brew
            if name == "sunshine":
                return "/usr/bin/sunshine"
            return None

        def fake_run(args, *a, **kw):
            for key, outcome in brew_prefix_results.items():
                if list(args[-2:]) == ["--prefix", key]:
                    return subprocess.CompletedProcess(args, outcome["rc"], outcome["stdout"], outcome.get("stderr", ""))
            return subprocess.CompletedProcess(args, 1, "", "")

        def fake_isfile(path):
            for key, value in isfile_results.items():
                if path.endswith(key):
                    return value
            return False

        def fake_access(path, mode):
            for key, value in access_results.items():
                if path.endswith(key):
                    return value
            return False

        patches = [
            patch("shutil.which", side_effect=fake_which),
            patch("subprocess.run", side_effect=fake_run),
            patch("os.path.isfile", side_effect=fake_isfile),
            patch("os.access", side_effect=fake_access),
            patch("sunshine.sunshine.run_command", side_effect=run_command or self._run_command_fail),
        ]
        for ctx in patches:
            ctx.start()
        self.addCleanup(self._stop_all, patches)
        return sunshine.detect_sunshine_installation()

    @staticmethod
    def _stop_all(patches):
        for ctx in patches:
            ctx.stop()

    def test_homebrew_detection_returns_homebrew_for_stable_formula(self):
        result = self._patched_detection(
            brew="/home/linuxbrew/.linuxbrew/bin/brew",
            brew_prefix_results={
                "sunshine": {
                    "rc": 0,
                    "stdout": "/home/linuxbrew/.linuxbrew/opt/sunshine\n",
                },
            },
            isfile_results={"bin/sunshine": True},
            access_results={"bin/sunshine": True},
        )
        self.assertEqual(result, (True, "homebrew"))

    def test_homebrew_detection_returns_homebrew_for_beta_formula(self):
        def run_cmd(*args, **kwargs):
            return subprocess.CompletedProcess([], 1, "", "")

        result = self._patched_detection(
            brew="/home/linuxbrew/.linuxbrew/bin/brew",
            run_command=run_cmd,
            brew_prefix_results={
                "sunshine": {"rc": 1, "stdout": "", "stderr": "No formula"},
                "sunshine-beta": {
                    "rc": 0,
                    "stdout": "/home/linuxbrew/.linuxbrew/opt/sunshine-beta\n",
                },
            },
            isfile_results={"bin/sunshine": True},
            access_results={"bin/sunshine": True},
        )
        self.assertEqual(result, (True, "homebrew"))

    def test_homebrew_detection_runs_before_generic_native(self):
        def run_cmd(command, *args, **kwargs):
            if "which sunshine" in command:
                self.fail("Homebrew probe should run before generic native detection")
            return subprocess.CompletedProcess([], 1, "", "")

        result = self._patched_detection(
            brew="/home/linuxbrew/.linuxbrew/bin/brew",
            run_command=run_cmd,
            brew_prefix_results={
                "sunshine": {
                    "rc": 0,
                    "stdout": "/home/linuxbrew/.linuxbrew/opt/sunshine\n",
                },
            },
            isfile_results={"bin/sunshine": True},
            access_results={"bin/sunshine": True},
        )
        self.assertEqual(result, (True, "homebrew"))

    def test_homebrew_detection_skips_formula_when_binary_missing(self):
        result = self._patched_detection(
            brew="/home/linuxbrew/.linuxbrew/bin/brew",
            brew_prefix_results={
                "sunshine": {
                    "rc": 0,
                    "stdout": "/home/linuxbrew/.linuxbrew/opt/sunshine\n",
                },
                "sunshine-beta": {"rc": 1, "stdout": "", "stderr": ""},
            },
            isfile_results={"bin/sunshine": False},
            access_results={"bin/sunshine": True},
        )
        self.assertEqual(result, (False, ""))

    def test_no_brew_falls_through_to_native(self):
        def run_cmd(command, *args, **kwargs):
            if "which sunshine" in command:
                return subprocess.CompletedProcess([], 0, "/usr/bin/sunshine", "")
            return subprocess.CompletedProcess([], 1, "", "")

        result = self._patched_detection(
            brew=None,
            run_command=run_cmd,
        )
        self.assertEqual(result, (True, "native"))


class SunshineConfigRootTests(unittest.TestCase):
    def test_config_root_is_native_sunshine_path_for_homebrew(self):
        with patch.object(sunshine, "INSTALLATION_TYPE", "homebrew"):
            self.assertEqual(
                sunshine._get_config_root(),
                os.path.expanduser("~/.config/sunshine"),
            )


if __name__ == "__main__":
    unittest.main()
