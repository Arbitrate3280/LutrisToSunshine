import glob
import os
import time
import zlib
from pathlib import Path
from typing import Any, Dict, List, Optional

from utils.input import get_user_input
from utils.terminal import accent, badge, heading, muted
from display import manager


BUS_BLUETOOTH = 0x05
DS4_VENDOR_ID = "054c"
DS4_BLUETOOTH_PRODUCTS = {"05c4", "09cc"}


def _read_text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8").strip()
    except OSError:
        return ""


def _read_hex(path: Path) -> str:
    value = _read_text(path).lower()
    if not value:
        return ""
    value = value.removeprefix("0x")
    try:
        return f"{int(value, 16):04x}"
    except ValueError:
        return ""


def _read_int(path: Path) -> int:
    value = _read_text(path)
    if not value:
        return 0
    try:
        return int(value, 0)
    except ValueError:
        try:
            return int(value, 16)
        except ValueError:
            return 0


def _selection_id_from_phys(phys: str) -> str:
    phys = manager._safe_string(phys)
    if not phys.startswith(manager.BRIDGE_DEVICE_PHYS_PREFIX):
        return ""
    return phys[len(manager.BRIDGE_DEVICE_PHYS_PREFIX):]


def _parse_uevent(path: Path) -> Dict[str, str]:
    data: Dict[str, str] = {}
    for line in _read_text(path).splitlines():
        if "=" not in line:
            continue
        key, value = line.split("=", 1)
        data[key] = value
    return data


def _hid_phys_from_sys_device(sys_device: Path) -> str:
    current = sys_device.resolve()
    for candidate in [current, *current.parents[:6]]:
        uevent_path = candidate / "uevent"
        if not uevent_path.exists():
            continue
        phys = manager._safe_string(_parse_uevent(uevent_path).get("HID_PHYS"))
        if phys:
            return phys
    return ""


def _supports_ff(event_path: str) -> bool:
    error = manager._evdev_import_error()
    if error:
        return False

    from evdev import InputDevice, ecodes

    device = None
    try:
        device = InputDevice(event_path)
        ff_caps = device.capabilities(absinfo=False).get(ecodes.EV_FF, [])
        return ecodes.FF_RUMBLE in ff_caps or bool(ff_caps)
    except OSError:
        return False
    finally:
        if device is not None:
            device.close()


def _input_device_details(event_path: str) -> Dict[str, Any]:
    event_name = os.path.basename(event_path)
    sys_input = Path("/sys/class/input") / event_name / "device"
    return {
        "name": _read_text(sys_input / "name"),
        "vendor_id": _read_hex(sys_input / "id" / "vendor"),
        "product_id": _read_hex(sys_input / "id" / "product"),
        "bustype": _read_int(sys_input / "id" / "bustype"),
    }


def list_live_bridge_devices() -> List[Dict[str, Any]]:
    state = manager.load_state()
    labels = {
        selection["selection_id"]: selection["label"]
        for selection in state.get("exclusive_input_devices", {}).get("devices", [])
        if selection.get("selection_id")
    }

    devices: Dict[str, Dict[str, Any]] = {}
    runtime_status = manager._input_bridge_status(state)

    for item in runtime_status.get("devices", []):
        if not isinstance(item, dict):
            continue
        selection_id = manager._safe_string(item.get("selection_id"))
        if not selection_id:
            continue
        event_path = manager._safe_string(item.get("virtual_event_path")) or manager._safe_string(item.get("matched_path"))
        hidraw_path = manager._safe_string(item.get("virtual_hidraw_path")) or manager._safe_string(item.get("hidraw_path"))
        event_details = _input_device_details(event_path) if event_path and Path(event_path).exists() else {}
        devices[selection_id] = {
            "selection_id": selection_id,
            "label": labels.get(selection_id, manager._safe_string(item.get("label")) or selection_id),
            "event_path": event_path if event_path and Path(event_path).exists() else "",
            "hidraw_path": hidraw_path if hidraw_path and Path(hidraw_path).exists() else "",
            "name": event_details.get("name") or manager._safe_string(item.get("source_name")) or labels.get(selection_id, selection_id),
            "vendor_id": event_details.get("vendor_id") or manager._safe_string(item.get("source_vendor")).lower(),
            "product_id": event_details.get("product_id") or manager._safe_string(item.get("source_product")).lower(),
            "bustype": event_details.get("bustype", 0),
            "supports_ff": bool(item.get("ff_supported")),
            "bridge_mode": manager._safe_string(item.get("bridge_mode")),
            "runtime_backed": True,
        }

    for event_path in sorted(glob.glob("/dev/input/event*")):
        event_name = os.path.basename(event_path)
        sys_input = Path("/sys/class/input") / event_name / "device"
        selection_id = _selection_id_from_phys(_hid_phys_from_sys_device(sys_input))
        if not selection_id:
            continue
        devices.setdefault(
            selection_id,
            {
                "selection_id": selection_id,
                "label": labels.get(selection_id, selection_id),
                "event_path": "",
                "hidraw_path": "",
                "name": "",
                "vendor_id": "",
                "product_id": "",
                "bustype": 0,
                "supports_ff": False,
                "bridge_mode": "",
                "runtime_backed": False,
            },
        )
        details = _input_device_details(event_path)
        devices[selection_id].update(
            {
                "event_path": event_path,
                "name": details["name"] or labels.get(selection_id, selection_id),
                "vendor_id": details["vendor_id"],
                "product_id": details["product_id"],
                "bustype": details["bustype"],
                "supports_ff": _supports_ff(event_path),
            }
        )

    for hidraw_path in sorted(glob.glob("/dev/hidraw*")):
        hidraw_name = os.path.basename(hidraw_path)
        sys_hidraw = Path("/sys/class/hidraw") / hidraw_name / "device"
        selection_id = _selection_id_from_phys(_hid_phys_from_sys_device(sys_hidraw))
        if not selection_id:
            continue
        devices.setdefault(
            selection_id,
            {
                "selection_id": selection_id,
                "label": labels.get(selection_id, selection_id),
                "event_path": "",
                "hidraw_path": "",
                "name": labels.get(selection_id, selection_id),
                "vendor_id": "",
                "product_id": "",
                "bustype": 0,
                "supports_ff": False,
                "bridge_mode": "",
                "runtime_backed": False,
            },
        )
        devices[selection_id]["hidraw_path"] = hidraw_path

    return sorted(
        [
            device
            for device in devices.values()
            if device.get("event_path") or device.get("hidraw_path")
        ],
        key=lambda item: (item["label"].lower(), item["selection_id"]),
    )


