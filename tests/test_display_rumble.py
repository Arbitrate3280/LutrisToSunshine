import unittest

from display import rumble


class DisplayRumbleTests(unittest.TestCase):
    def test_selection_id_from_phys_uses_bridge_prefix(self) -> None:
        self.assertEqual(
            rumble._selection_id_from_phys("lts-inputbridge/controller-1"),
            "controller-1",
        )
        self.assertEqual(rumble._selection_id_from_phys("usb-1/input0"), "")

    def test_build_ds4_bt_rumble_report_has_expected_shape(self) -> None:
        payload = rumble.build_ds4_bt_rumble_report(0xC000, 0x8000)

        self.assertEqual(len(payload), 78)
        self.assertEqual(payload[0], 0x11)
        self.assertEqual(payload[1], 0xC0)
        self.assertEqual(payload[2], 0x20)
        self.assertEqual(payload[3], 0x07)
        self.assertEqual(payload[5], 0x80)
        self.assertEqual(payload[6], 0xC0)
        self.assertNotEqual(payload[-4:], b"\x00\x00\x00\x00")


if __name__ == "__main__":
    unittest.main()
