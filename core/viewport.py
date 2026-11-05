from pathlib import Path
from typing import Tuple, Optional
import cv2
import numpy as np

# Path template arah utara default
TEMPLATE_NORTH_ARROW_PATH = Path(__file__).resolve().parent / "template_north_arrow.png"


def detect_north_arrow_rotation(
    img: np.ndarray,
    template_path: Optional[Path] = None,
    min_confidence: float = 0.55
) -> Tuple[int, float]:
    """
    Deteksi orientasi sejati peta berdasarkan simbol panah arah utara ('U' / North Arrow).
    Menguji 4 rotasi ortogonal (0, 90, 180, 270 derajat) dengan template matching multi-skala.
    Mengembalikan (best_rotation_deg, best_score).
    Rotasi yang dihasilkan adalah sudut putar yang HARUS diterapkan pada citra agar
    simbol panah utara selalu mengarah tegak lurus ke atas (North-Up sejati).
    """
    t_path = template_path or TEMPLATE_NORTH_ARROW_PATH
    if not t_path.exists():
        return 0, 0.0

    tmpl_base = cv2.imread(str(t_path), cv2.IMREAD_GRAYSCALE)
    if tmpl_base is None:
        return 0, 0.0

    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY) if len(img.shape) == 3 else img

    # Downsampling untuk kecepatan pencocokan tinggi
    scale_img = 0.50
    small_img = cv2.resize(gray, (0, 0), fx=scale_img, fy=scale_img)

    best_rot = 0
    best_score = -1.0

    for rot in [0, 90, 180, 270]:
        rimg = rotate_image(small_img, rot)
        for t_scale in [0.85, 1.00, 1.15]:
            tmpl = cv2.resize(tmpl_base, (0, 0), fx=scale_img * t_scale, fy=scale_img * t_scale)
            if rimg.shape[0] < tmpl.shape[0] or rimg.shape[1] < tmpl.shape[1]:
                continue
            res = cv2.matchTemplate(rimg, tmpl, cv2.TM_CCOEFF_NORMED)
            _, max_val, _, _ = cv2.minMaxLoc(res)
            if max_val > best_score:
                best_score = max_val
                best_rot = rot

    return best_rot, float(best_score)


def detect_map_viewport(img: np.ndarray) -> Tuple[int, int, int, int]:
    """
    Deteksi otomatis kotak batas aktif peta (neatline) tanpa nilai hardcoded.
    Menggunakan morphological operations pada kernel garis horizontal & vertikal.
    Mengembalikan (bx, by, bw, bh).
    """
    h, w = img.shape[:2]
    is_landscape = w > h

    scale = 0.25
    small = cv2.resize(img, (0, 0), fx=scale, fy=scale)
    sh, sw = small.shape[:2]
    gray = cv2.cvtColor(small, cv2.COLOR_BGR2GRAY)

    # Adaptive binarization untuk menangkap garis bingkai hitam tebal
    _, bin_inv = cv2.threshold(gray, 75, 255, cv2.THRESH_BINARY_INV)

    # Filter morfologi garis horizontal
    h_len = max(20, int(sw * 0.20))
    h_kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (h_len, 1))
    h_lines = cv2.morphologyEx(bin_inv, cv2.MORPH_OPEN, h_kernel)

    # Filter morfologi garis vertikal
    v_len = max(20, int(sh * 0.20))
    v_kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (1, v_len))
    v_lines = cv2.morphologyEx(bin_inv, cv2.MORPH_OPEN, v_kernel)

    # Gabungan kisi bingkai
    grid = cv2.add(h_lines, v_lines)
    cnts, _ = cv2.findContours(grid, cv2.RETR_TREE, cv2.CHAIN_APPROX_SIMPLE)

    best_box = None
    max_area = 0
    total_area = sw * sh

    for c in cnts:
        x, y, bw, bh = cv2.boundingRect(c)
        area = bw * bh
        # Kotak peta harus berada di dalam batas wajar margin dan mencakup proporsi substansial
        if x < 0.15 * sw and y < 0.15 * sh and bw > 0.55 * sw and bh > 0.45 * sh:
            if 0.35 * total_area < area < 0.90 * total_area:
                if area > max_area:
                    max_area = area
                    best_box = (
                        int(round(x / scale)),
                        int(round(y / scale)),
                        int(round(bw / scale)),
                        int(round(bh / scale))
                    )

    if best_box is None:
        # Fallback rasio standar jika neatline terputus atau tidak terdeteksi
        if is_landscape:
            best_box = (int(0.025 * w), int(0.045 * h), int(0.77 * w), int(0.91 * h))
        else:
            best_box = (int(0.035 * w), int(0.035 * h), int(0.93 * w), int(0.76 * h))

    return best_box


def rotate_image(img: np.ndarray, rot_deg: int) -> np.ndarray:
    """
    Rotasi citra pada sudut ortogonal: 0, 90, 180, atau 270 derajat searah jarum jam.
    """
    rot_deg = rot_deg % 360
    if rot_deg == 0:
        return img
    elif rot_deg == 90:
        return cv2.rotate(img, cv2.ROTATE_90_CLOCKWISE)
    elif rot_deg == 180:
        return cv2.rotate(img, cv2.ROTATE_180)
    elif rot_deg == 270:
        return cv2.rotate(img, cv2.ROTATE_90_COUNTERCLOCKWISE)
    return img
