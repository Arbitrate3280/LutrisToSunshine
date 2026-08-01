#!@PYTHON_EXECUTABLE@
import json
import os
import re
import signal
import subprocess
import time
import xml.etree.ElementTree as ET
from pathlib import Path

STATUS_PATH = Path('@KWIN_INPUT_ISOLATION_STATUS_FILE@')
SERVICE_CANDIDATES = ["org.kde.KWin", "org.kde.KWin.InputDevice"]
ROOT_PATHS = ["/org/kde/KWin/InputDevice", "/org/kde/KWin"]
DEVICE_INTERFACE = "org.kde.KWin.InputDevice"
SUNSHINE_VENDOR_ID = @SUNSHINE_VENDOR_ID@
SUNSHINE_PRODUCT_ID = @SUNSHINE_PRODUCT_ID@
NAME_MARKERS = @NAME_MARKERS@
STOP = False


def safe_string(value):
    return str(value or "").strip()


def is_plasma_session():
    current_desktop = safe_string(os.environ.get("XDG_CURRENT_DESKTOP")).lower()
    session_desktop = safe_string(os.environ.get("XDG_SESSION_DESKTOP")).lower()
    desktop_session = safe_string(os.environ.get("DESKTOP_SESSION")).lower()
    kde_full_session = safe_string(os.environ.get("KDE_FULL_SESSION")).lower()
    plasma_markers = ("kde", "plasma", "kwin")
    return (
        kde_full_session in {"1", "true", "yes", "on"}
        or any(marker in current_desktop for marker in plasma_markers)
        or any(marker in session_desktop for marker in plasma_markers)
        or any(marker in desktop_session for marker in plasma_markers)
    )


def write_status(state="inactive", service="", disabled_devices=None, failed_devices=None, seen_device_count=0, last_error=""):
    payload = {
        "state": state,
        "service": service,
        "disabled_devices": disabled_devices or [],
        "failed_devices": failed_devices or [],
        "seen_device_count": seen_device_count,
        "last_error": safe_string(last_error),
    }
    STATUS_PATH.parent.mkdir(parents=True, exist_ok=True)
    STATUS_PATH.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")


def handle_signal(_signum, _frame):
    global STOP
    STOP = True


def run_gdbus(*args):
    result = subprocess.run(
        ["gdbus", *args],
        text=True,
        capture_output=True,
        check=False,
    )
    if result.returncode != 0:
        raise RuntimeError(safe_string(result.stderr) or f"gdbus exit {result.returncode}")
    stdout = result.stdout.strip()
    if not stdout:
        raise RuntimeError("gdbus returned no output")
    return stdout


def unbox_variant(output):
    text = safe_string(output)
    if text.startswith("(") and text.endswith(")"):
        text = text[1:-1].strip()
    if text.endswith(","):
        text = text[:-1].strip()
    while text.startswith("<") and text.endswith(">"):
        text = text[1:-1].strip()
    if text.startswith("'") and text.endswith("'"):
        return text[1:-1]
    lowered = text.lower()
    if lowered == "true":
        return True
    if lowered == "false":
        return False
    if text.startswith("uint") or text.startswith("int"):
        parts = text.split(None, 1)
        if len(parts) == 2 and parts[1].isdigit():
            return int(parts[1])
    if text.isdigit():
        return int(text)
    return text


def introspect_xml(service, path):
    return run_gdbus("introspect", "--session", "--dest", service, "--object-path", path, "--xml")


def discover_roots(service):
    roots = []
    last_error = ""
    for path in ROOT_PATHS:
        try:
            xml_text = introspect_xml(service, path)
            if xml_text:
                roots.append(path)
        except Exception as exc:
            last_error = safe_string(exc)
    return roots, last_error


def find_device_paths(service, roots):
    paths = []
    seen = set()
    queue = list(roots)
    while queue:
        path = queue.pop(0)
        if path in seen:
            continue
        seen.add(path)
        try:
            xml_text = introspect_xml(service, path)
        except Exception:
            continue
        try:
            root = ET.fromstring(xml_text)
        except ET.ParseError:
            continue
        if path.endswith(tuple(f"/event{index}" for index in range(0, 512))):
            paths.append(path)
        for node in root.findall("node"):
            name = safe_string(node.attrib.get("name"))
            if not name:
                continue
            child = path.rstrip("/") + "/" + name
            if re.fullmatch(r"event\d+", name):
                paths.append(child)
            elif child not in seen and child not in queue and child.count("/") <= 6:
                queue.append(child)
    return sorted(set(paths))


def interface_properties(service, path):
    xml_text = introspect_xml(service, path)
    root = ET.fromstring(xml_text)
    props = {}
    for iface in root.findall("interface"):
        if iface.attrib.get("name") != DEVICE_INTERFACE:
            continue
        for prop in iface.findall("property"):
            name = safe_string(prop.attrib.get("name"))
            if name:
                props[name] = safe_string(prop.attrib.get("type"))
    return props


def get_property(service, path, name):
    value = run_gdbus(
        "call",
        "--session",
        "--dest",
        service,
        "--object-path",
        path,
        "--method",
        "org.freedesktop.DBus.Properties.Get",
        DEVICE_INTERFACE,
        name,
    )
    return unbox_variant(value)


def set_enabled(service, path, enabled, property_name="enabled"):
    variant = "<true>" if enabled else "<false>"
    run_gdbus(
        "call",
        "--session",
        "--dest",
        service,
        "--object-path",
        path,
        "--method",
        "org.freedesktop.DBus.Properties.Set",
        DEVICE_INTERFACE,
        property_name,
        variant,
    )


def normalize_hex(value):
    if isinstance(value, bool):
        return ""
    if isinstance(value, int):
        return f"{value:04x}"
    text = safe_string(value).lower()
    if not text:
        return ""
    if text.startswith("0x"):
        text = text[2:]
    if text.isdigit():
        return f"{int(text):04x}"
    return text


