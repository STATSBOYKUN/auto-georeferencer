import math
import urllib.request
from pathlib import Path
from typing import Tuple, Optional
import cv2
import numpy as np

from config import DEFAULT_CACHE_DIR, WGS84_M_PER_DEG_LON, WGS84_M_PER_DEG_LAT


def deg2num(lat_deg: float, lon_deg: float, zoom: int) -> Tuple[int, int]:
    """Konversi koordinat geodetik (WGS84) ke indeks tile Slippy Map."""
    lat_rad = math.radians(lat_deg)
    n = 2.0 ** zoom
    xtile = int((lon_deg + 180.0) / 360.0 * n)
    ytile = int((1.0 - math.asinh(math.tan(lat_rad)) / math.pi) / 2.0 * n)
    return (xtile, ytile)


def num2deg(xtile: int, ytile: int, zoom: int) -> Tuple[float, float]:
    """Konversi indeks tile Slippy Map ke koordinat pojok barat laut (NW) dalam WGS84."""
    n = 2.0 ** zoom
    lon_deg = xtile / n * 360.0 - 180.0
    lat_rad = math.atan(math.sinh(math.pi * (1.0 - 2.0 * ytile / n)))
    lat_deg = math.degrees(lat_rad)
    return (lat_deg, lon_deg)


def get_hybrid_mosaic(
    min_lon: float,
    min_lat: float,
    max_lon: float,
    max_lat: float,
    cache_name: str,
    cache_dir: Path = DEFAULT_CACHE_DIR,
    zoom: int = 18
) -> Tuple[np.ndarray, float, float, float, float, float, float, float]:
    """
    Unduh dan susun mozaik Google Hybrid (lyrs=y) beresolusi tinggi di sekitar SLS.
    Mengembalikan: (mosaic_img, nw_lon, se_lon, se_lat, nw_lat, res_lon, res_lat, res_m)
    """
    cache_dir.mkdir(parents=True, exist_ok=True)
    cache_path = cache_dir / f"{cache_name}_hyb_z{zoom}.jpg"

    # Tambahkan margin 35% di sekeliling poligon SLS
    dlon = max(max_lon - min_lon, 0.003) * 0.35
    dlat = max(max_lat - min_lat, 0.003) * 0.35
    m_min_lon = min_lon - dlon
    m_max_lon = max_lon + dlon
    m_min_lat = min_lat - dlat
    m_max_lat = max_lat + dlat

    x1, y2_tile = deg2num(m_min_lat, m_min_lon, zoom)
    x2, y1_tile = deg2num(m_max_lat, m_max_lon, zoom)

    tw = x2 - x1 + 1
    th = y2_tile - y1_tile + 1

    nw_lat, nw_lon = num2deg(x1, y1_tile, zoom)
    se_lat, se_lon = num2deg(x2 + 1, y2_tile + 1, zoom)

    res_lon = (se_lon - nw_lon) / float(tw * 256)
    res_lat = (nw_lat - se_lat) / float(th * 256)
    res_m = ((se_lon - nw_lon) * WGS84_M_PER_DEG_LON) / float(tw * 256)

    if cache_path.exists():
        mosaic = cv2.imread(str(cache_path))
        if mosaic is not None and mosaic.shape[0] == th * 256 and mosaic.shape[1] == tw * 256:
            return mosaic, nw_lon, se_lon, se_lat, nw_lat, res_lon, res_lat, res_m

    mosaic = np.zeros((th * 256, tw * 256, 3), dtype=np.uint8)
    headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}

    for ty in range(y1_tile, y2_tile + 1):
        for tx in range(x1, x2 + 1):
            t_cache = cache_dir / f"tile_hyb_{zoom}_{tx}_{ty}.jpg"
            if not t_cache.exists():
                url = f"https://mt1.google.com/vt/lyrs=y&x={tx}&y={ty}&z={zoom}"
                try:
                    req = urllib.request.Request(url, headers=headers)
                    with urllib.request.urlopen(req, timeout=8) as resp:
                        t_cache.write_bytes(resp.read())
                except Exception:
                    pass

            if t_cache.exists():
                t_img = cv2.imread(str(t_cache))
                if t_img is not None:
                    iy = (ty - y1_tile) * 256
                    ix = (tx - x1) * 256
                    mosaic[iy:iy + 256, ix:ix + 256] = t_img

    if np.any(mosaic > 0):
        cv2.imwrite(str(cache_path), mosaic)

    return mosaic, nw_lon, se_lon, se_lat, nw_lat, res_lon, res_lat, res_m


def extract_structural_edges(img: np.ndarray) -> np.ndarray:
    """Ekstraksi tepi struktural jalan dan batas fisik dengan filter bilateral & Canny."""
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY) if len(img.shape) == 3 else img
    filtered = cv2.bilateralFilter(gray, 5, 50, 50)
    edges = cv2.Canny(filtered, 40, 120)
    return edges


def compute_structural_correlation(
    vp_edges: np.ndarray,
    mosaic_edges: np.ndarray,
    A: float,
    E: float,
    C: float,
    F: float,
    bx: int,
    by: int,
    bw: int,
    bh: int,
    nw_lon: float,
    nw_lat: float,
    res_lon_mos: float,
    res_lat_mos: float
) -> float:
    """
    Hitung korelasi struktural (kepadatan tepi berimpit) antara scan peta dan Google Hybrid.
    Mengembalikan skor normalisasi [0.0, 1.0].
    """
    h_mos, w_mos = mosaic_edges.shape[:2]

    # Proyeksikan 4 sudut viewport scan ke piksel Google Hybrid
    x_coords = np.array([bx, bx + bw, bx + bw, bx], dtype=np.float32)
    y_coords = np.array([by, by, by + bh, by + bh], dtype=np.float32)

    lon_pts = C + x_coords * A
    lat_pts = F + y_coords * E

    mos_x = (lon_pts - nw_lon) / res_lon_mos
    mos_y = (nw_lat - lat_pts) / res_lat_mos

    src_quad = np.column_stack([x_coords - bx, y_coords - by]).astype(np.float32)
    dst_quad = np.column_stack([mos_x, mos_y]).astype(np.float32)

    target_w = 400
    target_h = int(round(400 * (bh / float(bw))))

    try:
        M_warp = cv2.getPerspectiveTransform(src_quad, np.float32([[0, 0], [target_w, 0], [target_w, target_h], [0, target_h]]))
        vp_warp = cv2.warpPerspective(vp_edges, M_warp, (target_w, target_h))

        M_mos_warp = cv2.getPerspectiveTransform(dst_quad, np.float32([[0, 0], [target_w, 0], [target_w, target_h], [0, target_h]]))
        mos_warp = cv2.warpPerspective(mosaic_edges, M_mos_warp, (target_w, target_h))

        kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (3, 3))
        mos_dil = cv2.dilate(mos_warp, kernel)

        overlap = np.sum((vp_warp > 0) & (mos_dil > 0))
        total_vp_edges = np.sum(vp_warp > 0)

        if total_vp_edges == 0:
            return 0.0

        score = min(1.0, float(overlap) / float(max(100, total_vp_edges * 0.35)))
        return score
    except Exception:
        return 0.0

