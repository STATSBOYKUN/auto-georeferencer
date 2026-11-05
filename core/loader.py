#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Modul Loader Data Spasial Batas SLS (.geojson / .gpkg / .shp).
Mendukung multi-layer GeoPackage, auto-reproject EPSG:4326, dan normalisasi kolom ID SLS.
"""

from pathlib import Path
from typing import Optional, Union, List
import geopandas as gpd

try:
    import pyogrio
    HAS_PYOGRIO = True
except ImportError:
    HAS_PYOGRIO = False


def normalize_columns(gdf: gpd.GeoDataFrame) -> gpd.GeoDataFrame:
    """Normalisasi nama-nama kolom utama SLS agar case-insensitive dan konsisten tanpa menimbulkan duplikasi."""
    existing_cols = set(gdf.columns)
    col_mapping = {}

    # Cek idsubsls
    if "idsubsls" not in existing_cols:
        for col in gdf.columns:
            clow = str(col).lower().replace("_", "").replace("-", "")
            if clow in ["idsubsls", "kdsubsls"]:
                col_mapping[col] = "idsubsls"
                break

    # Cek idsls
    if "idsls" not in existing_cols:
        for col in gdf.columns:
            clow = str(col).lower().replace("_", "").replace("-", "")
            if clow in ["idsls", "kdsls"]:
                col_mapping[col] = "idsls"
                break

    # Cek nmsls
    if "nmsls" not in existing_cols:
        for col in gdf.columns:
            clow = str(col).lower().replace("_", "").replace("-", "")
            if clow in ["nmsls", "namasls"]:
                col_mapping[col] = "nmsls"
                break

    if col_mapping:
        gdf.rename(columns=col_mapping, inplace=True)
    return gdf


def list_geopackage_layers(path: Union[str, Path]) -> List[str]:
    """Daftar layer yang tersedia di dalam file GeoPackage."""
    p_str = str(path)
    if HAS_PYOGRIO:
        try:
            layers_info = pyogrio.list_layers(p_str)
            return [str(l[0]) for l in layers_info]
        except Exception:
            pass
    try:
        import fiona
        return fiona.listlayers(p_str)
    except Exception:
        return []


def load_sls_boundary(
    boundary_path: Union[str, Path],
    layer: Optional[str] = None
) -> gpd.GeoDataFrame:
    """
    Memuat file batas SLS dari GeoPackage (.gpkg), GeoJSON (.geojson), atau Shapefile (.shp).
    Mengembalikan GeoDataFrame yang sudah diproyeksikan ke EPSG:4326 dengan kolom ternormalisasi.
    """
    path = Path(boundary_path).resolve()
    if not path.exists():
        raise FileNotFoundError(f"File batas SLS tidak ditemukan: {path}")

    ext = path.suffix.lower()

    if ext == ".gpkg":
        available_layers = list_geopackage_layers(path)
        chosen_layer = layer

        if not chosen_layer and available_layers:
            # Cari layer dengan kata kunci spesifik
            for cand in available_layers:
                cand_low = cand.lower()
                if any(kw in cand_low for kw in ["sls", "wilkerstat", "batas", "polygon"]):
                    chosen_layer = cand
                    break
            # Jika belum ketemu, pakai layer pertama
            if not chosen_layer:
                chosen_layer = available_layers[0]

        if chosen_layer:
            gdf = gpd.read_file(path, layer=chosen_layer)
        else:
            gdf = gpd.read_file(path)
    else:
        gdf = gpd.read_file(path)

    # Pastikan CRS adalah WGS84 (EPSG:4326)
    if gdf.crs is not None:
        try:
            if gdf.crs.to_epsg() != 4326:
                gdf = gdf.to_crs(epsg=4326)
        except Exception:
            # Jika to_epsg() gagal (misal proj string kustom), paksa reproject ke 4326
            gdf = gdf.to_crs("EPSG:4326")
    else:
        gdf.set_crs(epsg=4326, inplace=True)

    # Normalisasi kolom
    normalize_columns(gdf)

    return gdf


def find_sls_feature(gdf: gpd.GeoDataFrame, target_id: str) -> Optional[gpd.GeoDataFrame]:
    """
    Mencari poligon SLS yang sesuai berdasarkan ID SLS (16 atau 14 digit).
    Menggunakan hierarki kecocokan:
    1. idsubsls == target_id
    2. idsls == target_id (atau 14 digit pertama)
    3. idsls awalan 10 digit (level desa/kelurahan)
    """
    clean_id = str(target_id).strip()

    # Cek idsubsls jika ada
    if "idsubsls" in gdf.columns:
        sub = gdf[gdf["idsubsls"].astype(str) == clean_id]
        if len(sub) > 0:
            return sub

    # Cek idsls
    if "idsls" in gdf.columns:
        sub = gdf[gdf["idsls"].astype(str) == clean_id]
        if len(sub) > 0:
            return sub
        if len(clean_id) >= 14:
            sub = gdf[gdf["idsls"].astype(str) == clean_id[:14]]
            if len(sub) > 0:
                return sub

    # Fallback prefix matching
    if "idsls" in gdf.columns and len(clean_id) >= 10:
        sub = gdf[gdf["idsls"].astype(str).str.startswith(clean_id[:10])]
        if len(sub) > 0:
            return sub

    return None
