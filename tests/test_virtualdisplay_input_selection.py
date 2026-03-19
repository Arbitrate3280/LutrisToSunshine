import unittest

from virtualdisplay import manager


class VirtualDisplayInputSelectionTests(unittest.TestCase):
    def test_selection_id_is_stable(self) -> None:
        fingerprint = {
            "by_id": "/dev/input/by-id/bluetooth-Sony_Controller-event-joystick",
            "uniq": "aa:bb:cc:dd",
            "phys": "usb-0000:00:00.0-1/input0",
            "vendor_id": "054c",
            "product_id": "09cc",
            "name": "Wireless Controller",
        }
        self.assertEqual(
            manager._selection_id_from_fingerprint(fingerprint),
            manager._selection_id_from_fingerprint(dict(fingerprint)),
        )

    def test_normalized_selection_entry_backfills_missing_fields(self) -> None:
        normalized = manager._normalized_selection_entry(
            {
                "label": "",
                "fingerprint": {
                    "vendor_id": "054C",
                    "product_id": "09CC",
                    "name": "Wireless Controller",
                },
            }
        )

        self.assertEqual(normalized["label"], "Wireless Controller")
        self.assertEqual(normalized["fingerprint"]["vendor_id"], "054c")
        self.assertEqual(normalized["fingerprint"]["product_id"], "09cc")
        self.assertTrue(normalized["selection_id"])

    def test_selection_matches_by_id_first(self) -> None:
        selection = manager._normalized_selection_entry(
            {
                "label": "DualShock",
                "fingerprint": {
                    "by_id": "/dev/input/by-id/bluetooth-Sony_Controller-event-joystick",
                    "uniq": "aa:bb:cc:dd",
                    "phys": "ignored",
                    "vendor_id": "054c",
                    "product_id": "09cc",
                    "name": "Wireless Controller",
                },
            }
        )
        device = {
            "fingerprint": {
                "by_id": "/dev/input/by-id/bluetooth-Sony_Controller-event-joystick",
                "uniq": "different",
                "phys": "different",
                "vendor_id": "054c",
                "product_id": "09cc",
                "name": "Wireless Controller",
            }
        }

        self.assertTrue(manager._selection_matches_device(selection, device))

    def test_parse_selection_numbers_supports_ranges(self) -> None:
        parsed = manager._parse_selection_numbers("1,3-4", 4)
        self.assertEqual(parsed, [0, 2, 3])

    def test_bridge_phys_prefix_detection(self) -> None:
        self.assertTrue(manager._is_bridge_input_phys("lts-inputbridge/controller-1"))
        self.assertFalse(manager._is_bridge_input_phys("usb-0000:02:00.0-6/input0"))

    def test_udev_rule_does_not_match_bridge_phys_prefix(self) -> None:
        rule = manager._udev_rule()
        self.assertNotIn('ATTRS{phys}=="lts-inputbridge/*"', rule)

    def test_udev_rule_grants_uhid_access(self) -> None:
        rule = manager._udev_rule()
        self.assertIn('KERNEL=="uhid"', rule)
        self.assertIn('SUBSYSTEM=="misc"', rule)

    def test_udev_rule_grants_current_user_access_to_sunshine_inputs(self) -> None:
        original_user = manager.current_user_name
        original_group = manager.current_user_group
        try:
            manager.current_user_name = lambda: "alice"
            manager.current_user_group = lambda: "streaming"
            rule = manager._udev_rule()
        finally:
            manager.current_user_name = original_user
            manager.current_user_group = original_group
        self.assertIn('OWNER="alice"', rule)
        self.assertIn('GROUP="streaming"', rule)
        self.assertIn('MODE="0660"', rule)

    def test_udev_rule_preserves_input_classification(self) -> None:
        rule = manager._udev_rule()
        self.assertNotIn('ENV{ID_INPUT}=""', rule)
        self.assertNotIn('ENV{ID_INPUT_KEYBOARD}=""', rule)
        self.assertNotIn('ENV{ID_INPUT_MOUSE}=""', rule)
        self.assertNotIn('ENV{ID_INPUT_TOUCHPAD}=""', rule)

    def test_ensure_dependencies_requires_setfacl(self) -> None:
        original = manager.shutil.which
        try:
            manager.shutil.which = lambda name: None if name == "setfacl" else "/usr/bin/fake"
            missing = manager._ensure_dependencies()
        finally:
            manager.shutil.which = original
        self.assertIn("setfacl", missing)

    def test_udev_rule_no_longer_grants_bridged_hidraw_access_by_phys(self) -> None:
        rule = manager._udev_rule()
        self.assertNotIn('KERNEL=="hidraw*"', rule)
        self.assertNotIn('SUBSYSTEM=="hidraw"', rule)


if __name__ == "__main__":
    unittest.main()
