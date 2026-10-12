from __future__ import annotations

import struct
import unittest

from dahdev.dah1.executable_evidence import XbeImage


def _synthetic_xbe() -> bytes:
    data = bytearray(0x420)
    data[:4] = b"XBEH"
    base = 0x10000
    struct.pack_into("<I", data, 0x104, base)
    struct.pack_into("<I", data, 0x11C, 1)
    struct.pack_into("<I", data, 0x120, base + 0x200)
    struct.pack_into("<4I", data, 0x204, 0x11000, 0x20, 0x400, 0x20)
    struct.pack_into("<I", data, 0x214, base + 0x300)
    data[0x300:0x306] = b".text\0"
    data[0x400:0x420] = bytes(range(0x20))
    return bytes(data)


class XbeImageTests(unittest.TestCase):
    def test_maps_virtual_addresses_through_section_table(self) -> None:
        image = XbeImage(_synthetic_xbe())
        self.assertEqual(image.base_address, 0x10000)
        self.assertEqual(image.sections[0].name, ".text")
        self.assertEqual(image.read_virtual(0x11008, 4), bytes((8, 9, 10, 11)))

    def test_rejects_unmapped_virtual_ranges(self) -> None:
        image = XbeImage(_synthetic_xbe())
        with self.assertRaisesRegex(ValueError, "unmapped"):
            image.read_virtual(0x12000, 4)

    def test_rejects_non_xbe_data(self) -> None:
        with self.assertRaisesRegex(ValueError, "not an XBE"):
            XbeImage(bytes(0x200))


if __name__ == "__main__":
    unittest.main()
