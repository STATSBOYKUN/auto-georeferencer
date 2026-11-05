#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Ekstraksi garis batas SLS multi-warna (Merah, Oranye, Cyan/Biru)
dan pengambilan sampel titik koordinat poligon GeoJSON.
"""

from typing import Tuple, List, Optional
import cv2
import numpy as np
from shapely.geometry.base import BaseGeometry
from config import WGS84_M_PER_DEG_LON, WGS84_M_PER_DEG_LAT


def extract_boundary_lines(
    img: np.ndarray,
    bx: int,
    by: int,
    bw: int,
    bh: int
) -> np.ndarray:
    """
    Ekstraksi garis batas SLS lengkap pada peta scan Wilkerstat BPS.
    Mencakup 3 kelas warna kartografis standar Wilkerstat:
    1. Merah / Magenta (Batas SLS utama): H < 15 atau H > 160
    2. Jingga / Oranye (Batas turunan / sub-SLS): 15 <= H <= 28
    3. Cyan / Biru Muda (Batas perairan / batas SLS tetangga): 95 <= H <= 125
    """
    h, w = img.shape[:2]

    # Pisahkan kanal RGB dan konversi ke int untuk menghindari underflow
    b_ch, g_ch, r_ch = cv2.split(img)
    r_i = r_ch.astype(int)
    g_i = g_ch.astype(int)
    b_i = b_ch.astype(int)

    hsv = cv2.cvtColor(img, cv2.COLOR_BGR2HSV)
    h_ch = hsv[:, :, 0]
    s_ch = hsv[:, :, 1]
    v_ch = hsv[:, :, 2]

    # 1. Merah / Magenta
    is_red = (
        ((h_ch < 15) | (h_ch > 160)) &
        (s_ch > 38) & (v_ch > 45) &
        (r_i - b_i > 15) & (r_i - g_i > 10)
    )

    # 2. Oranye / Jingga
    is_orange = (
        (h_ch >= 15) & (h_ch <= 28) &
        (s_ch > 45) & (v_ch > 65) &
        (r_i - b_i > 20) & (r_i - g_i > 6)
    )

    # 3. Cyan / Biru Muda
    is_cyan = (
        (h_ch >= 95) & (h_ch <= 125) &
        (s_ch > 35) & (v_ch > 65) &
        (b_i - r_i > 15)
    )

    combined = (is_red | is_orange | is_cyan).astype(np.uint8) * 255

    # Batasi di dalam viewport dengan margin aman
    mask = np.zeros((h, w), dtype=np.uint8)
    y1 = max(0, by + 10)
    y2 = min(h, by + bh - 10)
    x1 = max(0, bx + 10)
    x2 = min(w, bx + bw - 10)

    if y2 > y1 and x2 > x1:
        mask[y1:y2, x1:x2] = combined[y1:y2, x1:x2]

    # Penutupan celah garis putus-putus (morfologi close 3x3)
    kernel_close = cv2.getStructuringElement(cv2.MORPH_RECT, (3, 3))
    mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, kernel_close)

    return mask


def sample_polygon(geom: BaseGeometry, step_m: float = 1.0) -> np.ndarray:
    """
    Diskretisasi garis batas poligon/multipoligon GeoJSON menjadi sampel titik (lon, lat)
    dengan interval jarak fisik step_m (meter).
    """
    polys = list(geom.geoms) if hasattr(geom, 'geoms') else [geom]
    pts = []

    for p in polys:
        coords = np.array(p.exterior.coords)
        for i in range(len(coords) - 1):
            p1, p2 = coords[i], coords[i + 1]
            dist_m = np.hypot(
                (p2[0] - p1[0]) * WGS84_M_PER_DEG_LON,
                (p2[1] - p1[1]) * WGS84_M_PER_DEG_LAT
            )
            n_steps = max(1, int(round(dist_m / step_m)))
            for t in np.linspace(0, 1, n_steps, endpoint=False):
                pts.append(p1 + t * (p2 - p1))

    return np.array(pts, dtype=np.float64)


def compute_chamfer_error(
    dist_map: np.ndarray,
    pts_deg: np.ndarray,
    A: float,
    E: float,
    C: float,
    F: float,
    bx: int,
    by: int,
    bw: int,
    bh: int
) -> Tuple[float, float]:
    """
    Hitung nilai rata-rata jarak Chamfer (piksel) dan rasio containment poligon di dalam viewport.
    Mengembalikan (mean_distance_px, containment_ratio).
    """
    if len(pts_deg) == 0:
        return 999.0, 0.0

    lon_pts = pts_deg[:, 0]
    lat_pts = pts_deg[:, 1]

    pxs = ((lon_pts - C) / A).astype(np.int32)
    pys = ((lat_pts - F) / E).astype(np.int32)

    h_img, w_img = dist_map.shape[:2]
    in_img = (pxs >= 0) & (pxs < w_img) & (pys >= 0) & (pys < h_img)
    in_vp = (pxs >= bx) & (pxs < bx + bw) & (pys >= by) & (pys < by + bh)

    containment = float(np.mean(in_vp)) if len(pxs) > 0 else 0.0

    if np.sum(in_img) == 0:
        return 999.0, 0.0

    valid_dists = dist_map[pys[in_img], pxs[in_img]]
    # 90% Trimmed Mean: mengabaikan 10% titik outlier yang tertutup teks nama jalan/label bangunan
    sorted_dists = np.sort(valid_dists)
    n_trimmed = max(1, int(len(sorted_dists) * 0.90))
    mean_dist = float(np.mean(sorted_dists[:n_trimmed]))

    return mean_dist, containment
