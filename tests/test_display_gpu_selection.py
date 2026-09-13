"""Tests for virtual-display GPU selection ("Choose which GPU to use").

The manual GPU choice only reaches sway through the rendered
``@GPU_DETECTION_BLOCK@`` / ``@GPU_ENV_VARS_BLOCK@`` placeholders, so these
tests assert on the rendered scripts rather than on a helper script: the
former ``get_gpu_addr.sh`` was removed as dead weight, and nothing else
guards this wiring.
"""

import pathlib
import unittest
from unittest.mock import MagicMock, patch

from display import gpu
from display import scripts_render
from display import state as display_state


CARD = "/dev/dri/by-path/pci-0000:2d:00.0-card"
RENDER = "/dev/dri/by-path/pci-0000:2d:00.0-render"


def _manual_state() -> display_state.DisplayState:
    state = display_state.default_state()
    state.enabled = True
    state.gpu_mode = "manual"
    state.gpu_card_path = CARD
    state.gpu_render_path = RENDER
    return state


def _rendered(state, key: str) -> str:
    return scripts_render.render_managed_files(state)[pathlib.Path(getattr(state.paths, key))]


class GpuSelectionRenderingTests(unittest.TestCase):
    def test_manual_mode_bakes_wlr_device_vars_into_sway_and_launch(self) -> None:
        sway = _rendered(_manual_state(), "sway_start_script")
        launch = _rendered(_manual_state(), "launch_app_script")

        for script in (sway, launch):
            self.assertIn("WLR_DRM_DEVICES", script)
            self.assertIn("WLR_RENDER_DRM_DEVICE", script)
        self.assertIn(f'[[ -e "{CARD}" ]] && [[ -e "{RENDER}" ]]', sway)
        self.assertIn(CARD, sway)

    def test_auto_mode_emits_no_wlr_device_vars(self) -> None:
        state = display_state.default_state()
        state.enabled = True

        self.assertNotIn("WLR_DRM_DEVICES", _rendered(state, "sway_start_script"))
        self.assertNotIn("WLR_DRM_DEVICES", _rendered(state, "launch_app_script"))

    def test_manual_mode_without_saved_paths_emits_no_wlr_device_vars(self) -> None:
        state = _manual_state()
        state.gpu_card_path = ""

        self.assertNotIn("WLR_DRM_DEVICES", _rendered(state, "sway_start_script"))


class SetGpuModeTests(unittest.TestCase):
    def _state(self) -> display_state.DisplayState:
        return display_state.default_state()

    def test_manual_persists_card_and_render_paths(self) -> None:
        state = self._state()
        refreshed = []
        gpu.set_gpu_mode(
            "manual",
            CARD,
            RENDER,
            load_state_fn=lambda: state,
            refresh_managed_files_fn=lambda current: refreshed.append(current) or current,
        )

        self.assertEqual(refreshed, [state])
        self.assertEqual(state.gpu_mode, "manual")
        self.assertEqual(state.gpu_card_path, CARD)
        self.assertEqual(state.gpu_render_path, RENDER)

    def test_auto_clears_saved_paths(self) -> None:
        state = _manual_state()
        gpu.set_gpu_mode(
            "auto",
            load_state_fn=lambda: state,
            refresh_managed_files_fn=lambda current: current,
        )

        self.assertEqual(state.gpu_mode, "auto")
        self.assertEqual(state.gpu_card_path, "")
        self.assertEqual(state.gpu_render_path, "")

    def test_unknown_mode_falls_back_to_auto(self) -> None:
        state = _manual_state()
        gpu.set_gpu_mode(
            "bogus",
            load_state_fn=lambda: state,
            refresh_managed_files_fn=lambda current: current,
        )

        self.assertEqual(state.gpu_mode, "auto")
        self.assertEqual(state.gpu_card_path, "")


class ConfigureGpuTests(unittest.TestCase):
    def test_picking_a_detected_gpu_sets_manual_paths(self) -> None:
        state = display_state.default_state()
        state.enabled = True
        detected = gpu.detect_available_gpus()
        self.assertTrue(detected, "expected at least one DRM GPU to test against")
        chosen = detected[0]

        prompt = MagicMock(return_value="1")
        with patch("utils.input.get_user_input", prompt), \
             patch("utils.input.get_yes_no_input", return_value=False):
            result = gpu.configure_gpu(
                load_state_fn=lambda: state,
                refresh_managed_files_fn=lambda current: current,
                restart_fn=lambda: 0,
            )

        self.assertEqual(result, 0)
        self.assertEqual(state.gpu_mode, "manual")
        self.assertEqual(state.gpu_card_path, chosen["card_path"])
        self.assertEqual(state.gpu_render_path, chosen["render_path"])

        # The menu validator must reject numbers outside the listed GPUs.
        validator = prompt.call_args.args[1]
        self.assertEqual(validator("0"), "0")
        self.assertEqual(validator(str(len(detected))), str(len(detected)))
        with self.assertRaises(ValueError):
            validator(str(len(detected) + 1))

    def test_choice_zero_returns_to_auto(self) -> None:
        state = _manual_state()

        with patch("utils.input.get_user_input", MagicMock(return_value="0")), \
             patch("utils.input.get_yes_no_input", return_value=False):
            result = gpu.configure_gpu(
                load_state_fn=lambda: state,
                refresh_managed_files_fn=lambda current: current,
                restart_fn=lambda: 0,
            )

        self.assertEqual(result, 0)
        self.assertEqual(state.gpu_mode, "auto")
        self.assertEqual(state.gpu_card_path, "")

    def test_stale_saved_path_is_reported(self) -> None:
        state = _manual_state()
        state.gpu_card_path = "/dev/dri/by-path/pci-0000:ff:00.0-card"

        self.assertIn("[STALE]", gpu.gpu_status_label(state))


if __name__ == "__main__":
    unittest.main()