def _select_target_devices(devices: List[Dict[str, Any]], selector: str) -> List[Dict[str, Any]]:
    selector = manager._safe_string(selector)
    if not selector:
        return list(devices)
    if selector.isdigit():
        index = int(selector) - 1
        if 0 <= index < len(devices):
            return [devices[index]]
        return []

    lowered = selector.lower()
    matches = []
    for device in devices:
        label = device["label"].lower()
        selection_id = device["selection_id"].lower()
        if lowered in {selection_id, label} or lowered in label:
            matches.append(device)
    return matches


def _play_evdev_rumble(
    device_info: Dict[str, Any],
    strong_magnitude: int,
    weak_magnitude: int,
    duration: float,
    repeat: int,
    pause: float,
) -> None:
    from evdev import InputDevice, ecodes, ff

    event_path = device_info["event_path"]
    if not event_path:
        raise RuntimeError("No bridged event device found.")

    device = InputDevice(event_path)
    effect_id = None
    try:
        ff_caps = device.capabilities(absinfo=False).get(ecodes.EV_FF, [])
        if ecodes.FF_RUMBLE not in ff_caps and not ff_caps:
            raise RuntimeError("Bridged event device does not expose EV_FF rumble.")

        rumble = ff.Rumble(
            strong_magnitude=max(0, min(int(strong_magnitude), 0xFFFF)),
            weak_magnitude=max(0, min(int(weak_magnitude), 0xFFFF)),
        )
        effect = ff.Effect(
            ecodes.FF_RUMBLE,
            -1,
            0,
            ff.Trigger(0, 0),
            ff.Replay(int(max(duration, 0.05) * 1000), 0),
            ff.EffectType(ff_rumble_effect=rumble),
        )
        effect_id = device.upload_effect(effect)

        for _ in range(max(1, repeat)):
            device.write(ecodes.EV_FF, effect_id, 1)
            device.syn()
            time.sleep(max(duration, 0.05))
            device.write(ecodes.EV_FF, effect_id, 0)
            device.syn()
            if pause > 0:
                time.sleep(pause)
    finally:
        if effect_id is not None:
            try:
                device.erase_effect(effect_id)
            except OSError:
                pass
        device.close()


def build_ds4_bt_rumble_report(
    strong_magnitude: int,
    weak_magnitude: int,
    red: int = 0,
    green: int = 0,
    blue: int = 32,
    flash_on: int = 0,
    flash_off: int = 0,
) -> bytes:
    strong = max(0, min(int(strong_magnitude) >> 8, 0xFF))
    weak = max(0, min(int(weak_magnitude) >> 8, 0xFF))
    payload = bytearray(78)
    payload[0] = 0x11
    payload[1] = 0xC0
    payload[2] = 0x20
    payload[3] = 0x07
    payload[4] = 0x00
    payload[5] = weak
    payload[6] = strong
    payload[7] = max(0, min(int(red), 0xFF))
    payload[8] = max(0, min(int(green), 0xFF))
    payload[9] = max(0, min(int(blue), 0xFF))
    payload[10] = max(0, min(int(flash_on), 0xFF))
    payload[11] = max(0, min(int(flash_off), 0xFF))
    crc = zlib.crc32(b"\xA2")
    crc = zlib.crc32(payload[:-4], crc)
    payload[-4:] = crc.to_bytes(4, byteorder="little")
    return bytes(payload)


