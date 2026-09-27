"""Execution tests for the exact-refresh display scripts.

Regression coverage for the bug where "exact" sync mode ("Use the client's
refresh rate") left the virtual display on the 1920x1080@60 fallback.

The rendered scripts resolved the negotiated stream rate with
``journalctl --user -u <sunshine unit>``. Flatpak runs Sunshine inside a
transient scope (``app-flatpak-<app>-<id>.scope``), so the unit journal
never contains Sunshine's "Requested frame rate" lines; with no resolved
rate the mode switch was skipped entirely, even though the client's
resolution was already known. The scripts now read Sunshine's own log
(next to ``sunshine.conf``) and fall back to the client's requested FPS
instead of leaving the fallback mode in place.

Tests run the actually rendered scripts via subprocess, matching how
Sunshine's prep-cmd executes them at runtime.
"""

import os
import stat
import subprocess
import tempfile
import time
import unittest
from datetime import datetime
from pathlib import Path

from display import manager
from display import scripts_render


def _requested_rate_line(epoch: float, body: str) -> str:
    stamp = datetime.fromtimestamp(epoch).strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]
    return f"[{stamp}]: Info: [wlgrab] Requested frame rate [{body}]"


def _pipewire_rate_line(epoch: float, body: str) -> str:
    stamp = datetime.fromtimestamp(epoch).strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]
    return f"[{stamp}]: Info: [pipewire] Requested frame rate: {body}"


def _pipewire_pacing_line(epoch: float, milliseconds: str) -> str:
    stamp = datetime.fromtimestamp(epoch).strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]
    return (
        f"[{stamp}]: Info: [pipewire] Requested variable frame rate "
        f"(Sunshine pacing required: {milliseconds}ms)"
    )


