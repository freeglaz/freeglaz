"""Synthetic, public test data for the measurement QC (no private measurements).

A smooth analytic "printer": each ink (cyan = 1-R, magenta = 1-G, yellow = 1-B)
attenuates X, Y, Z multiplicatively with a dot-gain-like exponent, above a
black floor. Primaries land in a realistic inkjet range (C*max ≈ 100, black
L* ≈ 26), so a clean chart must raise no alarm."""
from __future__ import annotations

import numpy as np

from lib.z9_client.colorimetry import ARGYLL_D50_WHITE, xyz_to_lab

WHITE = np.array([86.0, 89.0, 74.0])
FLOOR = np.array([1.0, 1.05, 0.9])
TRANSMITTANCE = np.array([[0.15, 0.30, 0.70],    # cyan    on X, Y, Z
                          [0.37, 0.17, 0.40],    # magenta
                          [0.92, 0.80, 0.06]])   # yellow


def printer_xyz(rgb01) -> np.ndarray:
    ink = (1.0 - np.atleast_2d(np.asarray(rgb01, float))) ** 1.25
    return FLOOR + (WHITE - FLOOR) * np.prod(TRANSMITTANCE[None, :, :] ** ink[:, :, None], axis=1)


def lab_to_xyz(lab) -> np.ndarray:
    """Inverse of colorimetry.xyz_to_lab (tests only, Argyll D50 white)."""
    lab = np.atleast_2d(np.asarray(lab, float))
    fy = (lab[:, 0] + 16) / 116
    f = np.stack([fy + lab[:, 1] / 500, fy, fy - lab[:, 2] / 200], axis=1)
    t = np.where(f > 0.20689655, f ** 3, (f - 16.0 / 116.0) / 7.787)
    return t * np.asarray(ARGYLL_D50_WHITE)


def grid(levels: int) -> np.ndarray:
    v = np.linspace(0, 1, levels)
    return np.array(np.meshgrid(v, v, v, indexing="ij")).reshape(3, -1).T


def irregular(n: int, seed: int = 0) -> np.ndarray:
    """8 cube corners + n-8 uniformly random device values."""
    return np.vstack([grid(2), np.random.default_rng(seed).random((n - 8, 3))])


def measure(rgb01, noise: float = 0.002, seed: int = 1) -> np.ndarray:
    xyz = printer_xyz(rgb01)
    return xyz * (1 + np.random.default_rng(seed).normal(scale=noise, size=xyz.shape))


def make_ti3(rgb01, xyz, ids=None, lab_columns=None, originator=None) -> str:
    """CTI3 text with SAMPLE_ID RGB (0-100) XYZ (0-100) [+ LAB_* columns]."""
    rgb01 = np.asarray(rgb01, float)
    ids = ids or [str(i + 1) for i in range(len(rgb01))]
    fmt = "SAMPLE_ID RGB_R RGB_G RGB_B XYZ_X XYZ_Y XYZ_Z" + (" LAB_L LAB_A LAB_B" if lab_columns is not None else "")
    lines = []
    for i, (s, r, x) in enumerate(zip(ids, rgb01, xyz)):
        line = f"{s} {r[0] * 100:.4f} {r[1] * 100:.4f} {r[2] * 100:.4f} {x[0]:.5f} {x[1]:.5f} {x[2]:.5f}"
        if lab_columns is not None:
            line += " " + " ".join(f"{v:.4f}" for v in lab_columns[i])
        lines.append(line)
    head = "CTI3\n"
    if originator:
        head += f'ORIGINATOR "{originator}"\n'
    return (head + 'NORMALIZED_TO_Y_100 "YES"\n'
            f"NUMBER_OF_FIELDS {len(fmt.split())}\nBEGIN_DATA_FORMAT\n{fmt}\nEND_DATA_FORMAT\n"
            f"NUMBER_OF_SETS {len(rgb01)}\nBEGIN_DATA\n" + "\n".join(lines) + "\nEND_DATA\n")


def nearest(rgb01, target) -> int:
    return int(np.argmin(np.linalg.norm(np.asarray(rgb01) - np.asarray(target, float), axis=1)))


def paper_lab(xyz) -> np.ndarray:
    """Lab of the lightest reading (a 'missed patch' reads the paper)."""
    lab = xyz_to_lab(xyz)
    return lab[int(np.argmax(lab[:, 0]))]
