#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Closed-Loop Fast Convolutional Multi-Candidate Optimizer & Polish.
Menentukan skala kartografis, rotasi ortogonal North-Up, dan translasi sub-pixel
menggunakan konvolusi 2D jarak Chamfer pada batas poligon SLS.
"""

from typing import Dict, Any, Tuple, Optional, List
import cv2
import numpy as np
from shapely.geometry.base import BaseGeometry

from config import (
    MAX_CHAMFER_ERROR_PX,
    MIN_OVERLAP_RATIO,
    WGS84_M_PER_DEG_LON,
    WGS84_M_PER_DEG_LAT,
    STANDARD_SCALES
)
from core.viewport import detect_map_viewport, rotate_image, detect_north_arrow_rotation
from core.boundary import extract_boundary_lines, sample_polygon, compute_chamfer_error
from core.hybrid_refiner import extract_structural_edges, compute_structural_correlation


def optimize_georeferencing_configuration(
    img: np.ndarray,
    geom: BaseGeometry,
    mosaic: Optional[np.ndarray] = None,
    mosaic_meta: Optional[Tuple[float, float, float, float, float]] = None,
    stem: str = "",
    target_id: str = "",
    progress_callback=None
) -> Dict[str, Any]:
    """
    Optimasi Georeferensi Presisi Wilkerstat:
    1. Deteksi rotasi sejati agar arah utara selalu menghadap ke atas (North-Up).
    2. Konvolusi 2D Chamfer Distance cepat untuk menyelaraskan poligon SLS GeoJSON dengan garis batas peta.
    3. Fine-tuning sub-pixel skala & translasi untuk mencapai akurasi sub-meter.
    """
    minx, miny, maxx, maxy = geom.bounds
    cx_geo = (minx + maxx) / 2.0
    cy_geo = (miny + maxy) / 2.0
    geo_w_m = (maxx - minx) * WGS84_M_PER_DEG_LON
    geo_h_m = (maxy - miny) * WGS84_M_PER_DEG_LAT

    # Sampel titik kontur poligon SLS
    pts_deg = sample_polygon(geom, step_m=1.0)
    lon_pts = pts_deg[:, 0]
    lat_pts = pts_deg[:, 1]

    # [1] Deteksi Simbol Arah Utara (North Arrow)
    if progress_callback:
        progress_callback("Mendeteksi simbol panah arah utara (North Arrow)...")

    north_rot, north_score = detect_north_arrow_rotation(img)
    if north_score >= 0.55:
        candidate_rotations = [north_rot]
        if progress_callback:
            progress_callback(f"Arah Utara terdeteksi: Rotasi {north_rot}° (skor {north_score:.2f})")
    else:
        # Evaluasi 4 rotasi ortogonal jika simbol utara belum teridentifikasi jelas
        candidate_rotations = [0, 90, 180, 270]

    all_hypotheses: List[Dict[str, Any]] = []

    for rot in candidate_rotations:
        rimg = rotate_image(img, rot)
        h_img, w_img = rimg.shape[:2]
        bx, by, bw, bh = detect_map_viewport(rimg)

        # Ekstraksi garis batas multi-warna (Merah, Oranye, Cyan)
        boundary_mask = extract_boundary_lines(rimg, bx, by, bw, bh)

        # Downsample untuk pencarian konvolusi multi-skala berkecepatan tinggi
        scale_ds = 0.20  # 5x downsampling
        small_mask = cv2.resize(
            boundary_mask, (0, 0),
            fx=scale_ds, fy=scale_ds,
            interpolation=cv2.INTER_NEAREST
        )
        dist_map_ds = cv2.distanceTransform((small_mask == 0).astype(np.uint8), cv2.DIST_L2, 5)

        # Estimasi skala dasar teoretis dari dimensi poligon terhadap viewport
        if rot in [90, 270]:
            s_base = max(geo_h_m / float(bw), geo_w_m / float(bh)) / (0.0254 / 300.0)
        else:
            s_base = max(geo_w_m / float(bw), geo_h_m / float(bh)) / (0.0254 / 300.0)

        # Himpun rentang skala pengujian:
        # BPS Wilkerstat selalu membingkai SLS mengisi 70%-95% viewport (rasio 0.95 - 1.35)
        # atau di-zoom in pada multi-lembar _1, _2 (rasio 0.45 - 0.75)
        cand_scales_set = set()
        for sc in STANDARD_SCALES:
            if sc >= s_base * 0.40 and sc <= s_base * 1.50:
                cand_scales_set.add(float(sc))

        # Tambahkan skala kontinu rapat (50 langkah)
        for sc in np.linspace(s_base * 0.42, s_base * 1.45, 51):
            cand_scales_set.add(float(sc))

        cand_scales = sorted(list(cand_scales_set))

        best_rot_val = 999.0
        best_rot_hyp = None

        # [2] Pencarian Cepat berbasis 2D Convolution Chamfer Filter
        for sc in cand_scales:
            res_m = sc * (0.0254 / 300.0)
            A_sc = (res_m / WGS84_M_PER_DEG_LON) / scale_ds
            E_sc = (-res_m / WGS84_M_PER_DEG_LAT) / scale_ds

            poly_x = ((lon_pts - cx_geo) / A_sc)
            poly_y = ((lat_pts - cy_geo) / E_sc)
            min_px = int(np.floor(np.min(poly_x)))
            max_px = int(np.ceil(np.max(poly_x)))
            min_py = int(np.floor(np.min(poly_y)))
            max_py = int(np.ceil(np.max(poly_y)))

            kernel_w = max_px - min_px + 1
            kernel_h = max_py - min_py + 1
            if (kernel_w <= 2 or kernel_h <= 2 or
                kernel_w >= dist_map_ds.shape[1] or kernel_h >= dist_map_ds.shape[0]):
                continue

            kernel = np.zeros((kernel_h, kernel_w), dtype=np.float32)
            k_xs = np.clip((poly_x - min_px).astype(int), 0, kernel_w - 1)
            k_ys = np.clip((poly_y - min_py).astype(int), 0, kernel_h - 1)
            kernel[k_ys, k_xs] = 1.0
            sum_k = np.sum(kernel)
            if sum_k == 0:
                continue
            kernel /= sum_k

            chamfer_surface = cv2.filter2D(dist_map_ds, cv2.CV_32F, kernel)

            # Batas pencarian pusat di sekitar viewport
            vp_x1 = max(0, int(bx * scale_ds) - min_px)
            vp_x2 = min(chamfer_surface.shape[1], int((bx + bw) * scale_ds) - max_px)
            vp_y1 = max(0, int(by * scale_ds) - min_py)
            vp_y2 = min(chamfer_surface.shape[0], int((by + bh) * scale_ds) - max_py)

            if vp_x2 > vp_x1 and vp_y2 > vp_y1:
                search_region = chamfer_surface[vp_y1:vp_y2, vp_x1:vp_x2]
                min_v, _, min_loc, _ = cv2.minMaxLoc(search_region)
                if min_v < best_rot_val:
                    best_rot_val = min_v
                    center_px = (min_loc[0] + vp_x1) / scale_ds
                    center_py = (min_loc[1] + vp_y1) / scale_ds
                    best_rot_hyp = {
                        "rotation": rot,
                        "rot_img": rimg,
                        "bx": bx, "by": by, "bw": bw, "bh": bh,
                        "scale": sc,
                        "center_px": center_px,
                        "center_py": center_py,
                        "ds_chamfer": min_v,
                        "boundary_mask": boundary_mask
                    }

        if best_rot_hyp is not None:
            all_hypotheses.append(best_rot_hyp)

    if not all_hypotheses:
        # Fallback darurat
        rimg = rotate_image(img, candidate_rotations[0])
        bx, by, bw, bh = detect_map_viewport(rimg)
        res_m = s_base * (0.0254 / 300.0)
        cand_A = res_m / WGS84_M_PER_DEG_LON
        cand_E = - res_m / WGS84_M_PER_DEG_LAT
        return {
            "rotation": candidate_rotations[0],
            "rot_img": rimg,
            "bx": bx, "by": by, "bw": bw, "bh": bh,
            "scale": s_base,
            "A": cand_A, "E": cand_E,
            "C": cx_geo - (bx + bw / 2.0) * cand_A,
            "F": cy_geo - (by + bh / 2.0) * cand_E,
            "chamfer_err": 99.0,
            "containment": 1.0,
            "cas": 0.0,
            "status": "FAIL_NO_FIT"
        }

    all_hypotheses.sort(key=lambda h: h["ds_chamfer"])
    best_hyp = all_hypotheses[0]

    # [3] Hierarchical Sub-Pixel Fine Polish
    if progress_callback:
        progress_callback("Menghaluskan penyelarasan batas SLS ke tingkat sub-pixel...")

    coarse_s = best_hyp["scale"]
    coarse_cx = best_hyp["center_px"]
    coarse_cy = best_hyp["center_py"]
    rimg = best_hyp["rot_img"]
    boundary_mask = best_hyp["boundary_mask"]
    bx, by, bw, bh = best_hyp["bx"], best_hyp["by"], best_hyp["bw"], best_hyp["bh"]

    full_dist_map = cv2.distanceTransform((boundary_mask == 0).astype(np.uint8), cv2.DIST_L2, 5)

    fine_s = coarse_s
    fine_cx = coarse_cx
    fine_cy = coarse_cy
    min_fine_ch = 999.0

    # Tahap 1: Pencarian menengah dengan jangkauan lebar (+-32px, langkah 2px)
    scale_fine_ratios = np.linspace(-0.02, 0.02, 11)
    for ds_rat in scale_fine_ratios:
        s_test = coarse_s * (1.0 + ds_rat)
        res_m_t = s_test * (0.0254 / 300.0)
        A_t = res_m_t / WGS84_M_PER_DEG_LON
        E_t = - res_m_t / WGS84_M_PER_DEG_LAT

        for dx in range(-32, 33, 2):
            for dy in range(-32, 33, 2):
                test_cx = coarse_cx + dx
                test_cy = coarse_cy + dy
                test_C = cx_geo - test_cx * A_t
                test_F = cy_geo - test_cy * E_t

                pxs = ((lon_pts - test_C) / A_t).astype(np.int32)
                pys = ((lat_pts - test_F) / E_t).astype(np.int32)

                in_vp = (pxs >= bx) & (pxs < bx + bw) & (pys >= by) & (pys < by + bh)
                if np.mean(in_vp) >= 0.60:
                    dists = full_dist_map[pys[in_vp], pxs[in_vp]]
                    n_trim = max(1, int(len(dists) * 0.90))
                    d = float(np.mean(np.sort(dists)[:n_trim]))
                    if d < min_fine_ch:
                        min_fine_ch = d
                        fine_s = s_test
                        fine_cx = test_cx
                        fine_cy = test_cy

    # Tahap 2: Sub-pixel ultra polish (0.5 piksel langkah translasi)
    res_m_fin = fine_s * (0.0254 / 300.0)
    final_A = res_m_fin / WGS84_M_PER_DEG_LON
    final_E = - res_m_fin / WGS84_M_PER_DEG_LAT

    for dx in range(-4, 5):
        for dy in range(-4, 5):
            test_cx = fine_cx + dx * 0.5
            test_cy = fine_cy + dy * 0.5
            test_C = cx_geo - test_cx * final_A
            test_F = cy_geo - test_cy * final_E

            pxs = np.clip(((lon_pts - test_C) / final_A).astype(int), 0, rimg.shape[1] - 1)
            pys = np.clip(((lat_pts - test_F) / final_E).astype(int), 0, rimg.shape[0] - 1)

            in_vp = (pxs >= bx) & (pxs < bx + bw) & (pys >= by) & (pys < by + bh)
            if np.mean(in_vp) >= 0.60:
                dists = full_dist_map[pys[in_vp], pxs[in_vp]]
                n_trim = max(1, int(len(dists) * 0.90))
                d = float(np.mean(np.sort(dists)[:n_trim]))
                if d < min_fine_ch:
                    min_fine_ch = d
                    fine_cx = test_cx
                    fine_cy = test_cy

    final_C = cx_geo - fine_cx * final_A
    final_F = cy_geo - fine_cy * final_E

    final_ch, containment = compute_chamfer_error(
        full_dist_map, pts_deg, final_A, final_E, final_C, final_F, bx, by, bw, bh
    )

    # Hitung CAS (Composite Alignment Score)
    ch_score = max(0.0, 1.0 - (final_ch / 5.0))
    cas_score = 0.70 * ch_score + 0.30 * containment

    if final_ch <= MAX_CHAMFER_ERROR_PX and containment >= MIN_OVERLAP_RATIO:
        status_label = "PASS"
    elif final_ch <= 4.0:
        status_label = "REVIEW_CHAMFER"
    else:
        status_label = "FAIL_MISALIGNED"

    return {
        "rotation": best_hyp["rotation"],
        "rot_img": rimg,
        "bx": bx, "by": by, "bw": bw, "bh": bh,
        "scale": fine_s,
        "A": final_A,
        "E": final_E,
        "C": final_C,
        "F": final_F,
        "chamfer_err": final_ch,
        "containment": containment,
        "cas": cas_score,
        "status": status_label
    }
