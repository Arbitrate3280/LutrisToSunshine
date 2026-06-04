"""Shared test helpers for the display package.

Centralises the boilerplate that every display test needs: monkey-
patching manager functions and creating a temporary display state
pointing at a real on-disk directory tree.  Use the context managers
from this module instead of repeating ``original = manager.foo`` /
``manager.foo = ...`` / ``manager.foo = original`` blocks in every
test case.
"""

from __future__ import annotations

import tempfile
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Dict, Iterator, Optional, Tuple


@contextmanager
def patched(manager: Any, **overrides: Any) -> Iterator[None]:
    """Temporarily replace attributes on ``manager`` for the duration of the block.

    Example::

        with patched(manager, load_state=lambda: state, save_state=lambda c: None):
            manager.remove_display()
    """
    originals = {name: getattr(manager, name) for name in overrides}
    try:
        for name, value in overrides.items():
            setattr(manager, name, value)
        yield
    finally:
        for name, original in originals.items():
            setattr(manager, name, original)


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
) -> Iterator[Tuple[Dict[str, Any], Path]]:
    """Yield ``(state, base_dir)`` pointing at a temporary on-disk layout.

    Any ``paths`` key that is not provided keeps the value from
    :func:`manager._default_state` -- with one exception: the standard
    script and status file keys are materialised as placeholder files
    under ``base_dir`` so remove/cleanup tests have something real to
    delete.
    """
    extra_paths = dict(extra_paths or {})
    explicit: set = set()
    with tempfile.TemporaryDirectory() as raw:
        base = Path(raw)
        state = manager._default_state()
        state["paths"] = dict(state["paths"])
        if state_overrides:
            for key, value in state_overrides.items():
                state[key] = value

        if systemd_user_dir is not None:
            state["paths"]["systemd_user_dir"] = str(systemd_user_dir)
            explicit.add("systemd_user_dir")
        if sunshine_override is not None:
            state["paths"]["sunshine_override"] = str(sunshine_override)
            explicit.add("sunshine_override")
        if sunshine_override_dir is not None:
            state["paths"]["sunshine_override_dir"] = str(sunshine_override_dir)
            explicit.add("sunshine_override_dir")
        if sunshine_wrapper_script is not None:
            state["paths"]["sunshine_wrapper_script"] = str(sunshine_wrapper_script)
            explicit.add("sunshine_wrapper_script")

        for key in (
            "state_path",
            "sunshine_wrapper_script",
            "input_bridge_script",
            "kwin_input_isolation_script",
            "audio_guard_script",
            "portal_active_file",
            "portal_lock_file",
            "input_bridge_status_file",
            "kwin_input_isolation_status_file",
            "wayland_display_file",
            "audio_module_file",
        ):
            if key in extra_paths:
                target = extra_paths[key]
                if target is None:
                    state["paths"].pop(key, None)
                    continue
                state["paths"][key] = str(target)
                continue
            if key in explicit:
                continue
            placeholder = base / f"{key}.placeholder"
            placeholder.write_text("managed\n", encoding="utf-8")
            state["paths"][key] = str(placeholder)

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
