#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Wilkerstat Autonomous Georeferencing Pipeline Runner.
Menghasilkan worldfile (.jgw / .pgw / .tfw) presisi tinggi berbasis pencocokan visual
terhadap poligon batas SLS (GeoPackage / GeoJSON), koreksi arah utara (North-Up),
dan visualisasi Rich Live Dynamic Dashboard.
"""

import os
import sys
import argparse
import csv
from pathlib import Path
from typing import Optional, List
import cv2
import numpy as np
import geopandas as gpd

from rich.console import Console, Group
from rich.table import Table
from rich.panel import Panel
from rich.progress import Progress, SpinnerColumn, BarColumn, TextColumn, TimeElapsedColumn, TimeRemainingColumn
from rich.live import Live
from rich.text import Text
from rich import box

# Pastikan root workspace terdaftar di sys.path
ROOT_DIR = Path(__file__).resolve().parent.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from config import (
    DEFAULT_ASSETS_DIR,
    DEFAULT_OUTPUT_DIR,
    DEFAULT_CACHE_DIR,
    DEFAULT_SLS_PATH,
    DEFAULT_SLS_LAYER,
    SUPPORTED_IMAGE_FORMATS,
    WGS84_M_PER_DEG_LON,
    WGS84_M_PER_DEG_LAT
)
from core.loader import load_sls_boundary, find_sls_feature
from core.hybrid_refiner import get_hybrid_mosaic
from core.optimizer import optimize_georeferencing_configuration
from core.worldfile import (
    write_worldfile,
    get_worldfile_extension,
    save_north_up_image,
    clean_auxiliary_files
)
from core.visualizer import render_verification_preview

console = Console()


def build_summary_table(summary_records: list, max_rows: int = 12) -> Table:
    """Bangun tabel rekapitulasi dinamis untuk Rich Live."""
    total = len(summary_records)
    title_suffix = f" (Menampilkan {min(max_rows, total)} dari {total} peta)" if total > max_rows else ""
    table = Table(
        title=f"[bold green]Rekapitulasi Optimasi Georeferensi Wilkerstat[/bold green]{title_suffix}",
        box=box.ROUNDED,
        header_style="bold cyan",
        expand=True
    )
    table.add_column("No", justify="center", style="dim", width=5)
    table.add_column("File Peta", justify="left", min_width=20)
    table.add_column("Nama SLS", justify="left", min_width=10)
    table.add_column("Rotasi", justify="center", width=10)
    table.add_column("Skala 1:N", justify="center", width=10)
    table.add_column("Chamfer (px)", justify="right", width=13)
    table.add_column("Err Fisik (m)", justify="right", width=13)
    table.add_column("Skor CAS", justify="right", width=10)
    table.add_column("Status", justify="center", min_width=14)

    display_records = summary_records[-max_rows:] if total > max_rows else summary_records
    start_num = total - len(display_records) + 1

    for idx, rec in enumerate(display_records, start_num):
        table.add_row(
            str(idx),
            rec["file"],
            rec["nmsls"],
            rec["rot"],
            rec["scale"],
            rec["err_px"],
            rec["err_m"],
            f"{rec['cas']:.2f}",
            rec["status"]
        )

    return table


def collect_image_files(input_path: Path) -> List[Path]:
    """
    Mengumpulkan seluruh file gambar yang didukung secara rekursif jika berupa direktori,
    secara otomatis mengabaikan folder 'Peta Contoh' dan file preview.
    """
    if input_path.is_file():
        if input_path.suffix.lower() in SUPPORTED_IMAGE_FORMATS:
            return [input_path]
        return []

    collected = []
    for ext in SUPPORTED_IMAGE_FORMATS:
        collected.extend(input_path.glob(f"*{ext}"))
        collected.extend(input_path.glob(f"*{ext.upper()}"))

    if not collected:
        # Coba rekursif ke subfolder
        for ext in SUPPORTED_IMAGE_FORMATS:
            collected.extend(input_path.rglob(f"*{ext}"))
            collected.extend(input_path.rglob(f"*{ext.upper()}"))

    # Hilangkan duplikasi, file preview, dan peta di dalam folder 'Peta Contoh'
    valid_files = []
    seen = set()
    for f in collected:
        f_resolved = f.resolve()
        if f.name.startswith("preview_"):
            continue

        parts_lower = [p.lower() for p in f_resolved.parts]
        if "peta contoh" in parts_lower or any("peta contoh" in p for p in parts_lower):
            continue

        if f_resolved not in seen:
            seen.add(f_resolved)
            valid_files.append(f)

    return sorted(valid_files)


def process_dataset(
    input_path: Path,
    output_dir: Path,
    boundary_path: Path,
    boundary_layer: Optional[str] = None,
    update_assets: bool = False,
    clean_aux: bool = True,
    zoom_level: int = 18,
    skip_existing: bool = False
):
    """Pipeline eksekusi dataset Wilkerstat dengan Rich Live Dynamic Dashboard."""
    output_dir.mkdir(parents=True, exist_ok=True)

    if clean_aux:
        rem_in = clean_auxiliary_files(input_path if input_path.is_dir() else input_path.parent)
        rem_out = clean_auxiliary_files(output_dir)
        console.print(f"[yellow]i[/yellow] Membersihkan {rem_in + rem_out} file auxiliary (.prj/.qpj/.aux.xml)")

    if not boundary_path.exists():
        console.print(f"[bold red]ERROR:[/bold red] File batas SLS tidak ditemukan: {boundary_path}")
        console.print("[yellow]Petunjuk:[/yellow] Periksa pengaturan SLS_BOUNDARY_PATH di file .env atau tentukan melalui argumen --boundary.")
        sys.exit(1)

    console.print(f"[cyan]i[/cyan] Memuat batas SLS referensi: [bold]{boundary_path.name}[/bold]...")
    try:
        gdf = load_sls_boundary(boundary_path, layer=boundary_layer)
        console.print(f"[green]✓[/green] Berhasil memuat {len(gdf)} fitur SLS (CRS: {gdf.crs})")
    except Exception as e:
        console.print(f"[bold red]ERROR:[/bold red] Gagal memuat file batas SLS: {e}")
        sys.exit(1)

    file_paths = collect_image_files(input_path)
    if not file_paths:
        console.print(f"[yellow]Peringatan:[/yellow] Tidak ada file citra yang didukung ({', '.join(SUPPORTED_IMAGE_FORMATS)}) di {input_path}")
        sys.exit(0)

    console.print(f"[green]i[/green] Ditemukan {len(file_paths)} peta scan untuk diproses.\n")

    summary_records = []

    # Setup Rich Progress Bars
    overall_progress = Progress(
        SpinnerColumn(),
        TextColumn("[bold cyan]{task.description}"),
        BarColumn(complete_style="cyan", finished_style="green"),
        TextColumn("[progress.percentage]{task.percentage:>3.0f}%"),
        TimeElapsedColumn(),
        TimeRemainingColumn()
    )
    overall_task = overall_progress.add_task("Memproses Dataset Peta...", total=len(file_paths))

    current_status_text = Text("Menyiapkan pemrosesan...", style="bold yellow")
    current_panel = Panel(
        current_status_text,
        title="[bold blue]Status Peta Aktif[/bold blue]",
        border_style="blue"
    )

    initial_table = build_summary_table(summary_records)
    dashboard_group = Group(overall_progress, current_panel, initial_table)

    with Live(dashboard_group, console=console, refresh_per_second=4) as live:
        for fidx, fpath in enumerate(file_paths, 1):
            stem = fpath.stem
            clean_id = stem.split("_")[0]
            wf_ext = get_worldfile_extension(fpath)

            if skip_existing:
                target_wf = (fpath.parent / f"{stem}{wf_ext}") if update_assets else (output_dir / f"{stem}{wf_ext}")
                if target_wf.exists():
                    summary_records.append({
                        "file": stem,
                        "nmsls": "(Sudah Ada)",
                        "rot": "-",
                        "scale": "-",
                        "err_px": "-",
                        "err_m": "-",
                        "cas": 1.0,
                        "status": "[dim]SKIPPED[/dim]"
                    })
                    overall_progress.advance(overall_task)
                    live.update(Group(overall_progress, current_panel, build_summary_table(summary_records)))
                    continue

            nmsls_label = clean_id

            def update_map_step(stage_num: int, stage_desc: str, detail_msg: str = ""):
                msg = Text()
                msg.append("Peta Aktif: ", style="bold dim")
                msg.append(f"[{fidx}/{len(file_paths)}] ", style="bold cyan")
                msg.append(f"{fpath.name} ", style="bold white")
                msg.append(f"({nmsls_label})\n", style="bold green")

                stages = [
                    (1, "Arah Utara"),
                    (2, "Satelit Hybrid"),
                    (3, "Garis Batas SLS"),
                    (4, "Simpan Worldfile")
                ]
                msg.append("Tahapan   : ", style="bold dim")
                for s_num, s_name in stages:
                    if s_num < stage_num:
                        msg.append(f"[{s_num}. {s_name} ✓] ", style="green")
                    elif s_num == stage_num:
                        msg.append(f"[▶ {s_num}. {s_name}] ", style="bold black on yellow")
                    else:
                        msg.append(f"[{s_num}. {s_name}] ", style="dim")
                msg.append("\n")
                if detail_msg:
                    msg.append(f"Detail    : ▶ {detail_msg}", style="yellow")
                else:
                    msg.append(f"Detail    : ▶ {stage_desc}", style="cyan")

                current_panel.renderable = msg
                live.update(Group(overall_progress, current_panel, build_summary_table(summary_records)))

            # Cari fitur SLS pada data acuan batas
            sub = find_sls_feature(gdf, clean_id)
            if sub is None or len(sub) == 0:
                summary_records.append({
                    "file": stem,
                    "nmsls": "-",
                    "rot": "-",
                    "scale": "-",
                    "err_px": "-",
                    "err_m": "-",
                    "cas": 0.0,
                    "status": "[red]SLS_NOT_FOUND[/red]"
                })
                overall_progress.advance(overall_task)
                live.update(Group(overall_progress, current_panel, build_summary_table(summary_records)))
                continue

            row = sub.iloc[0]
            geom = row.geometry
            nmsls_label = str(row.get("nmsls", clean_id))
            parent_sls_id = str(row.get("idsls", clean_id[:14]))
            parent_features = gdf[gdf["idsls"].astype(str) == parent_sls_id] if "idsls" in gdf.columns else sub

            minx, miny, maxx, maxy = geom.bounds

            try:
                buf = np.fromfile(str(fpath), dtype=np.uint8)
                img = cv2.imdecode(buf, cv2.IMREAD_COLOR) if len(buf) > 0 else None
            except Exception:
                img = None
            if img is None:
                summary_records.append({
                    "file": stem,
                    "nmsls": nmsls_label,
                    "rot": "-",
                    "scale": "-",
                    "err_px": "-",
                    "err_m": "-",
                    "cas": 0.0,
                    "status": "[red]READ_ERROR[/red]"
                })
                overall_progress.advance(overall_task)
                live.update(Group(overall_progress, current_panel, build_summary_table(summary_records)))
                continue

            # [Tahap 1/4] Deteksi Arah Utara
            update_map_step(1, "Mendeteksi simbol panah arah utara (North Arrow)...", "Analisis orientasi layout & kompas 'U'...")

            # [Tahap 2/4] Unduh / Muat Mozaik Google Hybrid
            update_map_step(2, "Mengunduh / memuat citra Google Hybrid (Zoom 18)...", f"Koordinat cakupan SLS {clean_id}...")
            mosaic, nw_lon, se_lon, se_lat, nw_lat, res_lon_mos, res_lat_mos, res_m_mos = get_hybrid_mosaic(
                minx, miny, maxx, maxy,
                cache_name=f"hyb_{clean_id}",
                cache_dir=DEFAULT_CACHE_DIR,
                zoom=zoom_level
            )
            mosaic_meta = (nw_lon, nw_lat, res_lon_mos, res_lat_mos, res_m_mos)

            # [Tahap 3/4] Pencocokan Presisi Garis Batas SLS
            update_map_step(3, "Penyelarasan garis batas SLS multi-warna (Merah, Oranye, Cyan)...")
            opt_res = optimize_georeferencing_configuration(
                img=img,
                geom=geom,
                mosaic=mosaic,
                mosaic_meta=mosaic_meta,
                stem=stem,
                target_id=clean_id,
                progress_callback=lambda msg: update_map_step(3, "Penyelarasan batas SLS", msg)
            )

            # [Tahap 4/4] Simpan Citra Tegak (North-Up), Worldfile & Preview
            update_map_step(4, f"Menyimpan citra tegak (North-Up {opt_res['rotation']}°) & worldfile {wf_ext}...", f"Skala 1:{int(round(opt_res['scale']))} | Chamfer {opt_res['chamfer_err']:.2f} px")
            rot_img = opt_res["rot_img"]

            # Simpan citra tegak ke direktori output dengan ekstensi yang sama
            out_img_path = output_dir / f"{stem}{fpath.suffix}"
            save_north_up_image(out_img_path, rot_img)

            wf_out_path = output_dir / f"{stem}{wf_ext}"
            write_worldfile(wf_out_path, opt_res["A"], opt_res["E"], opt_res["C"], opt_res["F"])

            # Jika update_assets aktif, sinkronkan ke folder sumber
            if update_assets and fpath.parent != output_dir:
                save_north_up_image(fpath, rot_img)
                write_worldfile(fpath.parent / f"{stem}{wf_ext}", opt_res["A"], opt_res["E"], opt_res["C"], opt_res["F"])

            # Render Citra Preview Verifikasi Visual
            preview_out_path = output_dir / f"preview_{stem}.png"
            render_verification_preview(
                opt_result=opt_res,
                geom=geom,
                parent_features=parent_features,
                nmsls=nmsls_label,
                mosaic=mosaic,
                mosaic_meta=mosaic_meta,
                out_path=preview_out_path
            )

            ch_px = opt_res["chamfer_err"]
            err_m = ch_px * (opt_res["scale"] * (0.0254 / 300.0))
            is_pass = "PASS" in opt_res["status"]

            summary_records.append({
                "file": stem,
                "nmsls": nmsls_label,
                "rot": f"{opt_res['rotation']} deg",
                "scale": f"1:{int(round(opt_res['scale']))}",
                "err_px": f"{ch_px:.2f} px",
                "err_m": f"{err_m:.2f} m",
                "cas": opt_res["cas"],
                "status": f"[green]{opt_res['status']}[/green]" if is_pass else f"[yellow]{opt_res['status']}[/yellow]"
            })

            overall_progress.advance(overall_task)
            live.update(Group(overall_progress, current_panel, build_summary_table(summary_records)))

        # Selesai
        done_text = Text("Seluruh pemrosesan peta selesai 100%!", style="bold green")
        current_panel.renderable = done_text
        live.update(Group(overall_progress, current_panel, build_summary_table(summary_records)))

    # Ekspor laporan rekapitulasi CSV
    csv_path = output_dir / "rekapitulasi_georeferensi.csv"
    try:
        with open(csv_path, mode="w", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)
            writer.writerow(["No", "File Peta", "Nama SLS", "Rotasi", "Skala", "Chamfer_px", "Err_m", "CAS", "Status"])
            for idx, r in enumerate(summary_records, 1):
                clean_status = (
                    r["status"]
                    .replace("[green]", "").replace("[/green]", "")
                    .replace("[yellow]", "").replace("[/yellow]", "")
                    .replace("[red]", "").replace("[/red]", "")
                    .replace("[dim]", "").replace("[/dim]", "")
                )
                writer.writerow([
                    idx, r["file"], r["nmsls"], r["rot"], r["scale"],
                    r["err_px"], r["err_m"], f"{r['cas']:.2f}", clean_status
                ])
    except Exception as e:
        console.print(f"[yellow]Peringatan:[/yellow] Gagal menyimpan CSV rekap: {e}")

    pass_count = sum(1 for r in summary_records if "PASS" in r["status"])
    review_count = sum(1 for r in summary_records if "REVIEW" in r["status"])
    fail_count = sum(1 for r in summary_records if "FAIL" in r["status"])
    skipped_count = sum(1 for r in summary_records if "SKIPPED" in r["status"])

    console.print()
    console.print(Panel(
        f"[bold green]Pipeline Georeferensi Wilkerstat Selesai![/bold green]\n\n"
        f"• Total Peta Terdata  : [bold cyan]{len(summary_records)}[/bold cyan]\n"
        f"• Status PASS         : [bold green]{pass_count}[/bold green]\n"
        f"• Status REVIEW       : [bold yellow]{review_count}[/bold yellow]\n"
        f"• Status FAIL         : [bold red]{fail_count}[/bold red]\n"
        f"• Dilewati (Skipped)  : [bold dim]{skipped_count}[/bold dim]\n\n"
        f"• File Rekapitulasi CSV : [bold]{csv_path}[/bold]\n"
        f"• Direktori Output      : [bold]{output_dir}[/bold]",
        title="[bold green]Laporan Rekapitulasi[/bold green]",
        border_style="green"
    ))


def main():
    parser = argparse.ArgumentParser(description="Wilkerstat Autonomous Georeferencing Pipeline")
    parser.add_argument("--input", "-i", type=Path, default=DEFAULT_ASSETS_DIR, help="Direktori aset atau file citra (.jpg, .png, .tif)")
    parser.add_argument("--output", "-o", type=Path, default=DEFAULT_OUTPUT_DIR, help="Direktori hasil worldfile, citra, dan preview")
    parser.add_argument("--boundary", "-b", "--geojson", "-g", dest="boundary", type=Path, default=DEFAULT_SLS_PATH, help="Path ke file batas SLS (.gpkg atau .geojson)")
    parser.add_argument("--layer", "-l", type=str, default=DEFAULT_SLS_LAYER, help="Nama layer pada GeoPackage (opsional)")
    parser.add_argument("--update-assets", action="store_true", help="Simpan citra tegak & worldfile langsung ke folder assets")
    parser.add_argument("--skip-existing", action="store_true", help="Lewati peta yang sudah memiliki file worldfile")
    parser.add_argument("--no-clean-aux", dest="clean_aux", action="store_false", help="Jangan hapus file non-worldfile auxiliary")
    parser.add_argument("--zoom", "-z", type=int, default=18, help="Level zoom Google Hybrid (default: 18)")

    args = parser.parse_args()

    process_dataset(
        input_path=args.input,
        output_dir=args.output,
        boundary_path=args.boundary,
        boundary_layer=args.layer,
        update_assets=args.update_assets,
        clean_aux=args.clean_aux,
        zoom_level=args.zoom,
        skip_existing=args.skip_existing
    )


if __name__ == "__main__":
    main()
