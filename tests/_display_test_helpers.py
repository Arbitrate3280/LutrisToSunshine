"""Shared test helpers for the display package."""

from __future__ import annotations

import dataclasses
import tempfile
from contextlib import contextmanager
from dataclasses import asdict
from pathlib import Path
from typing import Any, Dict, Iterator, Optional, Tuple

from display.state import DisplayPaths, DisplayState, default_state


@contextmanager
def patched(target: Any, **overrides: Any) -> Iterator[None]:
    """Temporarily replace attributes on a module/object under test."""
    originals = {name: getattr(target, name) for name in overrides}
    try:
        for name, value in overrides.items():
            setattr(target, name, value)
        yield
    finally:
        for name, original in originals.items():
            setattr(target, name, original)


# DisplayPaths fields that name a directory rather than a file.
_TEMP_DIR_PATH_FIELDS = frozenset(
    {"bin_root", "profile_root", "systemd_user_dir", "sunshine_override_dir"}
)


def assert_state_paths_under(state: DisplayState, base: Path) -> None:
    """Fail if any state path escapes ``base`` -- i.e. points at the real install."""
    leaked = sorted(
        f"{key}={value}"
        for key, value in asdict(state.paths).items()
        if value and not str(value).startswith(str(base))
    )
    assert not leaked, f"state paths leaked outside {base}: {leaked}"


def snapshot_fixture(**overrides: Any) -> Dict[str, Any]:
    """Return the pseudo DisplaySnapshot the hub views consume.

    The CLI rendering tests assert on rendered labels, so keep the full
    field set here and override only what each test exercises.
    """
    snapshot: Dict[str, Any] = {
        'configured': False,
        'dynamic_mangohud_fps_limit': False,
        'current_mangohud_config': '',
        'refresh_rate_sync_mode': 'client',
        'host_session': 'unknown',
        'input_isolation_mode': 'permissions-only',
        'sunshine_active': False,
        'sway_active': False,
        'bridge_state': 'inactive',
        'wireplumber_policy': 'absent',
        'portal_handoff_active': False,
        'dependencies_missing': [],
        'wayland_display': '',
        'current_headless_mode': '',
        'controller_detection_error': None,
        'controller_count': 0,
        'controllers': [],
        'gpu_status_label': '[AUTO] wlroots chooses GPU',
        'renderer_mode': 'default',
        'renderer_status_label': '[DEFAULT] wlroots default renderer',
        'next_step': 'Run enable.',
        'status_summary': 'NOT SET UP',
        'display_sync_summary': "Moonlight's requested FPS (integer, e.g. 60/90/120); MangoHud FPS sync off",
        'refresh_rate_sync_mode_summary': "Moonlight's requested FPS (integer, e.g. 60/90/120)",
        'isolation_summary': 'rule ready',
        'isolation_level': 'success',
    }
    snapshot.update(overrides)
    return snapshot

def redirect_state_paths(state: DisplayState, base: Path) -> None:
    """Point every :class:`DisplayPaths` entry at ``base``.

    A state built from :func:`default_state` carries the *real*
    ``~/.config/lutristosunshine`` paths. Anything that unlinks through such a
    state (``remove_display``, the audio policy, the rendered cleanup script)
    would then edit the developer's live install, so every field has to be
    redirected -- including ones added later.
    """
    for field in dataclasses.fields(DisplayPaths):
        target = base / field.name
        if field.name in _TEMP_DIR_PATH_FIELDS:
            target.mkdir(parents=True, exist_ok=True)
        setattr(state.paths, field.name, str(target))


@contextmanager
def temp_display_state(
    manager: Any,
    *,
    systemd_user_dir: Optional[Path] = None,
    sunshine_override: Optional[Path] = None,
    sunshine_override_dir: Optional[Path] = None,
    sunshine_wrapper_script: Optional[Path] = None,
    extra_paths: Optional[Dict[str, Path]] = None,
    state_overrides: Optional[Dict[str, Any]] = None,
) -> Iterator[Tuple[DisplayState, Path]]:
    """Yield ``(state, base_dir)`` pointing at a temporary on-disk layout.

    Every ``DisplayPaths`` entry is redirected under ``base_dir`` (files
    materialised as placeholders so remove/cleanup tests have something real
    to delete) unless the caller overrides it explicitly.
    """
    extra_paths = dict(extra_paths or {})
    explicit: set = set()
    with tempfile.TemporaryDirectory() as raw:
        base = Path(raw)
        state = default_state()
        state.paths = DisplayPaths(**asdict(state.paths))
        if state_overrides:
            for key, value in state_overrides.items():
                setattr(state, key, value)

        if systemd_user_dir is not None:
            state.paths.systemd_user_dir = str(systemd_user_dir)
            explicit.add("systemd_user_dir")
        if sunshine_override is not None:
            state.paths.sunshine_override = str(sunshine_override)
            explicit.add("sunshine_override")
        if sunshine_override_dir is not None:
            state.paths.sunshine_override_dir = str(sunshine_override_dir)
            explicit.add("sunshine_override_dir")
        if sunshine_wrapper_script is not None:
            state.paths.sunshine_wrapper_script = str(sunshine_wrapper_script)
            explicit.add("sunshine_wrapper_script")

        for field in dataclasses.fields(DisplayPaths):
            key = field.name
            if key in extra_paths:
                target = extra_paths[key]
                if target is None:
                    setattr(state.paths, key, "")
                    continue
                setattr(state.paths, key, str(target))
                continue
            if key in explicit:
                continue
            if key in _TEMP_DIR_PATH_FIELDS:
                directory = base / key
                directory.mkdir(parents=True, exist_ok=True)
                setattr(state.paths, key, str(directory))
                continue
            placeholder = base / f"{key}.placeholder"
            placeholder.write_text("managed\n", encoding="utf-8")
            setattr(state.paths, key, str(placeholder))

        yield state, base


def write_managed_override(path: Path, wrapper: Path) -> None:
    """Write an override.conf containing the marker this tool emits."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        f"[Service]\nExecStart=\nExecStart={wrapper}\n",
        encoding="utf-8",
    )


def write_user_override(path: Path, body: str = "USER_OVERRIDE=keep") -> None:
    """Write a user-managed override.conf that has no marker."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        f"[Service]\nEnvironment={body}\n",
        encoding="utf-8",
    )
