"""GPU detection and configuration for the virtual display."""
import os
import re
from pathlib import Path
from typing import Callable, Dict, List

from display.constants import _PCI_IDS_PATHS
from display.state import DisplayState, load_state, save_state
from display.utils import safe_string


def pci_device_name(vendor_hex: str, device_hex: str) -> str:
    """Look up the human-readable device name from pci.ids using vendor/device IDs.
    vendor_hex/device_hex are like '0x1002' / '0x73df'. Returns '' if not found.
    """
    pci_ids_file = None
    for candidate in _PCI_IDS_PATHS:
        if candidate.exists():
            pci_ids_file = candidate
            break
    if pci_ids_file is None:
        return ""
    vendor_prefix = vendor_hex.replace("0x", "").lower()
    device_prefix = device_hex.replace("0x", "").lower()
    if not vendor_prefix or not device_prefix:
        return ""
    try:
        in_vendor = False
        with open(pci_ids_file, encoding="utf-8", errors="replace") as fh:
            for line in fh:
                if not in_vendor:
                    if line.rstrip() and not line[0].isspace() and line.split()[0].lower() == vendor_prefix:
                        in_vendor = True
                    continue
                if line.strip() and not line[0].isspace() and not line.startswith("#"):
                    break
                if line[0] == "\t" and (len(line) < 2 or line[1] != "\t"):
                    parts = line.lstrip("\t").split(None, 1)
                    if parts and parts[0].lower() == device_prefix:
                        return parts[1].strip() if len(parts) > 1 else ""
        return ""
    except OSError:
        return ""


def detect_available_gpus() -> List[Dict[str, str]]:
    """Detect available DRM GPUs from /sys/class/drm/card*.
    Returns a list of dicts with keys: pci_addr, vendor, model, label, card_path, render_path.
    """
    gpus = []
    for entry in sorted(Path("/sys/class/drm").glob("card*")):
        if not re.match(r"^card\d+$", entry.name):
            continue
        try:
            device_link = entry / "device"
            if not device_link.is_symlink():
                continue
            pci_addr = os.path.basename(os.path.realpath(str(device_link)))
            if not pci_addr.startswith("0000:"):
                continue
            vendor_file = entry / "device" / "vendor"
            device_file = entry / "device" / "device"
            if not vendor_file.exists():
                continue
            vendor_id = vendor_file.read_text().strip()
            device_id = device_file.read_text().strip() if device_file.exists() else ""
        except OSError:
            continue
        vendor = "Unknown"
        gpu_type = "Unknown"
        if vendor_id == "0x8086":
            vendor = "Intel"
            gpu_type = "Integrated" if pci_addr == "0000:00:02.0" else "Discrete"
        elif vendor_id == "0x1002":
            vendor = "AMD"
            hwmon_dir = entry / "device" / "hwmon"
            try:
                has_voltage = any(hwmon_dir.glob("hwmon*/in1_input"))
            except OSError:
                has_voltage = False
            gpu_type = "Integrated" if has_voltage else "Discrete"
        elif vendor_id == "0x10de":
            vendor = "NVIDIA"
            gpu_type = "Discrete"
        card_path = f"/dev/dri/by-path/pci-{pci_addr}-card"
        render_path = f"/dev/dri/by-path/pci-{pci_addr}-render"
        model = pci_device_name(vendor_id, device_id)
        if model:
            label = f"{vendor} {model}"
        else:
            label = f"{vendor} ({gpu_type}) [{pci_addr}]"
        gpus.append({
            "pci_addr": pci_addr,
            "vendor": vendor,
            "model": model,
            "label": label,
            "card_path": card_path,
            "render_path": render_path,
        })
    return gpus


