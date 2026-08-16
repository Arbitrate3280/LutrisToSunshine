"""Tests for Sunshine installation resolution.

Covers package probes, single/ambiguous detection resolution, the
persisted user choice for ambiguous installs, and the CONNECTION
config-root mapping.  Tests patch ``sunshine.installation`` at the
defining module and never touch the real home directory.
"""

import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from sunshine import installation
from sunshine.connection import CONNECTION


def _probes(detected_types, **kwargs):
    return installation.SunshinePackageProbes(
        detected_types=list(detected_types), **kwargs
    )


def _unit_probe(unit_name=None):
    return installation.SunshineServiceUnitProbe(
        unit_name=unit_name or installation.SUNSHINE_UNIT
    )


class HomebrewPackageProbeTests(unittest.TestCase):
    def _patched_probes(
        self,
        *,
        brew=None,
        brew_prefix_results=None,
        isfile_results=None,
        access_results=None,
        sunshine_which="/usr/bin/sunshine",
    ):
        """Build a probe_packages() call patched with the given behavior."""
        brew_prefix_results = dict(brew_prefix_results or {})
        isfile_results = dict(isfile_results or {})
        access_results = dict(access_results or {})

        def fake_which(name):
            if name == "brew":
                return brew
            if name == "sunshine":
                return sunshine_which
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
        ]
        for ctx in patches:
            ctx.start()
        self.addCleanup(self._stop_all, patches)
        return installation.probe_packages()

    @staticmethod
    def _stop_all(patches):
        for ctx in patches:
            ctx.stop()

    def test_homebrew_detection_returns_homebrew_for_stable_formula(self):
        probes = self._patched_probes(
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
        self.assertEqual(
            probes.homebrew_binary,
            "/home/linuxbrew/.linuxbrew/opt/sunshine/bin/sunshine",
        )
        self.assertIn("homebrew", probes.detected_types)
        self.assertEqual(
            installation.preferred_install_type(probes.detected_types), "homebrew"
        )

    def test_homebrew_detection_returns_homebrew_for_beta_formula(self):
        probes = self._patched_probes(
            brew="/home/linuxbrew/.linuxbrew/bin/brew",
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
        self.assertEqual(
            probes.homebrew_binary,
            "/home/linuxbrew/.linuxbrew/opt/sunshine-beta/bin/sunshine",
        )
        self.assertEqual(
            installation.preferred_install_type(probes.detected_types), "homebrew"
        )

    def test_homebrew_preferred_before_generic_native(self):
        probes = self._patched_probes(
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
        self.assertEqual(probes.detected_types, ["homebrew", "native"])
        self.assertEqual(
            installation.preferred_install_type(probes.detected_types), "homebrew"
        )

    def test_homebrew_detection_skips_formula_when_binary_missing(self):
        probes = self._patched_probes(
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
            sunshine_which=None,
        )
        self.assertEqual(probes.detected_types, [])

    def test_no_brew_falls_through_to_native(self):
        probes = self._patched_probes(brew=None)
        self.assertEqual(probes.detected_types, ["native"])
        self.assertEqual(
            installation.preferred_install_type(probes.detected_types), "native"
        )


class ResolveInstallationTests(unittest.TestCase):
    def test_single_detection_auto_resolves(self):
        with patch.object(
            installation,
            "probe_packages",
            return_value=_probes(["native"], path_binary="/usr/bin/sunshine"),
        ), patch.object(
            installation, "probe_sunshine_service_unit", return_value=_unit_probe()
        ):
            facts = installation.resolve_installation()

        self.assertTrue(facts.installed)
        self.assertEqual(facts.install_type, "native")
        self.assertEqual(facts.detected_types, ["native"])

    def test_no_detection_reports_not_installed(self):
        with patch.object(
            installation, "probe_packages", return_value=_probes([])
        ), patch.object(
            installation, "probe_sunshine_service_unit", return_value=_unit_probe()
        ) as probe_unit:
            facts = installation.resolve_installation()

        self.assertFalse(facts.installed)
        self.assertIsNone(facts.install_type)
        self.assertEqual(facts.detected_types, [])
        probe_unit.assert_not_called()

    def test_single_native_detection_resolves_without_subprocess(self):
        def fake_which(name):
            if name == "sunshine":
                return "/usr/bin/sunshine"
            return None

        with patch("shutil.which", side_effect=fake_which), \
             patch("subprocess.run") as mock_run, \
             patch.object(installation, "homebrew_sunshine_executable", return_value=None), \
             patch.object(installation, "probe_sunshine_service_unit", return_value=_unit_probe()):
            facts = installation.resolve_installation()

        self.assertTrue(facts.installed)
        self.assertEqual(facts.install_type, "native")
        mock_run.assert_not_called()

    def test_homebrew_on_path_resolves_without_brew_binary(self):
        def fake_which(name):
            if name == "sunshine":
                return "/home/linuxbrew/.linuxbrew/bin/sunshine"
            return None

        with patch("shutil.which", side_effect=fake_which), \
             patch("subprocess.run") as mock_run, \
             patch.object(installation, "homebrew_sunshine_executable", return_value=None), \
             patch.object(installation, "probe_sunshine_service_unit", return_value=_unit_probe()):
            facts = installation.resolve_installation()

        self.assertTrue(facts.installed)
        self.assertEqual(facts.install_type, "homebrew")
        mock_run.assert_not_called()

    def test_ambiguous_without_saved_choice_leaves_install_type_none(self):
        with tempfile.TemporaryDirectory() as raw:
            with patch.object(
                installation,
                "probe_packages",
                return_value=_probes(
                    ["flatpak", "native"],
                    flatpak_installed=True,
                    path_binary="/usr/bin/sunshine",
                ),
            ), patch.object(
                installation, "probe_sunshine_service_unit", return_value=_unit_probe()
            ), patch.object(
                installation, "detect_sunshine_config_root", return_value=Path(raw)
            ):
                facts = installation.resolve_installation()

        self.assertTrue(facts.installed)
        self.assertIsNone(facts.install_type)
        self.assertEqual(facts.detected_types, ["flatpak", "native"])

    def test_ambiguous_with_valid_saved_choice_resolves(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            (root / "server_connection.json").write_text(
                json.dumps({"sunshine": {"install_type": "native"}}),
                encoding="utf-8",
            )
            with patch.object(
                installation,
                "probe_packages",
                return_value=_probes(
                    ["flatpak", "native"],
                    flatpak_installed=True,
                    path_binary="/usr/bin/sunshine",
                ),
            ), patch.object(
                installation, "probe_sunshine_service_unit", return_value=_unit_probe()
            ), patch.object(
                installation, "detect_sunshine_config_root", return_value=root
            ):
                facts = installation.resolve_installation()

        self.assertTrue(facts.installed)
        self.assertEqual(facts.install_type, "native")

    def test_ambiguous_with_stale_saved_choice_returns_none(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            (root / "server_connection.json").write_text(
                json.dumps({"sunshine": {"install_type": "appimage"}}),
                encoding="utf-8",
            )
            with patch.object(
                installation,
                "probe_packages",
                return_value=_probes(
                    ["flatpak", "native"],
                    flatpak_installed=True,
                    path_binary="/usr/bin/sunshine",
                ),
            ), patch.object(
                installation, "probe_sunshine_service_unit", return_value=_unit_probe()
            ), patch.object(
                installation, "detect_sunshine_config_root", return_value=root
            ):
                facts = installation.resolve_installation()

        self.assertTrue(facts.installed)
        self.assertIsNone(facts.install_type)


class InstallChoicePersistenceTests(unittest.TestCase):
    def test_round_trip_preserves_other_settings(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            (root / "server_connection.json").write_text(
                json.dumps(
                    {
                        "sunshine": {"host": "192.168.1.10", "port": 47990},
                        "apollo": {"host": "localhost", "port": 47989},
                    }
                ),
                encoding="utf-8",
            )
            with patch.object(
                installation, "detect_sunshine_config_root", return_value=root
            ):
                installation.save_install_choice("native")
                saved = json.loads(
                    (root / "server_connection.json").read_text(encoding="utf-8")
                )
                self.assertEqual(
                    installation.load_install_choice(["flatpak", "native"]), "native"
                )

        self.assertEqual(saved["sunshine"]["install_type"], "native")
        self.assertEqual(saved["sunshine"]["host"], "192.168.1.10")
        self.assertEqual(saved["sunshine"]["port"], 47990)
        self.assertEqual(saved["apollo"], {"host": "localhost", "port": 47989})

    def test_save_creates_missing_config_root(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw) / "sunshine"
            with patch.object(
                installation, "detect_sunshine_config_root", return_value=root
            ):
                installation.save_install_choice("flatpak")
            saved = json.loads(
                (root / "server_connection.json").read_text(encoding="utf-8")
            )

        self.assertEqual(saved["sunshine"]["install_type"], "flatpak")

    def test_save_drops_stale_choice(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            (root / "server_connection.json").write_text(
                json.dumps({"sunshine": {"install_type": "appimage"}}),
                encoding="utf-8",
            )
            with patch.object(
                installation, "detect_sunshine_config_root", return_value=root
            ):
                installation.save_install_choice("native")
                saved = json.loads(
                    (root / "server_connection.json").read_text(encoding="utf-8")
                )

        self.assertEqual(saved["sunshine"]["install_type"], "native")


class SunshineConfigRootTests(unittest.TestCase):
    def test_config_root_is_native_sunshine_path_for_homebrew(self):
        with patch.object(CONNECTION, "installation_type", "homebrew"):
            self.assertEqual(
                CONNECTION.get_config_root(),
                os.path.expanduser("~/.config/sunshine"),
            )


if __name__ == "__main__":
    unittest.main()
