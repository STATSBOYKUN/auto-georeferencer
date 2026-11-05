from pathlib import Path
from typing import Dict, Any, Optional
import cv2
import numpy as np
import geopandas as gpd
from shapely.geometry.base import BaseGeometry

from config import WGS84_M_PER_DEG_LON, WGS84_M_PER_DEG_LAT


def render_verification_preview(
    opt_result: Dict[str, Any],
    geom: BaseGeometry,
    parent_features: gpd.GeoDataFrame,
    nmsls: str,
    mosaic: Optional[np.ndarray],
    mosaic_meta: Tuple[float, float, float, float, float],
    out_path: Path
) -> None:
    """
    Render citra verifikasi visual beresolusi tinggi dengan overlay semi-transparan
    Google Hybrid (35%), poligon GeoJSON (hijau tebal), detail inset 2.5x, dan badge metrik.
    """
    rot_img = opt_result["rot_img"]
    h, w = rot_img.shape[:2]
    vis = rot_img.copy()

    bx = opt_result["bx"]
    by = opt_result["by"]
    bw = opt_result["bw"]
    bh = opt_result["bh"]
    A = opt_result["A"]
    E = opt_result["E"]
    C = opt_result["C"]
    F = opt_result["F"]
    scale_val = opt_result["scale"]
    chamfer_err = opt_result["chamfer_err"]
    cas_score = opt_result["cas"]
    status_str = opt_result["status"]
    rot_val = opt_result["rotation"]

    # 1. Overlay Google Hybrid Semi-Transparan (35% Opacity) di dalam Viewport
    if mosaic is not None and np.any(mosaic > 0):
        nw_lon, nw_lat, res_lon_mos, res_lat_mos, _ = mosaic_meta
        h_mos, w_mos = mosaic.shape[:2]

        # 4 Titik sudut viewport dalam koordinat geografis
        x_corners = np.array([bx, bx + bw, bx + bw, bx], dtype=np.float32)
        y_corners = np.array([by, by, by + bh, by + bh], dtype=np.float32)

        lon_corners = C + x_corners * A
        lat_corners = F + y_corners * E

        mos_x = (lon_corners - nw_lon) / res_lon_mos
        mos_y = (nw_lat - lat_corners) / res_lat_mos

        src_pts = np.column_stack([mos_x, mos_y]).astype(np.float32)
        dst_pts = np.float32([[0, 0], [bw, 0], [bw, bh], [0, bh]])

        try:
            M_warp = cv2.getPerspectiveTransform(src_pts, dst_pts)
            hyb_warped = cv2.warpPerspective(mosaic, M_warp, (bw, bh), borderMode=cv2.BORDER_CONSTANT)

            # Blend hanya di dalam area aktif viewport
            vp_area = vis[by:by + bh, bx:bx + bw]
            blended = cv2.addWeighted(vp_area, 0.65, hyb_warped, 0.35, 0)
            vis[by:by + bh, bx:bx + bw] = blended
        except Exception:
            pass

    # 2. Gambar Batas SLS Tetangga (Garis Cyan Tipis)
    target_id = opt_result.get("target_id", "")
    for _, r_p in parent_features.iterrows():
        if str(r_p.get("idsubsls", "")) != target_id:
            geom_p = r_p.geometry
            for p in (list(geom_p.geoms) if hasattr(geom_p, "geoms") else [geom_p]):
                coords_p = np.array(p.exterior.coords)
                px_p = (coords_p[:, 0] - C) / A
                py_p = (coords_p[:, 1] - F) / E
                cv2.polylines(
                    vis,
                    [np.int32(np.column_stack([px_p, py_p]))],
                    isClosed=True,
                    color=(240, 240, 30),
                    thickness=2
                )

    # 3. Gambar Batas SLS Target (Garis Hijau Tebal jika PASS, Merah jika FAIL)
    poly_overlay = vis.copy()
    polys = list(geom.geoms) if hasattr(geom, "geoms") else [geom]
    key_corner = None

    is_pass = "PASS" in status_str
    poly_color = (0, 225, 0) if is_pass else (0, 0, 255)

    for p in polys:
        coords = np.array(p.exterior.coords)
        px_vis = (coords[:, 0] - C) / A
        py_vis = (coords[:, 1] - F) / E
        pts_vis = np.int32(np.column_stack([px_vis, py_vis]))
        cv2.polylines(poly_overlay, [pts_vis], isClosed=True, color=poly_color, thickness=4)

        in_mask = (
            (px_vis >= bx + 120) & (px_vis <= bx + bw - 120) &
            (py_vis >= by + 120) & (py_vis <= by + bh - 120)
        )
        if np.any(in_mask) and key_corner is None:
            idx_c = np.where(in_mask)[0][0]
            key_corner = (int(px_vis[idx_c]), int(py_vis[idx_c]))

    cv2.addWeighted(poly_overlay, 0.60, vis, 0.40, 0, vis)

    # 4. Jendela Inset Detail (Zoom 2.5x) pada Sudut Batas Kritis
    if key_corner is not None:
        kx, ky = key_corner
        crop_rad = 120
        if 0 <= kx - crop_rad and kx + crop_rad < w and 0 <= ky - crop_rad and ky + crop_rad < h:
            detail_crop = vis[ky - crop_rad:ky + crop_rad, kx - crop_rad:kx + crop_rad]
            inset_zoom = cv2.resize(detail_crop, (320, 320), interpolation=cv2.INTER_LINEAR)
            cv2.rectangle(inset_zoom, (0, 0), (319, 319), (255, 255, 255), 3)
            cv2.putText(inset_zoom, "INSET ZOOM (2.5x)", (12, 28), cv2.FONT_HERSHEY_SIMPLEX, 0.65, (255, 255, 255), 2)
            cv2.putText(inset_zoom, f"Chamfer: {chamfer_err:.2f}px", (12, 54), cv2.FONT_HERSHEY_SIMPLEX, 0.65, (0, 255, 0) if is_pass else (0, 0, 255), 2)

            inset_y = h - 350
            inset_x = w - 350
            vis[inset_y:inset_y + 320, inset_x:inset_x + 320] = inset_zoom

    # 5. Badge Diagnostik Header
    err_meter = chamfer_err * (scale_val * (0.0254 / 300.0))
    header_text = (
        f"{out_path.stem} | {nmsls} | Rot:{rot_val} deg | "
        f"Skala 1:{int(round(scale_val))} | Err:{chamfer_err:.2f}px ({err_meter:.2f}m) | "
        f"CAS:{cas_score:.2f} | Status:{status_str}"
    )

    # Banner latar belakang semi-transparan untuk header
    banner_h = 75
    banner_y1 = max(0, by + 15)
    banner_y2 = banner_y1 + banner_h
    banner_x1 = bx + 20
    banner_x2 = bx + bw - 20

    sub_banner = vis[banner_y1:banner_y2, banner_x1:banner_x2]
    black_rect = np.zeros_like(sub_banner)
    vis[banner_y1:banner_y2, banner_x1:banner_x2] = cv2.addWeighted(sub_banner, 0.35, black_rect, 0.65, 0)

    badge_color = (50, 255, 50) if is_pass else (50, 50, 255)
    cv2.putText(
        vis,
        header_text,
        (banner_x1 + 18, banner_y1 + 48),
        cv2.FONT_HERSHEY_SIMPLEX,
        1.15,
        badge_color,
        3
    )

    # Resize preview ke 35% agar efisien disimpan dan nyaman ditinjau
    preview_img = cv2.resize(vis, (0, 0), fx=0.35, fy=0.35)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    is_success, buf = cv2.imencode(out_path.suffix, preview_img)
    if is_success:
        buf.tofile(str(out_path))
    else:
        cv2.imwrite(str(out_path), preview_img)
