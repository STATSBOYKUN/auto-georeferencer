import os
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()

# Base directories
BASE_DIR = Path(__file__).resolve().parent
DEFAULT_ASSETS_DIR = BASE_DIR / "assets"
DEFAULT_OUTPUT_DIR = BASE_DIR / "output"
DEFAULT_CACHE_DIR = BASE_DIR / "cache_tiles"

# Default SLS Boundary Path (.geojson / .gpkg / .shp)
_env_sls = os.getenv("SLS_BOUNDARY_PATH")
if _env_sls and Path(_env_sls).exists():
    DEFAULT_SLS_PATH = Path(_env_sls)
else:
    DEFAULT_SLS_PATH = BASE_DIR / "assets" / "batas-peta" / "Final_SLS_202516474.geojson"

DEFAULT_SLS_LAYER = os.getenv("SLS_BOUNDARY_LAYER", None)
SUPPORTED_VECTOR_FORMATS = {".gpkg", ".geojson", ".json", ".shp"}
SUPPORTED_IMAGE_FORMATS = {".jpg", ".jpeg", ".png", ".tif", ".tiff", ".webp"}

# Standard BPS Cartographic Scales
STANDARD_SCALES = [
    500.0, 750.0, 1000.0, 1250.0, 1500.0, 1750.0,
    2000.0, 2500.0, 3000.0, 4000.0, 5000.0, 7500.0, 10000.0
]

# Quality Gate Strict Thresholds
MAX_CHAMFER_ERROR_PX = 2.5        # Maksimum jarak rata-rata garis batas (piksel)
MIN_OVERLAP_RATIO = 0.85          # Minimum rasio poligon berada di dalam viewport
MAX_ASPECT_RATIO_DEVIATION = 0.01 # Maksimum deviasi pixel aspect ratio (square pixels)
MIN_CAS_SCORE = 0.80              # Minimum Composite Alignment Score untuk PASS

# Spatial Reference
DEFAULT_CRS = "EPSG:4326"
WGS84_M_PER_DEG_LON = 111319.5
WGS84_M_PER_DEG_LAT = 110574.0
