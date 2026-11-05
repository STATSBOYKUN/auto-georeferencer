# Wilkerstat Autonomous Georeferencing Tool (.jgw v6)

Aplikasi Python modular untuk menghasilkan worldfile (`.jgw`) presisi tinggi dari peta scan Wilkerstat BPS, dilengkapi integrasi Google Hybrid basemap dan evaluasi mandiri (*Closed-Loop Multi-Candidate Optimizer & Self-Checking Preview*).

---

## 🌟 Fitur Utama

1. **Closed-Loop Multi-Candidate Optimizer**:
   - Mengevaluasi secara mandiri kombinasi rotasi ortogonal ($0^\circ, 90^\circ, 180^\circ, 270^\circ$), skala standar kartografis BPS ($1:500$ hingga $1:10.000$), dan translasi spasial.
   - Menggunakan *Hierarchical Grid Search* berbasis titik jangkar *Red Line Bounding Box Center* untuk menemukan kecocokan kontur global tanpa resiko terjebak pada *local minima*.
2. **Refinement Google Hybrid Multimodal**:
   - Mengunduh dan menyelaraskan citra satelit Google Hybrid (`lyrs=y`, zoom 18) di sekitar cakupan wilayah SLS.
   - Menghitung korelasi struktural tepi jalan dan blok pemukiman untuk penyesuaian sub-pixel.
3. **Quality Gate Ketat Berbasis Fisik & Piksel**:
   - Memvalidasi presisi spasial dengan dua metrik utama:
     - **Chamfer Alignment Error**: Rata-rata jarak batas poligon GeoJSON terhadap garis merah scan ($\le 2.5\text{ px}$ atau toleransi fisik $\le 1.0\text{ meter}$).
     - **Containment Ratio**: Persentase poligon SLS berada di dalam bingkai aktif peta ($\ge 85\%$).
4. **Verifikasi Visual Komprehensif (`preview_*.png`)**:
   - Menghasilkan citra verifikasi dengan lapisan dasar peta scan, overlay semi-transparan Google Hybrid (35%), poligon GeoJSON target (hijau tebal/merah), poligon tetangga (cyan), jendela pembesaran *Inset Zoom 2.5x*, serta *Badge Status Diagnostik*.
5. **Format Standar GIS**:
   - Menghasilkan file worldfile `.jgw` 6-baris standar ESRI/QGIS dengan presisi 12 digit desimal.
   - Membersihkan file auxiliary usang (`.prj`, `.qpj`, `.aux.xml`) agar direktori aset dan keluaran tetap bersih.

---

## 📂 Struktur Direktori

```
wilkerstat/
├── main.py                      # Thin CLI Runner entry point
├── config.py                    # Konfigurasi default & ambang batas Quality Gate
├── .env.example                 # Template konfigurasi path batas SLS
├── .gitignore                   # Aturan pengabaian cache, output, dan Peta Contoh
├── core/
│   ├── __init__.py              # Export pipeline runner & modul inti
│   ├── runner.py                # Orkestrator CLI & Rich Live Dashboard
│   ├── viewport.py              # Deteksi neatline bingkai & rotasi citra
│   ├── boundary.py              # Ekstraksi kontur batas & Chamfer error
│   ├── hybrid_refiner.py        # Unduh Google Hybrid tile & korelasi struktural
│   ├── optimizer.py             # Closed-Loop Multi-Candidate Optimizer & Polish
│   ├── worldfile.py             # Penulisan file .jgw & pembersihan auxiliary file
│   ├── visualizer.py            # Pembuat citra preview verifikasi komprehensif
│   ├── loader.py                # Pemuat format batas SLS (GeoPackage/GeoJSON/SHP)
│   └── template_north_arrow.png # Template kompas panah utara
├── assets/
│   ├── batas-peta/              # Peta batas referensi SLS (.geojson)
│   └── Peta Contoh/             # Sampel pengujian (diabaikan oleh git & runner)
└── output/
    └── .gitkeep                 # Direktori keluaran file .jgw & preview visual
```

---

## 🚀 Panduan Penggunaan CLI

### 1. Memproses Satu File Peta Tertentu
```powershell
python main.py --input "assets/Peta Contoh/020001 - Bontang Kuala/6474020001000100_1_georeferenced.png" --output output/
```

### 2. Memproses Seluruh Peta di Folder `assets/` Secara Batch
```powershell
python main.py --input assets/ --output output/ --update-assets
```
*Catatan: Folder `assets/Peta Contoh/` secara otomatis diabaikan saat melakukan pemindaian folder `assets/`.*

### 3. Menggunakan Peta Dasar GeoJSON Kustom
```powershell
python main.py --input assets/ --geojson "D:/path/to/batas_sls.geojson"
```

### 4. Eksekusi Alternatif sebagai Python Module
```powershell
python -m core.runner --help
```
