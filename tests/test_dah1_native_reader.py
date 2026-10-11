from __future__ import annotations

import struct
import unittest

import numpy as np

from dahdev.dah1.compression import decompress_bizarre
from dahdev.dah1.container import RELOCATION_MAGIC, parse_relocation_table
from dahdev.dah1.materials import (
    bc3_mip_chain_size,
    decode_dxt1_top_level,
    decode_dxt5_top_level,
    dxt1_mip_chain_size,
)
from dahdev.dah1.terrain import _strip_triangles
from dahdev.dah1.transforms import instance_matrix, transform_points


class CompressionTests(unittest.TestCase):
    def test_literal_commands(self) -> None:
        decoded, consumed = decompress_bizarre(b"\x00\x00\x00\x00ABC", 3)
        self.assertEqual(decoded, b"ABC")
        self.assertEqual(consumed, 7)

    def test_back_reference(self) -> None:
        # Three literals followed by a length-3, distance-3 reference.
        payload = b"\x10\x00\x00\x00ABC\x00\x02"
        decoded, consumed = decompress_bizarre(payload, 6)
        self.assertEqual(decoded, b"ABCABC")
        self.assertEqual(consumed, len(payload))

    def test_rejects_back_reference_past_output(self) -> None:
        payload = b"\x80\x00\x00\x00\x00\x00"
        with self.assertRaisesRegex(ValueError, "distance"):
            decompress_bizarre(payload, 3)


class RelocationTests(unittest.TestCase):
    def test_parse_relocation_table(self) -> None:
        header = struct.pack("<II", RELOCATION_MAGIC, 2) + bytes(24)
        table = header + struct.pack("<IIII", 4, 16, 8, 20)
        parsed = parse_relocation_table(table, 32)
        self.assertEqual(parsed.pointers, {4: 16, 8: 20})

    def test_rejects_non_aligned_source(self) -> None:
        header = struct.pack("<II", RELOCATION_MAGIC, 1) + bytes(24)
        with self.assertRaisesRegex(ValueError, "source"):
            parse_relocation_table(header + struct.pack("<II", 2, 8), 16)


class TransformTests(unittest.TestCase):
    def test_identity_translation(self) -> None:
        quaternion = np.array([0, 0, 0, 1], dtype=np.float32)
        position = np.array([10, 20, 30], dtype=np.float32)
        matrix = instance_matrix(quaternion, position, np.float32(1))
        points = np.array([[1, 2, 3], [-1, -2, -3]], dtype=np.float32)
        transformed = transform_points(points, matrix)
        np.testing.assert_array_equal(
            transformed,
            np.array([[11, 22, 33], [9, 18, 27]], dtype=np.float32),
        )


class TextureTests(unittest.TestCase):
    def test_verified_dxt1_mip_sizes(self) -> None:
        self.assertEqual(dxt1_mip_chain_size(256, 256, 7), 43688)
        self.assertEqual(dxt1_mip_chain_size(128, 128, 6), 10920)
        self.assertEqual(bc3_mip_chain_size(256, 64, 5), 21824)
        self.assertEqual(bc3_mip_chain_size(128, 128, 6), 21840)

    def test_decode_single_dxt1_block(self) -> None:
        block = struct.pack("<HHI", 0xF800, 0x07E0, 0)
        image = decode_dxt1_top_level(block, 4, 4)
        self.assertEqual(image.size, (4, 4))
        self.assertEqual(image.getpixel((0, 0)), (255, 0, 0, 255))

    def test_decode_single_dxt5_block(self) -> None:
        alpha = bytes((255, 0)) + bytes(6)
        color = struct.pack("<HHI", 0xF800, 0x07E0, 0)
        image = decode_dxt5_top_level(alpha + color, 4, 4)
        self.assertEqual(image.size, (4, 4))
        self.assertEqual(image.getpixel((0, 0)), (255, 0, 0, 255))

    def test_triangle_strip_winding_and_degenerates(self) -> None:
        strip = np.array([0, 1, 2, 2, 3, 4, 5], dtype=np.uint16)
        faces = _strip_triangles(strip)
        np.testing.assert_array_equal(
            faces,
            np.array([[0, 1, 2], [3, 2, 4], [3, 4, 5]], dtype=np.int32),
        )


if __name__ == "__main__":
    unittest.main()

