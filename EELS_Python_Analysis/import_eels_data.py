"""Convert a trusted toolbox MAT file to Python-readable HDF5."""

import argparse
from pathlib import Path

from eelsfit.importing import prepare_import


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input", type=Path)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    if args.input.suffix.lower() != ".mat":
        parser.error("This converter expects a MATLAB .mat input.")
    data = prepare_import(args.input, args.output.resolve())
    print(f"Exported {data.shape} [y, x, energy] to {data.source}")


if __name__ == "__main__":
    main()
