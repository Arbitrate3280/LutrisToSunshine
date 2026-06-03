"""Tests for Sunshine install detection, service-unit discovery, and
override management across Flatpak, native, and Homebrew installs.

These tests focus on ``display.manager`` behaviour around Sunshine
service unit resolution, Homebrew binary fallback, and safe cleanup
of managed systemd overrides without trampling user-supplied ones.
"""

import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from display import manager
from display import sunshine_service
from sunshine import install as sunshine_install
from tests._display_test_helpers import (
    patched,
    temp_display_state,
    write_managed_override,
    write_user_override,
)


def _completed(args, returncode=0, stdout="", stderr=""):
    return subprocess.CompletedProcess(args, returncode, stdout, stderr)


class HomebrewInstallDetectionTests(unittest.TestCase):
    def test_sunshine_unit_candidates_include_homebrew_aliases(self) -> None:
        candidates = sunshine_service.SUNSHINE_UNIT_CANDIDATES
        self.assertIn(sunshine_service.SUNSHINE_UNIT, candidates)
        self.assertIn(sunshine_service.FALLBACK_SUNSHINE_UNIT, candidates)
        self.assertIn("homebrew.sunshine.service", candidates)
        self.assertIn("homebrew.sunshine-beta.service", candidates)

    def test_canonical_install_executable_finds_homebrew_sunshine(self) -> None:
        def fake_run(args, *a, **kw):
            if list(args[-2:]) == ["--prefix", "sunshine"]:
                return _completed(
                    args, 0, "/home/linuxbrew/.linuxbrew/opt/sunshine\n"
                )
            return _completed(args, 1, "", "no formula")

        with mock.patch.object(sunshine_install.shutil, "which", return_value="/home/linuxbrew/.linuxbrew/bin/brew"), \
             mock.patch.object(sunshine_install.subprocess, "run", side_effect=fake_run), \
             mock.patch.object(sunshine_install.os.path, "isfile", return_value=True), \
             mock.patch.object(sunshine_install.os, "access", return_value=True):
            result = sunshine_install.homebrew_sunshine_executable()

        self.assertEqual(
            result,
            ("sunshine", "/home/linuxbrew/.linuxbrew/opt/sunshine/bin/sunshine"),
        )

    def test_canonical_install_binary_returns_just_path(self) -> None:
        with mock.patch.object(
            sunshine_install,
            "homebrew_sunshine_executable",
            return_value=("sunshine", "/opt/sunshine/bin/sunshine"),
        ):
            self.assertEqual(
                sunshine_install.homebrew_sunshine_binary(),
                "/opt/sunshine/bin/sunshine",
            )


class HomebrewBinaryResolutionTests(unittest.TestCase):
    def test_sunshine_binary_falls_back_to_homebrew_when_path_missing(self) -> None:
        fake_shutil = mock.MagicMock()
        fake_shutil.which.side_effect = lambda name: None if name == "sunshine" else f"/usr/bin/{name}"
        with mock.patch.object(sunshine_service, "shutil", fake_shutil), \
             mock.patch.object(
                 sunshine_service,
                 "homebrew_sunshine_binary",
                 return_value="/home/linuxbrew/.linuxbrew/opt/sunshine/bin/sunshine",
             ):
            self.assertEqual(
                sunshine_service.sunshine_binary(),
                "/home/linuxbrew/.linuxbrew/opt/sunshine/bin/sunshine",
            )


