from __future__ import annotations

import hashlib
import struct
from dataclasses import dataclass
from pathlib import Path


DAH1_RETAIL_XBOX_US_SHA256 = (
    "b9491b30eaee82af6c805d9b0036ec1119059b0660a0923803ebdb6219fa02eb"
)


@dataclass(frozen=True)
class XbeSection:
    name: str
    virtual_address: int
    virtual_size: int
    raw_offset: int
    raw_size: int


class XbeImage:
    def __init__(self, data: bytes):
        if len(data) < 0x124 or data[:4] != b"XBEH":
            raise ValueError("input is not an XBE image")
        self.data = data
        self.base_address = struct.unpack_from("<I", data, 0x104)[0]
        section_count = struct.unpack_from("<I", data, 0x11C)[0]
        section_headers_va = struct.unpack_from("<I", data, 0x120)[0]
        section_headers = section_headers_va - self.base_address
        if section_count > 256 or section_headers < 0:
            raise ValueError("XBE section table is invalid")

        sections: list[XbeSection] = []
        for index in range(section_count):
            offset = section_headers + index * 56
            if offset + 56 > len(data):
                raise ValueError("XBE section table is truncated")
            virtual_address, virtual_size, raw_offset, raw_size = struct.unpack_from(
                "<4I", data, offset + 4
            )
            name_va = struct.unpack_from("<I", data, offset + 0x14)[0]
            name_offset = name_va - self.base_address
            if not 0 <= name_offset < len(data):
                raise ValueError("XBE section name is outside the image")
            name_end = data.find(b"\0", name_offset)
            if name_end < 0:
                raise ValueError("XBE section name is unterminated")
            if raw_offset + raw_size > len(data):
                raise ValueError("XBE section payload is truncated")
            sections.append(
                XbeSection(
                    name=data[name_offset:name_end].decode("ascii", "replace"),
                    virtual_address=virtual_address,
                    virtual_size=virtual_size,
                    raw_offset=raw_offset,
                    raw_size=raw_size,
                )
            )
        self.sections = tuple(sections)

    def read_virtual(self, address: int, length: int) -> bytes:
        if length < 0:
            raise ValueError("virtual read length cannot be negative")
        for section in self.sections:
            mapped_size = min(section.virtual_size, section.raw_size)
            if (
                section.virtual_address <= address
                and address + length <= section.virtual_address + mapped_size
            ):
                offset = section.raw_offset + address - section.virtual_address
                return self.data[offset : offset + length]
        raise ValueError(f"virtual address range 0x{address:X}+0x{length:X} is unmapped")


_EVIDENCE_BYTES = {
    "terrain_stream_stride_24": (
        0xECB35,
        bytes.fromhex(
            "8b47388b48048b156c1d2500894a288b4f38a16c1d25008b11895020"
            "66c74014180089582489582c"
        ),
    ),
    "material_address_u_v_flags": (
        0xE5C40,
        bytes.fromhex(
            "895424088b467cf6c410bd01000000b90d00000074248b15c8841e00"
            "b8030000000bd1a3d0841e00a3d0851e008915c8841e00a350861e00"
            "eb1d090dc8841e00892dd0841e00892dd0851e00892d50861e00b803"
            "0000008b567cf6c620741f8b15c8841e000bd1a3d4841e00a3d4851e"
            "008915c8841e00a354861e00"
        ),
    ),
    "terrain_texture_scale_callback": (
        0xE3C40,
        bytes.fromhex(
            "83ec0c566a028bf1e8c3580500d83d285c22006a018bced95c2408e8"
            "b0580500d83d285c22008b4424048b15600e25008d4c24088944240c"
            "51d95c240c8b8af0000000e83816000033c05e83c40cc3"
        ),
    ),
    "material_texture_transform": (
        0xE5A40,
        bytes.fromhex(
            "f7417c000080000f57c074268b4424048b10895120f30f114124f30f"
            "11412cf30f1141308b4004894134f30f11413cc20400f30f100d285c"
            "2200f30f114920f30f114124f30f11412cf30f114130f30f114934f3"
            "0f11413cc20400"
        ),
    ),
    "float2_affine_texture_transform": (
        0xE5B00,
        bytes.fromhex(
            "8b442408f30f104124f30f594004f30f104920f30f59088b542404f3"
            "0f58c1f30f58412cf30f1102f30f104134f30f594004f30f104930f3"
            "0f5908f30f58c1f30f58413cf30f114204c20800"
        ),
    ),
    "transformed_pair_written_at_vertex_offset_0x0c": (
        0x936D5,
        bytes.fromhex(
            "8d5548528d45c850e81e2405008b4dc88b55cc894d4889554c8b4d74"
            "8b492485c98b4550894540c745440000803f74198d5540528d452050e8"
            "ee2305008b4d208b5524894d408955448d45a0508d55948d4dacc745a0"
            "00000000c745a400000000c745a80000803fe82e1904008d4d94518d55"
            "ac8d4d84e81f1904008b55348b45388b4d3c8955008b55748945048b42"
            "10894d088b4808518d55848d4d00e8981304008b55008b45048b4d0889"
            "168b55488946048b454c894e0889560c8946108b4d64894e14"
        ),
    ),
}


def verify_executable_evidence(path: str | Path) -> dict[str, object]:
    executable = Path(path)
    data = executable.read_bytes()
    digest = hashlib.sha256(data).hexdigest()
    if digest != DAH1_RETAIL_XBOX_US_SHA256:
        raise ValueError(
            "executable is not the pinned DAH1 US Xbox retail build: "
            f"expected {DAH1_RETAIL_XBOX_US_SHA256}, got {digest}"
        )
    image = XbeImage(data)
    checks = []
    for name, (address, expected) in _EVIDENCE_BYTES.items():
        actual = image.read_virtual(address, len(expected))
        if actual != expected:
            raise ValueError(f"executable evidence mismatch at 0x{address:X} ({name})")
        checks.append(
            {
                "name": name,
                "virtual_address": f"0x{address:X}",
                "byte_length": len(expected),
                "sha256": hashlib.sha256(expected).hexdigest(),
            }
        )

    return {
        "status": "verified",
        "build": "Destroy All Humans! (USA), Xbox retail",
        "executable": str(executable.resolve()),
        "sha256": digest,
        "checks": checks,
        "terrain_vertex_layout": {
            "stride": 24,
            "uv_offset": 12,
            "uv_format": "float32x2-little-endian",
            "status": "verified",
            "evidence": (
                "The native terrain draw path writes stream-0 stride 0x18; the retail "
                "terrain texture-scale path applies a float2 affine transform, and its "
                "terrain vertex construction stores that pair at byte +0x0C."
            ),
        },
        "terrain_material_addressing": {
            "flags_offset": "0x7C",
            "u_clamp_mask": "0x00001000",
            "v_clamp_mask": "0x00002000",
            "unset_mode": "wrap",
            "set_mode": "clamp",
            "native_values": {"wrap": 1, "clamp": 3},
            "texture_stages": [0, 2, 3],
            "status": "verified",
        },
        "remaining_unknowns": [
            "The packed four-byte vertex attribute at +0x14 is preserved without semantics.",
            "Material flags other than 0x1000 and 0x2000 remain preserved without semantics.",
            "A same-location retail capture is still required to validate viewport orientation and all render states visually.",
        ],
    }
