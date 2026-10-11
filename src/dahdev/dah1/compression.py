from __future__ import annotations

import struct
from dataclasses import dataclass


@dataclass(frozen=True)
class WrappedBlock:
    data: bytes
    end_offset: int
    compressed_size: int
    decompressed_size: int


def decompress_bizarre(payload: bytes, expected_size: int) -> tuple[bytes, int]:
    """Decode the DAH1 Xbox load-group LZ stream and return bytes consumed."""

    source = memoryview(payload)
    source_offset = 0
    output = bytearray()

    while len(output) < expected_size:
        if source_offset + 4 > len(source):
            raise ValueError("compressed stream ended before a flag word")

        flags = int.from_bytes(source[source_offset : source_offset + 4], "big")
        source_offset += 4
        shift = 14 - (flags & 3)
        mask = (1 << shift) - 1

        for bit in range(31, 1, -1):
            if len(output) == expected_size:
                break

            if flags & (1 << bit):
                if source_offset + 2 > len(source):
                    raise ValueError("compressed stream ended inside a back-reference")
                token = int.from_bytes(source[source_offset : source_offset + 2], "big")
                source_offset += 2
                distance = (token & mask) + 1
                length = (token >> shift) + 3
                if distance > len(output):
                    raise ValueError(
                        f"back-reference distance {distance} exceeds output size {len(output)}"
                    )
                if len(output) + length > expected_size:
                    raise ValueError("back-reference exceeds declared decompressed size")
                for _ in range(length):
                    output.append(output[-distance])
            else:
                if source_offset >= len(source):
                    raise ValueError("compressed stream ended inside a literal")
                output.append(source[source_offset])
                source_offset += 1

    if len(output) != expected_size:
        raise ValueError(
            f"decompressed size mismatch: got {len(output)}, expected {expected_size}"
        )

    return bytes(output), source_offset


def unwrap_block(raw: bytes, offset: int, expected_size: int | None = None) -> WrappedBlock:
    if offset < 0 or offset + 8 > len(raw):
        raise ValueError(f"wrapper offset 0x{offset:X} is outside the package")

    decompressed_size, compressed_size = struct.unpack_from("<II", raw, offset)
    if expected_size is not None and decompressed_size != expected_size:
        raise ValueError(
            f"wrapper declares {decompressed_size} bytes, expected {expected_size}"
        )
    payload_start = offset + 8
    payload_end = payload_start + compressed_size
    if compressed_size == 0 or payload_end > len(raw):
        raise ValueError("wrapped payload extends beyond the package")

    data, consumed = decompress_bizarre(raw[payload_start:payload_end], decompressed_size)
    if consumed > compressed_size:
        raise ValueError("decoder consumed beyond the wrapped payload")
    if any(raw[payload_start + consumed : payload_end]):
        raise ValueError("wrapped payload has non-zero bytes after the compressed stream")

    return WrappedBlock(
        data=data,
        end_offset=payload_end,
        compressed_size=compressed_size,
        decompressed_size=decompressed_size,
    )

