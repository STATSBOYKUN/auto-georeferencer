from pathlib import Path
from typing import Union
import cv2
import numpy as np


def get_worldfile_extension(image_path: Union[str, Path]) -> str:
    """Mendapatkan ekstensi worldfile standar sesuai format citra."""
    ext = Path(image_path).suffix.lower()
    mapping = {
        ".jpg": ".jgw",
        ".jpeg": ".jgw",
        ".png": ".pgw",
        ".tif": ".tfw",
        ".tiff": ".tfw",
        ".webp": ".wgw",
        ".bmp": ".bpw",
    }
    return mapping.get(ext, ".jgw")


def write_worldfile(
    out_path: Union[str, Path],
    A: float,
    E: float,
    C: float,
    F: float,
    B: float = 0.0,
    D: float = 0.0
) -> Path:
    """
    Tulis worldfile standar 6-baris WGS84 dengan presisi 12-14 digit desimal.
    Baris 1: A (Resolusi X dalam derajat/piksel)
    Baris 2: D (Rotasi Y / skew)
    Baris 3: B (Rotasi X / skew)
    Baris 4: E (Resolusi Y dalam derajat/piksel, bernilai negatif)
    Baris 5: C (Koordinat Bujur/Lon titik tengah piksel kiri atas [0,0])
    Baris 6: F (Koordinat Lintang/Lat titik tengah piksel kiri atas [0,0])
    """
    path = Path(out_path)
    path.parent.mkdir(parents=True, exist_ok=True)

    content = (
        f"{A:.14f}\n"
        f"{D:.14f}\n"
        f"{B:.14f}\n"
        f"{E:.14f}\n"
        f"{C:.14f}\n"
        f"{F:.14f}\n"
    )
    path.write_text(content, encoding="utf-8")
    return path


def save_north_up_image(
    out_img_path: Union[str, Path],
    rot_img: np.ndarray,
    quality: int = 95
) -> Path:
    """
    Simpan citra scan yang telah ditegakkan (North-Up) ke disk
    dengan format yang sesuai (JPEG, PNG, dsb.).
    """
    path = Path(out_img_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    ext = path.suffix.lower()

    if ext in [".jpg", ".jpeg"]:
        params = [cv2.IMWRITE_JPEG_QUALITY, quality]
    elif ext == ".png":
        params = [cv2.IMWRITE_PNG_COMPRESSION, 3]
    elif ext == ".webp":
        params = [cv2.IMWRITE_WEBP_QUALITY, quality]
    else:
        params = []

    is_success, buf = cv2.imencode(ext, rot_img, params)
    if is_success:
        buf.tofile(str(path))
    else:
        cv2.imwrite(str(path), rot_img, params)
    return path


def clean_auxiliary_files(target_dir: Union[str, Path]) -> int:
    """
    Hapus file non-worldfile (.prj, .qpj, .aux.xml) dari direktori target.
    """
    dir_path = Path(target_dir)
    if not dir_path.exists():
        return 0

    removed = 0
    for ext in [".prj", ".qpj", ".aux.xml"]:
        for f in dir_path.rglob(f"*{ext}"):
            try:
                f.unlink()
                removed += 1
            except Exception:
                pass
    return removed
