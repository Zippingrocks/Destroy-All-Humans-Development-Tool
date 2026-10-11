from __future__ import annotations

import numpy as np
from numpy.typing import NDArray


Float32Array = NDArray[np.float32]


def quaternion_matrix_xyzw(quaternion: Float32Array) -> Float32Array:
    """Reproduce retail routine 0xD58C0 without normalizing the quaternion."""

    x, y, z, w = (np.float32(value) for value in quaternion)
    f = np.float32
    x2, y2, z2 = f(x * 2), f(y * 2), f(z * 2)
    xx, yy, zz = f(x2 * x), f(y2 * y), f(z2 * z)
    xy, xz, yz = f(y2 * x), f(z2 * x), f(z2 * y)
    wx, wy, wz = f(x2 * w), f(y2 * w), f(z2 * w)

    return np.array(
        [
            [f(1 - f(zz + yy)), f(xy + wz), f(xz - wy), 0],
            [f(xy - wz), f(1 - f(zz + xx)), f(yz + wx), 0],
            [f(xz + wy), f(yz - wx), f(1 - f(yy + xx)), 0],
            [0, 0, 0, 1],
        ],
        dtype=np.float32,
    )


def instance_matrix(
    quaternion: Float32Array, position: Float32Array, scale: np.float32
) -> Float32Array:
    """Build the row-vector instance matrix used by retail routine 0xF2850."""

    matrix = quaternion_matrix_xyzw(quaternion)
    matrix[:3, :3] *= np.float32(scale)
    matrix[3, :3] = np.asarray(position, dtype=np.float32)
    return matrix


def transform_points(points: Float32Array, matrix: Float32Array) -> Float32Array:
    """Match retail CPU point helper 0xD68C0 operation order and float32 rounding."""

    vertices = np.asarray(points, dtype=np.float32)
    transform = np.asarray(matrix, dtype=np.float32)
    output = np.empty_like(vertices)
    output[:, 0] = (
        (vertices[:, 2] * transform[2, 0] + vertices[:, 1] * transform[1, 0])
        + vertices[:, 0] * transform[0, 0]
    ) + transform[3, 0]
    output[:, 1] = (
        (vertices[:, 2] * transform[2, 1] + vertices[:, 0] * transform[0, 1])
        + vertices[:, 1] * transform[1, 1]
    ) + transform[3, 1]
    output[:, 2] = (
        (vertices[:, 2] * transform[2, 2] + vertices[:, 0] * transform[0, 2])
        + vertices[:, 1] * transform[1, 2]
    ) + transform[3, 2]
    return output