def _play_ds4_bt_hidraw_rumble(
    device_info: Dict[str, Any],
    strong_magnitude: int,
    weak_magnitude: int,
    duration: float,
    repeat: int,
    pause: float,
) -> None:
    if device_info["vendor_id"] != DS4_VENDOR_ID or device_info["product_id"] not in DS4_BLUETOOTH_PRODUCTS:
        raise RuntimeError("Bridged hidraw device is not a supported Bluetooth DS4.")
    if device_info["bustype"] != BUS_BLUETOOTH:
        raise RuntimeError("DS4 hidraw rumble test currently supports Bluetooth controllers only.")
    if not device_info["hidraw_path"]:
        raise RuntimeError("No bridged hidraw device found.")

    start_report = build_ds4_bt_rumble_report(strong_magnitude, weak_magnitude)
    stop_report = build_ds4_bt_rumble_report(0, 0)
    hidraw_fd = os.open(device_info["hidraw_path"], os.O_WRONLY | os.O_NONBLOCK)
    try:
        for _ in range(max(1, repeat)):
            os.write(hidraw_fd, start_report)
            time.sleep(max(duration, 0.05))
            os.write(hidraw_fd, stop_report)
            if pause > 0:
                time.sleep(pause)
    finally:
        os.close(hidraw_fd)


def _select_devices_interactively(devices: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    if len(devices) <= 1:
        return devices

    print("Live bridged controllers:")
    for index, device in enumerate(devices, start=1):
        bits = []
        if device["event_path"]:
            bits.append(os.path.basename(device["event_path"]))
        if device["hidraw_path"]:
            bits.append(os.path.basename(device["hidraw_path"]))
        if device["supports_ff"]:
            bits.append("evdev-ff")
        print(f"{index}. {device['label']} [{' | '.join(bits)}]")

    selected_index = get_user_input(
        "Choose a controller to test: ",
        lambda raw: int(raw.strip()) - 1 if 0 < int(raw.strip()) <= len(devices) else (_ for _ in ()).throw(ValueError()),
        "Invalid selection. Enter one of the listed numbers.",
    )
    return [devices[selected_index]]


def test_bridge_rumble(
    selector: str = "",
    mode: str = "auto",
    duration: float = 1.0,
    repeat: int = 2,
    pause: float = 0.4,
    strong_magnitude: int = 0xC000,
    weak_magnitude: int = 0x8000,
) -> int:
    evdev_error = manager._evdev_import_error()
    if evdev_error:
        print(f"{badge('FAIL', 'error')} {evdev_error}")
        return 1

    devices = list_live_bridge_devices()
    if not devices:
        print(f"{badge('FAIL', 'error')} No live bridged controllers detected.")
        print("Start the virtual display stack and make sure a selected controller is currently bridged.")
        return 1

    targets = _select_target_devices(devices, selector)
    if not selector and len(targets) > 1:
        targets = _select_devices_interactively(targets)
    if not targets:
        print(f"{badge('FAIL', 'error')} No live bridged controller matched '{selector}'.")
        return 1

    success = False
    for device in targets:
        print(heading(f"Testing {device['label']}"))
        print(f"{accent('event:')} {device['event_path'] or muted('none')}")
        print(f"{accent('hidraw:')} {device['hidraw_path'] or muted('none')}")
        if device.get("bridge_mode"):
            print(f"{accent('bridge:')} {device['bridge_mode']}")
        if device.get("runtime_backed"):
            print(f"{accent('discovery:')} runtime bridge status")
        print(f"{accent('mode:')} {mode}")

        modes = [mode]
        if mode == "auto":
            modes = ["evdev", "hidraw-ds4"]

        for current_mode in modes:
            try:
                if current_mode == "evdev":
                    _play_evdev_rumble(
                        device,
                        strong_magnitude=strong_magnitude,
                        weak_magnitude=weak_magnitude,
                        duration=duration,
                        repeat=repeat,
                        pause=pause,
                    )
                    print(f"  {badge('PASS', 'success')} evdev rumble signal sent.")
                    success = True
                elif current_mode == "hidraw-ds4":
                    _play_ds4_bt_hidraw_rumble(
                        device,
                        strong_magnitude=strong_magnitude,
                        weak_magnitude=weak_magnitude,
                        duration=duration,
                        repeat=repeat,
                        pause=pause,
                    )
                    print(f"  {badge('PASS', 'success')} DS4 hidraw rumble signal sent.")
                    success = True
                else:
                    print(f"  {badge('WARN', 'warning')} unsupported mode: {current_mode}")
            except Exception as error:
                print(f"  {badge('WARN', 'warning')} {current_mode} failed: {error}")

    if not success:
        print(f"{badge('FAIL', 'error')} No rumble signal path succeeded.")
        return 1
    print(f"{badge('PASS', 'success')} Rumble test completed.")
    return 0
