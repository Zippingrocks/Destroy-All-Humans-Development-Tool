from __future__ import annotations

import unittest

from dahdev.dah1.container import ResourceRecord
from dahdev.dah1.materials import NativeMaterial


def _material(flags: int) -> NativeMaterial:
    resource = ResourceRecord(0, 0, 0, 0, 0, 128, 0)
    return NativeMaterial(resource, bytes(128), flags, 0, None)


class NativeMaterialAddressingTests(unittest.TestCase):
    def test_defaults_to_wrap(self) -> None:
        material = _material(0)
        self.assertEqual(material.address_u, "wrap")
        self.assertEqual(material.address_v, "wrap")

    def test_decodes_independent_clamp_bits(self) -> None:
        self.assertEqual(_material(0x1000).address_u, "clamp")
        self.assertEqual(_material(0x1000).address_v, "wrap")
        self.assertEqual(_material(0x2000).address_u, "wrap")
        self.assertEqual(_material(0x2000).address_v, "clamp")
        self.assertEqual(_material(0x3000).address_u, "clamp")
        self.assertEqual(_material(0x3000).address_v, "clamp")


if __name__ == "__main__":
    unittest.main()
