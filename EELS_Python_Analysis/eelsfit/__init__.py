"""Reusable routines for constrained EELS peak fitting."""

from .version import __version__

from .core import (
    BootstrapResult,
    FitConfig,
    FitResult,
    fit_spectrum,
    residual_block_bootstrap,
)
from .io import EELSData, load_eels_hdf5
from .pca import (
    PCABootstrapResult,
    PCAModel,
    PCAPeak,
    PCAResult,
    analyze_pca_reconstruction,
    estimate_resolved_peaks,
    fit_pca_model,
    spatial_block_pca_bootstrap,
)

__all__ = [
    "__version__",
    "BootstrapResult",
    "EELSData",
    "FitConfig",
    "FitResult",
    "PCABootstrapResult",
    "PCAModel",
    "PCAPeak",
    "PCAResult",
    "analyze_pca_reconstruction",
    "estimate_resolved_peaks",
    "fit_spectrum",
    "fit_pca_model",
    "load_eels_hdf5",
    "residual_block_bootstrap",
    "spatial_block_pca_bootstrap",
]