class RefreshScriptExecutionTests(unittest.TestCase):
    def _render_scripts(self, base: Path, *, stub_resolver: bool = False) -> dict:
        """Render the refresh scripts into ``base`` and return their paths."""
        state = manager._default_state()
        state.sunshine_unit_name = "sunshine.service"
        state.sunshine_execstart = "sunshine"
        state.paths.sunshine_conf = str(base / "sunshine.conf")
        state.paths.resolve_stream_fps_script = str(base / "resolve.sh")
        state.paths.apply_exact_refresh_script = str(base / "apply.sh")
        state.paths.set_resolution_script = str(base / "set.sh")
        Path(state.paths.sunshine_conf).write_text("", encoding="utf-8")

        rendered = scripts_render.render_managed_files(state)
        scripts = {}
        for key in (
            "resolve_stream_fps_script",
            "apply_exact_refresh_script",
            "set_resolution_script",
        ):
            path = Path(getattr(state.paths, key))
            scripts[key] = path
            if stub_resolver and key == "resolve_stream_fps_script":
                continue
            path.write_text(rendered[path], encoding="utf-8")
            path.chmod(path.stat().st_mode | stat.S_IXUSR)
        return scripts

    def _fake_binary(self, path: Path, body: str) -> Path:
        path.write_text(body, encoding="utf-8")
        path.chmod(0o755)
        return path

    def _fake_bin_dir(self, base: Path) -> Path:
        bindir = base / "fakebin"
        bindir.mkdir()
        return bindir

    def _env(self, bindir: Path, **extra: str) -> dict:
        return {**os.environ, "PATH": f"{bindir}:{os.environ['PATH']}", **extra}

    def test_resolver_prefers_sunshine_log_over_unit_journal(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            base = Path(raw)
            bindir = self._fake_bin_dir(base)
            Path(base / "sunshine.log").write_text(
                _requested_rate_line(time.time(), "60000/1001, approx. 59.94 fps") + "\n",
                encoding="utf-8",
            )
            scripts = self._render_scripts(base)

            # Flatpak's Sunshine never reaches the unit journal; a decoy rate
            # here proves which source the resolver actually reads.
            decoy = base / "decoy.log"
            decoy.write_text(_requested_rate_line(time.time(), "30fps") + "\n", encoding="utf-8")
            self._fake_binary(bindir / "journalctl", f'#!/bin/bash\ncat "{decoy}"\n')

            result = subprocess.run(
                [str(scripts["resolve_stream_fps_script"]), "exact", "none", ""],
                capture_output=True,
                text=True,
                env=self._env(bindir, SUNSHINE_CLIENT_FPS="90"),
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(result.stdout.strip(), "59.94")

    def test_resolver_reads_the_portal_log_formats(self) -> None:
        # The portal backend logs the requested rate in a different shape than
        # the wlroots backend ("...: 120/1, approx. 120.00 fps" and the variable
        # rate pacing line).  Neither matched, so portal capture always fell back
        # to the launch request's FPS -- a different value -- and the virtual
        # display kept the wrong refresh rate.
        now = time.time()
        cases = {
            "60000/1001, approx. 59.94 fps": "59.94",
            "120/1, approx. 120.00 fps": "120",
            "60 fps": "60",
        }
        for body, expected in cases.items():
            with self.subTest(body=body), tempfile.TemporaryDirectory() as raw:
                base = Path(raw)
                bindir = self._fake_bin_dir(base)
                Path(base / "sunshine.log").write_text(
                    _pipewire_rate_line(now - 1, body) + "\n", encoding="utf-8"
                )
                scripts = self._render_scripts(base)

                result = subprocess.run(
                    [str(scripts["resolve_stream_fps_script"]), "exact", "none", ""],
                    capture_output=True,
                    text=True,
                    env=self._env(bindir, SUNSHINE_CLIENT_FPS="60"),
                )

                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(result.stdout.strip(), expected)

    def test_resolver_reads_the_portal_pacing_line(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            base = Path(raw)
            bindir = self._fake_bin_dir(base)
            Path(base / "sunshine.log").write_text(
                _pipewire_pacing_line(time.time() - 1, "8.3333") + "\n",
                encoding="utf-8",
            )
            scripts = self._render_scripts(base)

            result = subprocess.run(
                [str(scripts["resolve_stream_fps_script"]), "exact", "none", ""],
                capture_output=True,
                text=True,
                env=self._env(bindir, SUNSHINE_CLIENT_FPS="60"),
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(result.stdout.strip(), "120")

    def test_client_mode_rounds_the_negotiated_rate(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            base = Path(raw)
            bindir = self._fake_bin_dir(base)
            now = time.time()
            Path(base / "sunshine.log").write_text(
                _pipewire_pacing_line(now - 1, "8.3333") + "\n", encoding="utf-8"
            )
            scripts = self._render_scripts(base)

            calls = base / "swaymsg.calls"
            self._fake_binary(bindir / "swaymsg", f'#!/bin/bash\nprintf \'%s\\n\' "$*" >> "{calls}"\n')

            result = subprocess.run(
                [
                    str(scripts["apply_exact_refresh_script"]),
                    "2560",
                    "1440",
                    f"{now - 5:.6f}",
                    "client",
                ],
                capture_output=True,
                text=True,
                env=self._env(bindir, SUNSHINE_CLIENT_FPS="60"),
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            # 120 from the stream's own line, not the launch request's 60.
            self.assertEqual(
                calls.read_text(encoding="utf-8").strip(),
                "output HEADLESS-1 mode 2560x1440@120Hz",
            )

    def test_client_mode_rounds_fractional_rates_to_integers(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            base = Path(raw)
            bindir = self._fake_bin_dir(base)
            now = time.time()
            Path(base / "sunshine.log").write_text(
                _pipewire_rate_line(now - 1, "60000/1001, approx. 59.94 fps") + "\n",
                encoding="utf-8",
            )
            scripts = self._render_scripts(base)

            calls = base / "swaymsg.calls"
            self._fake_binary(bindir / "swaymsg", f'#!/bin/bash\nprintf \'%s\\n\' "$*" >> "{calls}"\n')

            result = subprocess.run(
                [
                    str(scripts["apply_exact_refresh_script"]),
                    "1920",
                    "1080",
                    f"{now - 5:.6f}",
                    "client",
                ],
                capture_output=True,
                text=True,
                env=self._env(bindir, SUNSHINE_CLIENT_FPS="60"),
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(
                calls.read_text(encoding="utf-8").strip(),
                "output HEADLESS-1 mode 1920x1080@60Hz",
            )

    def test_scripts_report_the_client_rate_into_sunshine_log(self) -> None:
        # Prep-command output is captured in Sunshine's log; without it there is
        # no way to tell whether a wrong refresh rate came from the client's
        # request or from the display sync logic.
        with tempfile.TemporaryDirectory() as raw:
            base = Path(raw)
            scripts = self._render_scripts(base)

            set_resolution = scripts["set_resolution_script"].read_text(encoding="utf-8")
            apply_script = scripts["apply_exact_refresh_script"].read_text(encoding="utf-8")
            self.assertIn('lts_log "applying', set_resolution)
            self.assertIn("SUNSHINE_CLIENT_FPS:-unset", set_resolution)
            self.assertIn("client-sync.log", set_resolution)
            self.assertIn('lts_log "stream rate resolved to', apply_script)
            self.assertIn("client-sync.log", apply_script)

    def test_client_mode_correction_is_wired_into_set_resolution(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            base = Path(raw)
            scripts = self._render_scripts(base)
            text = scripts["set_resolution_script"].read_text(encoding="utf-8")

            self.assertIn('if [ "$mode" = "client" ]; then', text)
            self.assertIn('"@APPLY_EXACT_REFRESH_SCRIPT@"'.replace(
                "@APPLY_EXACT_REFRESH_SCRIPT@", str(base / "apply.sh")
            ), text)
            self.assertIn('client >/dev/null 2>&1 &', text)

    def test_apply_exact_refresh_switches_to_negotiated_rate(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            base = Path(raw)
            bindir = self._fake_bin_dir(base)
            now = time.time()
            Path(base / "sunshine.log").write_text(
                _requested_rate_line(now - 1, "60000/1001, approx. 59.94 fps") + "\n",
                encoding="utf-8",
            )
            scripts = self._render_scripts(base)

            calls = base / "swaymsg.calls"
            self._fake_binary(bindir / "swaymsg", f'#!/bin/bash\nprintf \'%s\\n\' "$*" >> "{calls}"\n')

            result = subprocess.run(
                [str(scripts["apply_exact_refresh_script"]), "2560", "1440", f"{now - 5:.6f}"],
                capture_output=True,
                text=True,
                env=self._env(bindir, SUNSHINE_CLIENT_FPS="90"),
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(
                calls.read_text(encoding="utf-8").strip(),
                "output HEADLESS-1 mode 2560x1440@59.94Hz",
            )

    def test_apply_exact_refresh_falls_back_to_requested_fps(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            base = Path(raw)
            bindir = self._fake_bin_dir(base)
            scripts = self._render_scripts(base, stub_resolver=True)
            # Models resolve_stream_fps' contract: silence when the negotiated
            # rate is unknown and no fallback was requested.
            self._fake_binary(
                scripts["resolve_stream_fps_script"],
                '#!/bin/bash\nif [ "${2:-}" = "fallback" ]; then printf \'%s\\n\' "$SUNSHINE_CLIENT_FPS"; fi\n',
            )

            calls = base / "swaymsg.calls"
            self._fake_binary(bindir / "swaymsg", f'#!/bin/bash\nprintf \'%s\\n\' "$*" >> "{calls}"\n')

            result = subprocess.run(
                [str(scripts["apply_exact_refresh_script"]), "2560", "1440", ""],
                capture_output=True,
                text=True,
                env=self._env(bindir, SUNSHINE_CLIENT_FPS="90"),
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(
                calls.read_text(encoding="utf-8").strip(),
                "output HEADLESS-1 mode 2560x1440@90Hz",
            )


if __name__ == "__main__":
    unittest.main()