class SunshineUnitSelectionTests(unittest.TestCase):
    def test_sunshine_unit_prefers_active_homebrew_unit(self) -> None:
        def fake_systemctl_user(*args, check=False):
            if args[:3] == ("show", "--property=LoadState", "--value"):
                unit = args[-1]
                loaded = unit in {"homebrew.sunshine.service", "homebrew.sunshine-beta.service"}
                return _completed(list(args), 0 if loaded else 1, "loaded\n" if loaded else "not-found\n")
            if args[:3] == ("show", "--property=Id", "--value"):
                return _completed(list(args), 0, f"{args[-1]}\n")
            if args and args[0] == "is-active":
                unit = args[-1]
                active = unit == "homebrew.sunshine.service"
                return _completed(list(args), 0 if active else 1, "active\n" if active else "inactive\n")
            return _completed(list(args), 0, "")

        with patched(sunshine_service, _systemctl_user=fake_systemctl_user):
            self.assertEqual(sunshine_service.sunshine_unit(), "homebrew.sunshine.service")

    def test_sunshine_unit_handles_active_beta_canonical_unit(self) -> None:
        def fake_systemctl_user(*args, check=False):
            if args[:3] == ("show", "--property=LoadState", "--value"):
                return _completed(list(args), 0, "loaded\n")
            if args[:3] == ("show", "--property=Id", "--value"):
                if args[-1] == "app-dev.lizardbyte.app.Sunshine.service":
                    return _completed(
                        list(args), 0, "app-dev.lizardbyte.app.Sunshine.service\n"
                    )
                return _completed(list(args), 0, f"{args[-1]}\n")
            if args and args[0] == "is-active":
                return _completed(list(args), 0, "active\n")
            return _completed(list(args), 0, "")

        with patched(sunshine_service, _systemctl_user=fake_systemctl_user):
            self.assertEqual(sunshine_service.sunshine_unit(), "app-dev.lizardbyte.app.Sunshine.service")

    def test_managed_sunshine_units_includes_known_candidates_without_duplicates(self) -> None:
        units = sunshine_service.managed_sunshine_units()
        self.assertEqual(len(units), len(set(units)))
        for candidate in sunshine_service.SUNSHINE_UNIT_CANDIDATES:
            self.assertIn(candidate, units)

    def test_managed_sunshine_units_prepends_saved_homebrew_unit(self) -> None:
        state = manager._default_state()
        state["sunshine_unit_name"] = "custom-saved-sunshine.service"
        units = sunshine_service.managed_sunshine_units(state)
        self.assertEqual(units[0], "custom-saved-sunshine.service")
        self.assertEqual(len(units), len(set(units)))
        self.assertIn("homebrew.sunshine.service", units)
        self.assertIn("homebrew.sunshine-beta.service", units)

    def test_sunshine_unit_falls_back_to_legacy_name_when_preferred_unit_missing(self) -> None:
        def fake_systemctl_user(*args, check=False):
            unit = args[-1]
            if args[:3] == ("show", "--property=LoadState", "--value"):
                if unit == sunshine_service.SUNSHINE_UNIT:
                    return _completed(list(args), 1, "", "missing")
                if unit == sunshine_service.FALLBACK_SUNSHINE_UNIT:
                    return _completed(list(args), 0, "loaded\n", "")
            return _completed(list(args), 0, "", "")

        with patched(sunshine_service, _systemctl_user=fake_systemctl_user):
            self.assertEqual(
                sunshine_service.sunshine_unit(),
                sunshine_service.FALLBACK_SUNSHINE_UNIT,
            )

    def test_sunshine_unit_prefers_canonical_active_unit_id_over_alias(self) -> None:
        def fake_systemctl_user(*args, check=False):
            unit = args[-1]
            if args[:3] == ("show", "--property=LoadState", "--value"):
                return _completed(list(args), 0, "loaded\n")
            if args[:3] == ("show", "--property=Id", "--value"):
                if unit == sunshine_service.FALLBACK_SUNSHINE_UNIT:
                    return _completed(
                        list(args), 0, f"{sunshine_service.SUNSHINE_UNIT}\n", ""
                    )
                return _completed(list(args), 0, f"{unit}\n", "")
            if args and args[0] == "is-active":
                return _completed(list(args), 0, "active\n")
            return _completed(list(args), 0, "", "")

        with patched(sunshine_service, _systemctl_user=fake_systemctl_user):
            self.assertEqual(
                sunshine_service.sunshine_unit(), sunshine_service.SUNSHINE_UNIT
            )


class SunshineExecStartSnapshotTests(unittest.TestCase):
    def test_remember_sunshine_execstart_preserves_homebrew_command(self) -> None:
        homebrew_command = (
            "/home/linuxbrew/.linuxbrew/opt/sunshine/bin/sunshine "
            "~/.config/sunshine/sunshine.conf"
        )
        state = manager._default_state()
        state["paths"] = dict(state["paths"])
        state["paths"]["sunshine_wrapper_script"] = "/run/lutristosunshine-run-display-service.sh"

        def fake_systemctl_user(*args, check=False):
            if args[:3] == ("show", "--property=ExecStart", "--value"):
                return _completed(list(args), 0, f"{homebrew_command}\n")
            return _completed(list(args), 0, "loaded\n")

        with patched(
            sunshine_service,
            _systemctl_user=fake_systemctl_user,
            sunshine_unit=lambda: "homebrew.sunshine.service",
            sunshine_binary=lambda: "/home/linuxbrew/.linuxbrew/opt/sunshine/bin/sunshine",
        ):
            updated = manager._remember_sunshine_execstart(
                state, unit="homebrew.sunshine.service"
            )

        self.assertEqual(updated["sunshine_execstart"], homebrew_command)
        self.assertEqual(updated["sunshine_unit_name"], "homebrew.sunshine.service")