def gpu_status_label(state: DisplayState) -> str:
    """Return a human-readable label for the current GPU mode setting."""
    gpu_mode = safe_string(state.gpu_mode).lower()
    if gpu_mode != "manual":
        return "[AUTO] wlroots chooses GPU"
    card_path = safe_string(state.gpu_card_path)
    if not card_path:
        return "[AUTO] wlroots chooses GPU"
    card_exists = Path(card_path).exists()
    if not card_exists:
        return f"[STALE] {card_path} (path no longer exists, falling back to auto)"
    pci_addr = ""
    by_path_prefix = "/dev/dri/by-path/pci-"
    if card_path.startswith(by_path_prefix) and card_path.endswith("-card"):
        pci_addr = card_path[len(by_path_prefix):-len("-card")]
    gpus = detect_available_gpus()
    for gpu in gpus:
        if gpu["pci_addr"] == pci_addr:
            return f"[MANUAL] {gpu['label']} ({card_path})"
    return f"[MANUAL] {card_path}"


def _save_state(state: DisplayState) -> DisplayState:
    save_state(state)
    return state


def set_gpu_mode(
    mode: str,
    card_path: str = "",
    render_path: str = "",
    *,
    load_state_fn: Callable[[], DisplayState] = load_state,
    refresh_managed_files_fn: Callable[[DisplayState], DisplayState] = _save_state,
) -> DisplayState:
    state = load_state_fn()
    if mode not in ("auto", "manual"):
        mode = "auto"
    state.gpu_mode = mode
    if mode == "manual" and card_path:
        state.gpu_card_path = card_path
        state.gpu_render_path = render_path
    else:
        state.gpu_card_path = ""
        state.gpu_render_path = ""
    return refresh_managed_files_fn(state)


def configure_gpu(
    *,
    load_state_fn: Callable[[], DisplayState] = load_state,
    refresh_managed_files_fn: Callable[[DisplayState], DisplayState] = _save_state,
    restart_fn: Callable[[], int] = lambda: 0,
) -> int:
    """Interactive GPU selection for the virtual display."""
    from utils.input import get_user_input, get_yes_no_input

    state = load_state_fn()
    print("")
    print("Virtual display GPU selection")
    print("Pick which graphics card drives the virtual display.")
    print("Most users should leave this on Auto.")
    current_label = gpu_status_label(state)
    print(f"Current: {current_label}")
    print("")
    print("  0. Auto — let the system decide")
    gpus = detect_available_gpus()
    if not gpus:
        print("")
        print("No DRM GPUs detected on this system.")
        gpu_mode = safe_string(state.gpu_mode).lower()
        if gpu_mode == "manual":
            print("Current manual GPU selection will be cleared to Auto.")
            if get_user_input(
                "Reset GPU selection to Auto? (y/n): ",
                lambda value: value.strip().lower() if value.strip().lower() in {"y", "n"} else (_ for _ in ()).throw(ValueError()),
                "Enter y or n.",
            ) == "y":
                set_gpu_mode("auto", load_state_fn=load_state_fn, refresh_managed_files_fn=refresh_managed_files_fn)
                print("GPU selection reset to Auto.")
        return 0
    valid_choices = ["0"]
    for i, gpu in enumerate(gpus, start=1):
        card_exists = "OK" if Path(gpu["card_path"]).exists() else "MISSING"
        print(f"  {i}. {gpu['label']} — {gpu['card_path']} ({card_exists})")
        valid_choices.append(str(i))
    print("")
    choice = get_user_input(
        "Choose a GPU for the virtual display: ",
        lambda value: value.strip() if value.strip() in valid_choices else (_ for _ in ()).throw(ValueError()),
        f"Enter a number from 0 to {len(gpus)}.",
    )
    if choice == "0":
        set_gpu_mode("auto", load_state_fn=load_state_fn, refresh_managed_files_fn=refresh_managed_files_fn)
        print("GPU selection set to Auto.")
    else:
        idx = int(choice) - 1
        gpu = gpus[idx]
        set_gpu_mode(
            "manual",
            gpu["card_path"],
            gpu["render_path"],
            load_state_fn=load_state_fn,
            refresh_managed_files_fn=refresh_managed_files_fn,
        )
        print(f"GPU selection set to {gpu['label']} ({gpu['card_path']}).")

    print("")
    if get_yes_no_input("Restart the virtual display for the change to take effect?", default=True):
        return restart_fn()
    return 0