def normalize_name(value):
    text = safe_string(value).replace("_", " ").lower()
    return " ".join(part for part in text.split() if part)


def device_details(service, path):
    props = interface_properties(service, path)
    if not props:
        return {}
    values = {}
    for prop_name in props:
        try:
            values[prop_name] = get_property(service, path, prop_name)
        except Exception:
            continue
    name = ""
    for candidate in ["Name", "name", "SysName", "sysName", "sys_name"]:
        if candidate in values:
            name = safe_string(values[candidate])
            if name:
                break
    vendor = ""
    for candidate in ["Vendor", "vendor", "VendorId", "vendorId", "vendor_id"]:
        if candidate in values:
            vendor = normalize_hex(values[candidate])
            if vendor:
                break
    product = ""
    for candidate in ["Product", "product", "ProductId", "productId", "product_id"]:
        if candidate in values:
            product = normalize_hex(values[candidate])
            if product:
                break
    enabled = values.get("enabled")
    enabled_property = "enabled" if "enabled" in values else ""
    if not isinstance(enabled, bool):
        enabled = values.get("Enabled")
        if isinstance(enabled, bool):
            enabled_property = "Enabled"
    if not isinstance(enabled, bool):
        enabled = True
    if not enabled_property and "Enabled" in props:
        enabled_property = "Enabled"
    if not enabled_property and "enabled" in props:
        enabled_property = "enabled"
    supports_disable_events = values.get("supportsDisableEvents")
    if not isinstance(supports_disable_events, bool):
        supports_disable_events = values.get("SupportsDisableEvents")
    if not isinstance(supports_disable_events, bool):
        supports_disable_events = False
    return {
        "name": name,
        "vendor": vendor,
        "product": product,
        "enabled": enabled,
        "enabled_property": enabled_property or "enabled",
        "supports_disable_events": supports_disable_events,
        "path": path,
    }


def is_sunshine_device(info):
    name = normalize_name(info.get("name"))
    vendor = normalize_hex(info.get("vendor"))
    product = normalize_hex(info.get("product"))
    vendor_match = vendor == f"{SUNSHINE_VENDOR_ID:04x}"
    product_match = product == f"{SUNSHINE_PRODUCT_ID:04x}"
    name_match = any(normalize_name(marker) in name for marker in NAME_MARKERS)
    return name_match or (vendor_match and product_match)


def kwin_service():
    last_error = ""
    for service in SERVICE_CANDIDATES:
        roots, error = discover_roots(service)
        if roots:
            return service, roots, ""
        if error:
            last_error = error
    return "", [], last_error


def main():
    write_status(state="starting")
    if not is_plasma_session():
        write_status(state="inactive")
        return 0

    service, roots, discovery_error = kwin_service()
    if not service:
        write_status(state="failed", last_error=discovery_error or "KWin InputDevice DBus service not found.")
        return 0

    disabled = {}
    signal.signal(signal.SIGINT, handle_signal)
    signal.signal(signal.SIGTERM, handle_signal)
    signal.signal(signal.SIGHUP, handle_signal)

    try:
        while not STOP:
            seen_count = 0
            current_paths = set()
            failed_devices = []
            try:
                for path in find_device_paths(service, roots):
                    try:
                        info = device_details(service, path)
                    except Exception:
                        continue
                    if not is_sunshine_device(info):
                        continue
                    seen_count += 1
                    current_paths.add(path)
                    if not info.get("enabled", True):
                        if path not in disabled:
                            disabled[path] = {
                                "path": path,
                                "name": safe_string(info.get("name")),
                                "enabled_before": False,
                                "enabled_property": safe_string(info.get("enabled_property")) or "enabled",
                            }
                        continue
                    if not info.get("supports_disable_events", False):
                        failed_devices.append({
                            "path": path,
                            "name": safe_string(info.get("name")),
                            "error": "KWin reports supportsDisableEvents=false",
                        })
                        continue
                    if info.get("enabled", True):
                        try:
                            set_enabled(
                                service,
                                path,
                                False,
                                safe_string(info.get("enabled_property")) or "enabled",
                            )
                            disabled[path] = {
                                "path": path,
                                "name": safe_string(info.get("name")),
                                "enabled_before": True,
                                "enabled_property": safe_string(info.get("enabled_property")) or "enabled",
                            }
                        except Exception as exc:
                            failed_devices.append({
                                "path": path,
                                "name": safe_string(info.get("name")),
                                "error": safe_string(exc),
                            })
                disabled_devices = []
                for path, details in list(disabled.items()):
                    if path not in current_paths:
                        continue
                    disabled_devices.append({"path": path, "name": details["name"]})
                write_status(
                    state="active",
                    service=service,
                    disabled_devices=disabled_devices,
                    failed_devices=failed_devices,
                    seen_device_count=seen_count,
                )
            except Exception as exc:
                write_status(
                    state="failed",
                    service=service,
                    disabled_devices=[{"path": path, "name": details["name"]} for path, details in disabled.items()],
                    failed_devices=failed_devices,
                    seen_device_count=0,
                    last_error=safe_string(exc),
                )
            time.sleep(1.0)
    finally:
        restore_error = ""
        for path, details in list(disabled.items()):
            try:
                set_enabled(
                    service,
                    path,
                    bool(details.get("enabled_before", True)),
                    safe_string(details.get("enabled_property")) or "enabled",
                )
            except Exception as exc:
                restore_error = safe_string(exc)
        write_status(
            state="restored" if not restore_error else "failed",
            service=service,
            disabled_devices=[],
            failed_devices=[],
            seen_device_count=0,
            last_error=restore_error,
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