class OverridePathHelpersTests(unittest.TestCase):
    def test_current_unit_override_paths_uses_saved_unit_name(self) -> None:
        tempdir = tempfile.TemporaryDirectory()
        self.addCleanup(tempdir.cleanup)
        base = Path(tempdir.name)
        systemd_user_dir = base / "systemd" / "user"

        state = manager._default_state()
        state["sunshine_unit_name"] = "homebrew.sunshine.service"
        state["paths"] = dict(state["paths"])
        state["paths"]["systemd_user_dir"] = str(systemd_user_dir)

        paths = sunshine_service.current_unit_override_paths(state)
        path_strs = [str(p) for p in paths]
        self.assertEqual(
            path_strs,
            [
                str(systemd_user_dir / "homebrew.sunshine.service.d" / "override.conf"),
                str(systemd_user_dir / "homebrew.sunshine.service.d"),
            ],
        )

    def test_current_unit_override_paths_empty_when_no_unit_named(self) -> None:
        state = manager._default_state()
        self.assertEqual(sunshine_service.current_unit_override_paths(state), [])

    def test_legacy_unit_override_paths_excludes_saved_unit(self) -> None:
        tempdir = tempfile.TemporaryDirectory()
        self.addCleanup(tempdir.cleanup)
        base = Path(tempdir.name)
        systemd_user_dir = base / "systemd" / "user"
        saved_unit = "homebrew.sunshine.service"

        state = manager._default_state()
        state["sunshine_unit_name"] = saved_unit
        state["paths"] = dict(state["paths"])
        state["paths"]["systemd_user_dir"] = str(systemd_user_dir)

        paths = sunshine_service.legacy_unit_override_paths(state)
        path_strs = [str(p) for p in paths]
        for unit in sunshine_service.SUNSHINE_UNIT_CANDIDATES:
            if unit == saved_unit:
                self.assertNotIn(str(systemd_user_dir / f"{unit}.d" / "override.conf"), path_strs)
                self.assertNotIn(str(systemd_user_dir / f"{unit}.d"), path_strs)
                continue
            self.assertIn(str(systemd_user_dir / f"{unit}.d" / "override.conf"), path_strs)
            self.assertIn(str(systemd_user_dir / f"{unit}.d"), path_strs)

    def test_candidate_override_paths_does_not_contain_saved_override(self) -> None:
        """Saved paths must NOT appear in candidate paths: candidates
        are explicitly NOT owned by this tool."""
        tempdir = tempfile.TemporaryDirectory()
        self.addCleanup(tempdir.cleanup)
        base = Path(tempdir.name)
        systemd_user_dir = base / "systemd" / "user"
        saved_override_dir = base / "saved.d"
        saved_override_dir.mkdir(parents=True)
        saved_override = saved_override_dir / "override.conf"

        state = manager._default_state()
        state["paths"] = dict(state["paths"])
        state["paths"]["systemd_user_dir"] = str(systemd_user_dir)
        state["paths"]["sunshine_override_dir"] = str(saved_override_dir)
        state["paths"]["sunshine_override"] = str(saved_override)

        saved = [str(p) for p in sunshine_service.current_unit_override_paths(state)]
        candidates = [str(p) for p in sunshine_service.legacy_unit_override_paths(state)]
        for path in saved:
            self.assertNotIn(path, candidates)


