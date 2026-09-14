"""Matplotlib interface for selecting EELS ROIs and fitting constrained peaks."""

from __future__ import annotations

import csv
import json
import queue
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

import matplotlib.pyplot as plt
import numpy as np
from matplotlib.patches import Rectangle
from matplotlib.widgets import Button, CheckButtons, RadioButtons, RectangleSelector, TextBox

from .core import (
    BootstrapResult,
    FitConfig,
    FitResult,
    expand_values,
    fit_spectrum,
    parse_number_list,
    residual_block_bootstrap,
)
from .io import EELSData, load_eels_hdf5


ROI_COLORS = {
    "Si": "#2b6cb0",
    "Interface": "#dd6b20",
    "Ge": "#2f855a",
}


@dataclass
class ROI:
    x_min: float
    x_max: float
    y_min: float
    y_max: float

    def normalized(self) -> "ROI":
        return ROI(
            min(self.x_min, self.x_max),
            max(self.x_min, self.x_max),
            min(self.y_min, self.y_max),
            max(self.y_min, self.y_max),
        )


@dataclass
class RegionFit:
    result: FitResult
    config: FitConfig
    pixel_count: int
    roi: ROI


class EELSPeakFitApp:
    """Interactive analysis app for a single exported EELS HDF5 cube."""

    def __init__(self, data: EELSData) -> None:
        self.data = data
        self.active_region = "Si"
        self.rois = self._default_rois()
        self.region_fits: dict[str, RegionFit] = {}
        self.bootstrap_results: dict[str, BootstrapResult] = {}
        self._event_queue: queue.Queue[tuple[str, object]] = queue.Queue()
        self._bootstrap_running = False
        self._selector: RectangleSelector | None = None
        self._buttons: list[Button] = []
        self._safe_resize_callbacks: list[Callable] = []
        self._safe_resize_cids: list[int] = []

        self.fig = plt.figure(figsize=(16, 9), facecolor="white")
        self.fig.canvas.manager.set_window_title("EELS constrained Gaussian fitting")
        self.ax_map = self.fig.add_axes([0.055, 0.415, 0.38, 0.52])
        self.ax_spectrum = self.fig.add_axes([0.52, 0.415, 0.43, 0.52])
        self.ax_colorbar = self.fig.add_axes([0.45, 0.415, 0.018, 0.52])

        self._create_map()
        self._create_controls()
        self._draw_rois()
        self._render_empty_spectrum()

        self._poll_timer = self.fig.canvas.new_timer(interval=200)
        self._poll_timer.add_callback(self._poll_bootstrap_queue)
        self._poll_timer.start()

    def _default_rois(self) -> dict[str, ROI]:
        x_min, x_max = float(self.data.x_nm[0]), float(self.data.x_nm[-1])
        y_min, y_max = float(self.data.y_nm[0]), float(self.data.y_nm[-1])
        span = x_max - x_min
        return {
            "Si": ROI(x_min, x_min + 0.38 * span, y_min, y_max),
            "Interface": ROI(
                x_min + 0.38 * span, x_min + 0.62 * span, y_min, y_max
            ),
            "Ge": ROI(x_min + 0.62 * span, x_max, y_min, y_max),
        }

    def _spatial_extent(self) -> list[float]:
        dx = float(np.median(np.diff(self.data.x_nm)))
        dy = float(np.median(np.diff(self.data.y_nm)))
        return [
            self.data.x_nm[0] - dx / 2,
            self.data.x_nm[-1] + dx / 2,
            self.data.y_nm[0] - dy / 2,
            self.data.y_nm[-1] + dy / 2,
        ]

    def _energy_mask(self, lower: float, upper: float) -> np.ndarray:
        if lower >= upper:
            raise ValueError("Energy lower bound must be smaller than upper bound.")
        mask = (self.data.energy_meV >= lower) & (self.data.energy_meV <= upper)
        if int(mask.sum()) < 8:
            raise ValueError("The selected energy range contains too few channels.")
        return mask

    def _integrated_map(self, lower: float, upper: float) -> np.ndarray:
        mask = self._energy_mask(lower, upper)
        energy = self.data.energy_meV[mask]
        values = self.data.intensity[:, :, mask]
        return np.trapezoid(values, energy, axis=2)

    def _create_map(self) -> None:
        integrated = self._integrated_map(0.0, 120.0)
        finite = integrated[np.isfinite(integrated)]
        lower, upper = np.percentile(finite, [2, 99.5])
        if lower == upper:
            upper = lower + 1.0

        self.map_image = self.ax_map.imshow(
            integrated,
            origin="lower",
            extent=self._spatial_extent(),
            aspect="auto",
            cmap="inferno",
            vmin=lower,
            vmax=upper,
        )
        self.colorbar = self.fig.colorbar(self.map_image, cax=self.ax_colorbar)
        self.colorbar.set_label("Integrated intensity (a.u. meV)")
        self.ax_map.set_xlabel("x (nm)")
        self.ax_map.set_ylabel("y (nm)")
        self.ax_map.set_title("Map integrated over the fit energy window")

    def _create_controls(self) -> None:
        zlp_fwhm = self.data.attributes.get("zlp_fwhm_meV", 10.0)
        try:
            zlp_fwhm = float(zlp_fwhm)
        except (TypeError, ValueError):
            zlp_fwhm = 10.0
        fwhm_floor = max(0.5, zlp_fwhm / 2.0)

        self.text_boxes: dict[str, TextBox] = {}
        self._add_text_box("fit_min", "Fit E min (meV)", [0.055, 0.275, 0.085, 0.04], "0")
        self._add_text_box("fit_max", "Fit E max (meV)", [0.155, 0.275, 0.085, 0.04], "120")
        self._add_text_box(
            "centers",
            "Initial centers (meV)",
            [0.285, 0.275, 0.18, 0.04],
            "20, 50, 75, 100",
        )
        self._add_text_box(
            "center_lower",
            "Center lower (meV)",
            [0.49, 0.275, 0.18, 0.04],
            "10, 40, 65, 90",
        )
        self._add_text_box(
            "center_upper",
            "Center upper (meV)",
            [0.695, 0.275, 0.18, 0.04],
            "30, 60, 85, 110",
        )
        self._add_text_box(
            "fwhm_lower",
            "FWHM lower (meV)",
            [0.285, 0.195, 0.18, 0.04],
            f"{fwhm_floor:.3g}",
        )
        self._add_text_box(
            "fwhm_upper",
            "FWHM upper (meV)",
            [0.49, 0.195, 0.18, 0.04],
            "40",
        )
        self._add_text_box(
            "samples",
            "Bootstrap samples",
            [0.055, 0.195, 0.085, 0.04],
            "1000",
        )
        self._add_text_box(
            "block",
            "Block size",
            [0.155, 0.195, 0.085, 0.04],
            "auto",
        )

        radio_axis = self.fig.add_axes([0.055, 0.075, 0.12, 0.09], facecolor="#f7fafc")
        self.region_selector = RadioButtons(
            radio_axis, ("Si", "Interface", "Ge"), active=0, activecolor=ROI_COLORS["Si"]
        )
        self.region_selector.on_clicked(self._on_region_selected)
        self.fig.text(0.055, 0.17, "ROI selected by rectangle drag", fontsize=9)

        check_axis = self.fig.add_axes([0.70, 0.187, 0.16, 0.06], facecolor="#f7fafc")
        self.baseline_check = CheckButtons(check_axis, ("Linear baseline",), (True,))

        self._add_button("Update map", [0.205, 0.105, 0.105, 0.05], self._on_update_map)
        self._add_button("Fit active", [0.325, 0.105, 0.105, 0.05], self._on_fit_active)
        self._add_button("Fit all", [0.445, 0.105, 0.105, 0.05], self._on_fit_all)
        self._add_button(
            "Bootstrap active", [0.565, 0.105, 0.135, 0.05], self._on_bootstrap_active
        )
        self._add_button(
            "Bootstrap all", [0.715, 0.105, 0.12, 0.05], self._on_bootstrap_all
        )
        self._add_button("Save CSV", [0.85, 0.105, 0.095, 0.05], self._on_save)

        self.status_text = self.fig.text(
            0.205,
            0.045,
            self._dataset_summary(),
            fontsize=9,
            color="#2d3748",
            va="bottom",
        )

        self._selector = RectangleSelector(
            self.ax_map,
            self._on_rectangle,
            useblit=True,
            button=[1],
            minspanx=float(np.median(np.diff(self.data.x_nm))) * 0.25,
            minspany=float(np.median(np.diff(self.data.y_nm))) * 0.25,
            spancoords="data",
            interactive=True,
            drag_from_anywhere=True,
        )

    def _add_text_box(
        self, key: str, label: str, position: list[float], default: str
    ) -> None:
        self.fig.text(position[0], position[1] + position[3] + 0.006, label, fontsize=8)
        axis = self.fig.add_axes(position)
        existing_resize_cids = set(
            self.fig.canvas.callbacks.callbacks.get("resize_event", {})
        )
        text_box = TextBox(axis, "", initial=default)
        self._replace_textbox_resize_callback(text_box, existing_resize_cids)
        self.text_boxes[key] = text_box

    def _replace_textbox_resize_callback(
        self, text_box: TextBox, existing_resize_cids: set[int]
    ) -> None:
        """Work around Matplotlib 3.11 TextBox handling of ResizeEvent.

        TextBox._resize is decorated for mouse events and assumes event.inaxes
        exists, but ResizeEvent has no such attribute. Remove that callback and
        keep only the necessary behavior: finish an active text edit safely.
        """

        callbacks = self.fig.canvas.callbacks.callbacks.get("resize_event", {})
        new_resize_cids = set(callbacks) - existing_resize_cids
        for cid in new_resize_cids:
            self.fig.canvas.mpl_disconnect(cid)
            if cid in text_box._cids:
                text_box._cids.remove(cid)

        def safe_resize(_event, box: TextBox = text_box) -> None:
            if box.capturekeystrokes:
                box.stop_typing()

        self._safe_resize_callbacks.append(safe_resize)
        self._safe_resize_cids.append(
            self.fig.canvas.mpl_connect("resize_event", safe_resize)
        )

    def _add_button(
        self, label: str, position: list[float], callback: Callable
    ) -> None:
        axis = self.fig.add_axes(position)
        button = Button(axis, label)
        button.on_clicked(callback)
        self._buttons.append(button)

    def _dataset_summary(self) -> str:
        y_count, x_count, energy_count = self.data.shape
        return (
            f"Loaded {y_count} x {x_count} spatial pixels and {energy_count} energy channels. "
            f"Active ROI: {self.active_region}."
        )

    def _set_status(self, message: str, color: str = "#2d3748") -> None:
        self.status_text.set_text(message)
        self.status_text.set_color(color)
        self.fig.canvas.draw_idle()

    def _on_region_selected(self, label: str) -> None:
        self.active_region = label
        if self._selector is not None:
            roi = self.rois[label].normalized()
            self._selector.extents = (roi.x_min, roi.x_max, roi.y_min, roi.y_max)
        self._set_status(f"Active ROI: {label}. Drag a rectangle on the map to replace it.")

    def _clamp_roi(self, roi: ROI) -> ROI:
        return ROI(
            max(float(self.data.x_nm[0]), roi.x_min),
            min(float(self.data.x_nm[-1]), roi.x_max),
            max(float(self.data.y_nm[0]), roi.y_min),
            min(float(self.data.y_nm[-1]), roi.y_max),
        ).normalized()

    def _on_rectangle(self, first_event, second_event) -> None:
        if first_event.xdata is None or second_event.xdata is None:
            return
        if first_event.ydata is None or second_event.ydata is None:
            return
        roi = ROI(
            first_event.xdata,
            second_event.xdata,
            first_event.ydata,
            second_event.ydata,
        )
        self.rois[self.active_region] = self._clamp_roi(roi)
        self._draw_rois()
        self._set_status(
            f"Updated {self.active_region} ROI. Fit it to refresh the spectrum."
        )

    def _draw_rois(self) -> None:
        if not hasattr(self, "roi_patches"):
            self.roi_patches: dict[str, Rectangle] = {}
            for name, color in ROI_COLORS.items():
                patch = Rectangle(
                    (0, 0),
                    0,
                    0,
                    edgecolor=color,
                    facecolor="none",
                    linewidth=2.0,
                    label=name,
                )
                self.ax_map.add_patch(patch)
                self.roi_patches[name] = patch

        for name, patch in self.roi_patches.items():
            roi = self.rois[name].normalized()
            patch.set_bounds(roi.x_min, roi.y_min, roi.x_max - roi.x_min, roi.y_max - roi.y_min)
            patch.set_alpha(1.0 if name == self.active_region else 0.65)
        self.fig.canvas.draw_idle()

    def _read_fit_window(self) -> tuple[float, float]:
        lower = float(self.text_boxes["fit_min"].text)
        upper = float(self.text_boxes["fit_max"].text)
        self._energy_mask(lower, upper)
        return lower, upper

    def _read_config(self) -> FitConfig:
        centers = parse_number_list(self.text_boxes["centers"].text)
        n_peaks = centers.size
        center_lower = expand_values(
            parse_number_list(self.text_boxes["center_lower"].text),
            n_peaks,
            "Center lower",
        )
        center_upper = expand_values(
            parse_number_list(self.text_boxes["center_upper"].text),
            n_peaks,
            "Center upper",
        )
        fwhm_lower = expand_values(
            parse_number_list(self.text_boxes["fwhm_lower"].text),
            n_peaks,
            "FWHM lower",
        )
        fwhm_upper = expand_values(
            parse_number_list(self.text_boxes["fwhm_upper"].text),
            n_peaks,
            "FWHM upper",
        )
        include_baseline = bool(self.baseline_check.get_status()[0])
        return FitConfig(
            centers=centers,
            center_lower=center_lower,
            center_upper=center_upper,
            fwhm_lower=fwhm_lower,
            fwhm_upper=fwhm_upper,
            include_baseline=include_baseline,
        )

    def _extract_region_spectrum(
        self, region: str, lower: float, upper: float
    ) -> tuple[np.ndarray, np.ndarray, int]:
        roi = self.rois[region].normalized()
        x_mask = (self.data.x_nm >= roi.x_min) & (self.data.x_nm <= roi.x_max)
        y_mask = (self.data.y_nm >= roi.y_min) & (self.data.y_nm <= roi.y_max)
        if not x_mask.any() or not y_mask.any():
            raise ValueError(f"{region} ROI does not contain a pixel center.")
        energy_mask = self._energy_mask(lower, upper)
        region_data = self.data.intensity[y_mask][:, x_mask, :]
        spectrum = np.nanmean(region_data, axis=(0, 1))
        return (
            self.data.energy_meV[energy_mask],
            spectrum[energy_mask],
            int(x_mask.sum() * y_mask.sum()),
        )

    def _fit_region(self, region: str, config: FitConfig) -> RegionFit:
        lower, upper = self._read_fit_window()
        energy, spectrum, pixel_count = self._extract_region_spectrum(region, lower, upper)
        result = fit_spectrum(energy, spectrum, config)
        return RegionFit(result=result, config=config, pixel_count=pixel_count, roi=self.rois[region])

    def _render_empty_spectrum(self) -> None:
        lower, upper = self._read_fit_window()
        self.ax_spectrum.clear()
        self.ax_spectrum.set_xlim(lower, upper)
        self.ax_spectrum.set_xlabel("Energy loss (meV)")
        self.ax_spectrum.set_ylabel("Mean intensity (a.u.)")
        self.ax_spectrum.set_title("Fit result will appear here")
        self.fig.canvas.draw_idle()

    def _render_fit(self, region: str) -> None:
        if region not in self.region_fits:
            self._render_empty_spectrum()
            return

        region_fit = self.region_fits[region]
        result = region_fit.result
        boot = self.bootstrap_results.get(region)
        self.ax_spectrum.clear()
        self.ax_spectrum.plot(
            result.energy_meV,
            result.intensity,
            ".",
            ms=3,
            color="#1a202c",
            label="ROI mean spectrum",
        )
        self.ax_spectrum.plot(
            result.energy_meV,
            result.fitted,
            color="#c53030",
            lw=2.2,
            label="Total fit",
        )
        if region_fit.config.include_baseline:
            self.ax_spectrum.plot(
                result.energy_meV,
                result.baseline,
                "--",
                color="#718096",
                lw=1.2,
                label="Linear baseline",
            )
        component_colors = plt.get_cmap("tab10")
        for index, component in enumerate(result.components):
            self.ax_spectrum.plot(
                result.energy_meV,
                component,
                color=component_colors(index),
                lw=1.1,
                alpha=0.9,
                label=f"Peak {index + 1}",
            )

        span = float(np.nanmax(result.intensity) - np.nanmin(result.intensity))
        residual_offset = float(np.nanmin(result.intensity) - max(span, 1.0) * 0.08)
        self.ax_spectrum.plot(
            result.energy_meV,
            result.residual + residual_offset,
            color="#805ad5",
            lw=0.9,
            label="Residual (offset)",
        )
        self.ax_spectrum.axhline(residual_offset, color="#a0aec0", lw=0.7)
        self.ax_spectrum.set_xlabel("Energy loss (meV)")
        self.ax_spectrum.set_ylabel("Mean intensity (a.u.)")
        self.ax_spectrum.set_title(
            f"{region} ROI: {region_fit.pixel_count} pixels, RMSE={result.rmse:.4g}"
        )
        self.ax_spectrum.legend(fontsize=8, loc="best", ncol=2)

        lines = []
        if boot is None:
            for index, center in enumerate(result.centers_meV):
                lines.append(
                    f"P{index + 1}: {center:.3f} meV, FWHM {result.fwhm_meV[index]:.3f}"
                )
        else:
            center_low, center_high = boot.center_ci95
            for index, center in enumerate(result.centers_meV):
                lines.append(
                    f"P{index + 1}: {center:.3f} [{center_low[index]:.3f}, {center_high[index]:.3f}] meV"
                )
            lines.append(
                f"Bootstrap: {boot.n_success}/{boot.n_requested} successful, block={boot.block_size}"
            )
        self.ax_spectrum.text(
            0.015,
            0.985,
            "\n".join(lines),
            transform=self.ax_spectrum.transAxes,
            va="top",
            ha="left",
            fontsize=8,
            bbox={"facecolor": "white", "edgecolor": "#cbd5e0", "alpha": 0.85},
        )
        self.fig.canvas.draw_idle()

    def _on_update_map(self, _event) -> None:
        try:
            lower, upper = self._read_fit_window()
            integrated = self._integrated_map(lower, upper)
            finite = integrated[np.isfinite(integrated)]
            color_min, color_max = np.percentile(finite, [2, 99.5])
            if color_min == color_max:
                color_max = color_min + 1.0
            self.map_image.set_data(integrated)
            self.map_image.set_clim(color_min, color_max)
            self.ax_map.set_title(
                f"Map integrated from {lower:.3g} to {upper:.3g} meV"
            )
            self._render_empty_spectrum()
            self._set_status("Map and fit window updated.")
        except (ValueError, TypeError) as error:
            self._set_status(f"Input error: {error}", color="#c53030")

    def _on_fit_active(self, _event) -> None:
        try:
            config = self._read_config()
            region_fit = self._fit_region(self.active_region, config)
            self.region_fits[self.active_region] = region_fit
            self.bootstrap_results.pop(self.active_region, None)
            self._render_fit(self.active_region)
            self._set_status(
                f"Fitted {self.active_region}: {region_fit.pixel_count} pixels, "
                f"{region_fit.result.nfev} optimizer evaluations."
            )
        except (ValueError, RuntimeError) as error:
            self._set_status(f"Fit error: {error}", color="#c53030")

    def _on_fit_all(self, _event) -> None:
        try:
            config = self._read_config()
            fitted_regions = []
            for region in ROI_COLORS:
                self.region_fits[region] = self._fit_region(region, config)
                self.bootstrap_results.pop(region, None)
                fitted_regions.append(region)
            self._render_fit(self.active_region)
            self._set_status("Fitted regions: " + ", ".join(fitted_regions) + ".")
        except (ValueError, RuntimeError) as error:
            self._set_status(f"Fit error: {error}", color="#c53030")

    def _read_bootstrap_settings(self) -> tuple[int, int | None]:
        n_resamples = int(float(self.text_boxes["samples"].text))
        raw_block = self.text_boxes["block"].text.strip().lower()
        if raw_block in {"", "auto"}:
            block_size = None
        else:
            block_size = int(float(raw_block))
        return n_resamples, block_size

    def _on_bootstrap_active(self, _event) -> None:
        self._start_bootstrap([self.active_region])

    def _on_bootstrap_all(self, _event) -> None:
        self._start_bootstrap(list(ROI_COLORS))

    def _start_bootstrap(self, regions: list[str]) -> None:
        if self._bootstrap_running:
            self._set_status("Bootstrap is already running.", color="#c05621")
            return
        try:
            config = self._read_config()
            n_resamples, block_size = self._read_bootstrap_settings()
            jobs: list[tuple[str, RegionFit]] = []
            for region in regions:
                region_fit = self._fit_region(region, config)
                self.region_fits[region] = region_fit
                self.bootstrap_results.pop(region, None)
                jobs.append((region, region_fit))
        except (ValueError, RuntimeError) as error:
            self._set_status(f"Bootstrap setup error: {error}", color="#c53030")
            return

        self._bootstrap_running = True
        total = len(jobs) * n_resamples
        self._set_status(
            f"Bootstrap started for {', '.join(regions)}: {total} constrained refits."
        )
        worker = threading.Thread(
            target=self._bootstrap_worker,
            args=(jobs, n_resamples, block_size),
            daemon=True,
        )
        worker.start()

    def _bootstrap_worker(
        self,
        jobs: list[tuple[str, RegionFit]],
        n_resamples: int,
        block_size: int | None,
    ) -> None:
        try:
            for job_index, (region, region_fit) in enumerate(jobs):
                completed_before = job_index * n_resamples

                def report(done: int, _total: int, offset: int = completed_before) -> None:
                    self._event_queue.put(("progress", (offset + done, len(jobs) * n_resamples)))

                bootstrap = residual_block_bootstrap(
                    region_fit.result,
                    region_fit.config,
                    n_resamples=n_resamples,
                    block_size=block_size,
                    progress=report,
                )
                self._event_queue.put(("result", (region, bootstrap)))
        except Exception as error:
            self._event_queue.put(("error", str(error)))
        finally:
            self._event_queue.put(("done", None))

    def _poll_bootstrap_queue(self) -> None:
        while True:
            try:
                kind, payload = self._event_queue.get_nowait()
            except queue.Empty:
                break

            if kind == "progress":
                completed, total = payload
                self._set_status(f"Bootstrap progress: {completed}/{total} refits.")
            elif kind == "result":
                region, bootstrap = payload
                self.bootstrap_results[region] = bootstrap
                if region == self.active_region:
                    self._render_fit(region)
            elif kind == "error":
                self._set_status(f"Bootstrap error: {payload}", color="#c53030")
            elif kind == "done":
                self._bootstrap_running = False
                if self.active_region in self.bootstrap_results:
                    self._render_fit(self.active_region)
                self._set_status("Bootstrap finished. Save CSV to retain the result.")

    def _on_save(self, _event) -> None:
        if not self.region_fits:
            self._set_status("Fit at least one region before saving.", color="#c05621")
            return
        try:
            from tkinter import Tk, filedialog

            default_name = self.data.source.with_name(
                self.data.source.stem + "_fit_results.csv"
            ).name
            root = Tk()
            root.withdraw()
            output = filedialog.asksaveasfilename(
                title="Save peak-fitting results",
                defaultextension=".csv",
                initialdir=str(self.data.source.parent),
                initialfile=default_name,
                filetypes=(("CSV files", "*.csv"),),
            )
            root.destroy()
            if not output:
                return
            output_path = Path(output)
            self._write_results(output_path)
            self._set_status(f"Saved fit results: {output_path}")
        except (OSError, RuntimeError, ValueError) as error:
            self._set_status(f"Save error: {error}", color="#c53030")

    def _write_results(self, output_path: Path) -> None:
        rows: list[dict[str, object]] = []
        for region, region_fit in self.region_fits.items():
            result = region_fit.result
            bootstrap = self.bootstrap_results.get(region)
            if bootstrap is not None:
                center_low, center_high = bootstrap.center_ci95
                fwhm_low, fwhm_high = bootstrap.fwhm_ci95
                area_low, area_high = bootstrap.area_ci95
                bootstrap_success = bootstrap.n_success
                bootstrap_requested = bootstrap.n_requested
                bootstrap_block = bootstrap.block_size
            else:
                center_low = center_high = np.full(result.centers_meV.shape, np.nan)
                fwhm_low = fwhm_high = np.full(result.fwhm_meV.shape, np.nan)
                area_low = area_high = np.full(result.areas.shape, np.nan)
                bootstrap_success = bootstrap_requested = bootstrap_block = np.nan

            roi = region_fit.roi.normalized()
            for index in range(result.centers_meV.size):
                rows.append(
                    {
                        "region": region,
                        "peak_index": index + 1,
                        "pixel_count": region_fit.pixel_count,
                        "roi_x_min_nm": roi.x_min,
                        "roi_x_max_nm": roi.x_max,
                        "roi_y_min_nm": roi.y_min,
                        "roi_y_max_nm": roi.y_max,
                        "center_meV": result.centers_meV[index],
                        "center_ci95_lower_meV": center_low[index],
                        "center_ci95_upper_meV": center_high[index],
                        "fwhm_meV": result.fwhm_meV[index],
                        "fwhm_ci95_lower_meV": fwhm_low[index],
                        "fwhm_ci95_upper_meV": fwhm_high[index],
                        "amplitude": result.amplitudes[index],
                        "area": result.areas[index],
                        "area_ci95_lower": area_low[index],
                        "area_ci95_upper": area_high[index],
                        "fit_rmse": result.rmse,
                        "fit_success": result.success,
                        "bootstrap_successful": bootstrap_success,
                        "bootstrap_requested": bootstrap_requested,
                        "bootstrap_block_size_channels": bootstrap_block,
                    }
                )

        fieldnames = list(rows[0].keys())
        with output_path.open("w", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(handle, fieldnames=fieldnames)
            writer.writeheader()
            writer.writerows(rows)

        metadata = {
            "source_hdf5": str(self.data.source),
            "source_attributes": self.data.attributes,
            "rois_nm": {
                name: vars(roi.normalized()) for name, roi in self.rois.items()
            },
            "warning": (
                "Bootstrap confidence intervals are conditional on the chosen ROI, "
                "preprocessing, fit range, peak model, and bounds."
            ),
        }
        metadata_path = output_path.with_suffix(".json")
        with metadata_path.open("w", encoding="utf-8") as handle:
            json.dump(metadata, handle, indent=2, default=_json_default)


def _json_default(value):
    if isinstance(value, np.generic):
        return value.item()
    if isinstance(value, np.ndarray):
        return value.tolist()
    return str(value)


def choose_hdf5_file() -> Path | None:
    from tkinter import Tk, filedialog

    root = Tk()
    root.withdraw()
    selected = filedialog.askopenfilename(
        title="Select exported EELS HDF5 file",
        filetypes=(("HDF5 files", "*.h5 *.hdf5"),),
    )
    root.destroy()
    return Path(selected) if selected else None


def launch(path: str | Path | None = None) -> EELSPeakFitApp | None:
    """Open the graphical fitting tool."""

    if path is None:
        path = choose_hdf5_file()
    if path is None:
        return None
    app = EELSPeakFitApp(load_eels_hdf5(path))
    plt.show()
    return app
