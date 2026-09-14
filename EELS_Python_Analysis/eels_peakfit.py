"""Launch the interactive constrained Gaussian EELS fitting tool."""

from __future__ import annotations

import argparse

from eelsfit.gui import launch
from eelsfit.io import load_eels_hdf5


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Interactive ROI selection and residual-bootstrap EELS fitting."
    )
    parser.add_argument(
        "hdf5_file",
        nargs="?",
        help="HDF5 file exported by matlab/export_eels_to_hdf5.m",
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="Load the HDF5 file and print its dimensions without opening a GUI.",
    )
    arguments = parser.parse_args()

    if arguments.check:
        if not arguments.hdf5_file:
            parser.error("--check requires hdf5_file")
        data = load_eels_hdf5(arguments.hdf5_file)
        print(f"source: {data.source}")
        print(f"intensity [y, x, energy]: {data.shape}")
        print(
            "x range: "
            f"{data.x_nm[0]:.6g} to {data.x_nm[-1]:.6g} nm; "
            "y range: "
            f"{data.y_nm[0]:.6g} to {data.y_nm[-1]:.6g} nm"
        )
        print(
            "energy range: "
            f"{data.energy_meV[0]:.6g} to {data.energy_meV[-1]:.6g} meV"
        )
        return

    launch(arguments.hdf5_file)


if __name__ == "__main__":
    main()