class OverrideCleanupTests(unittest.TestCase):
    def test_remove_display_deletes_homebrew_override_files(self) -> None:
        tempdir = tempfile.TemporaryDirectory()
        self.addCleanup(tempdir.cleanup)
        base = Path(tempdir.name)
        systemd_user_dir = base / "systemd" / "user"
        homebrew_override_dir = systemd_user_dir / "homebrew.sunshine.service.d"
        wrapper_path = base / "lutristosunshine-run-display-service.sh"
        write_managed_override(homebrew_override_dir / "override.conf", wrapper_path)

        with temp_display_state(
            manager,
            systemd_user_dir=systemd_user_dir,
            sunshine_override_dir=homebrew_override_dir,
            sunshine_override=homebrew_override_dir / "override.conf",
            sunshine_wrapper_script=wrapper_path,
            state_overrides={"enabled": True, "sunshine_unit_name": "homebrew.sunshine.service"},
        ) as (state, _):
            with patched(
                manager,
                load_state=lambda: state,
                save_state=lambda current: None,
                stop_display=lambda: 0,
                _remove_udev_rule=lambda current: True,
                _daemon_reload=lambda: None,
            ):
                result = manager.remove_display()
        self.assertEqual(result, 0)
        self.assertFalse((homebrew_override_dir / "override.conf").exists())
        self.assertFalse(homebrew_override_dir.exists())

    def test_cleanup_legacy_display_units_preserves_user_managed_overrides(self) -> None:
        tempdir = tempfile.TemporaryDirectory()
        self.addCleanup(tempdir.cleanup)
        base = Path(tempdir.name)
        systemd_user_dir = base / "systemd" / "user"
        wrapper_path = base / "lutristosunshine-run-display-service.sh"

        saved_override_dir = systemd_user_dir / "homebrew.sunshine.service.d"
        write_managed_override(saved_override_dir / "override.conf", wrapper_path)

        user_dir = systemd_user_dir / "sunshine.service.d"
        write_user_override(user_dir / "override.conf")

        other_managed_dir = systemd_user_dir / "app-dev.lizardbyte.app.Sunshine.service.d"
        write_managed_override(other_managed_dir / "override.conf", wrapper_path)

        with temp_display_state(
            manager,
            systemd_user_dir=systemd_user_dir,
            sunshine_override_dir=saved_override_dir,
            sunshine_override=saved_override_dir / "override.conf",
            sunshine_wrapper_script=wrapper_path,
        ) as (state, _):
            sunshine_service.cleanup_managed_overrides(state)

        self.assertTrue(
            (user_dir / "override.conf").exists(), "user-managed override must be preserved"
        )
        self.assertFalse(
            (other_managed_dir / "override.conf").exists(),
            "stale managed override should be removed",
        )
        self.assertFalse(
            (saved_override_dir / "override.conf").exists(),
            "saved override is owned by us and must be removed",
        )

    def test_remove_display_preserves_user_owned_homebrew_overrides(self) -> None:
        tempdir = tempfile.TemporaryDirectory()
        self.addCleanup(tempdir.cleanup)
        base = Path(tempdir.name)
        systemd_user_dir = base / "systemd" / "user"
        homebrew_override_dir = systemd_user_dir / "homebrew.sunshine.service.d"
        homebrew_override_dir.mkdir(parents=True)
        write_user_override(homebrew_override_dir / "override.conf")
        (homebrew_override_dir / "extra.conf").write_text(
            "[Service]\nExecStartPre=/usr/local/bin/pre-start.sh\n",
            encoding="utf-8",
        )

        with temp_display_state(
            manager,
            systemd_user_dir=systemd_user_dir,
            sunshine_override_dir=homebrew_override_dir,
            sunshine_override=homebrew_override_dir / "override.conf",
            state_overrides={"enabled": True, "sunshine_unit_name": "homebrew.sunshine.service"},
        ) as (state, _):
            with patched(
                manager,
                load_state=lambda: state,
                save_state=lambda current: None,
                stop_display=lambda: 0,
                _remove_udev_rule=lambda current: True,
                _daemon_reload=lambda: None,
            ):
                result = manager.remove_display()

        self.assertEqual(result, 0)
        self.assertTrue(
            (homebrew_override_dir / "override.conf").exists(),
            "user-owned Homebrew override must be preserved",
        )
        self.assertTrue(
            (homebrew_override_dir / "extra.conf").exists(),
            "user-owned Homebrew drop-in must be preserved",
        )
        self.assertTrue(
            homebrew_override_dir.exists(),
            "directory must remain when user files are present",
        )


if __name__ == "__main__":
    unittest.main()
