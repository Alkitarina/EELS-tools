"""Small source-only identity for launcher reuse; never scans experiment data."""
import hashlib
from pathlib import Path


def ui_source_id(root: Path | None = None) -> str:
    root = Path(root) if root is not None else Path(__file__).resolve().parents[1]
    paths = [root / "eels_web.py"]
    paths.extend((root / "eelsfit").rglob("*.py"))
    paths.extend(p for p in (root / "web" / "static").rglob("*")
                 if p.suffix in {".js", ".css", ".html"})
    digest = hashlib.sha256(b"eels_ui_source_v1\n")
    for path in sorted(paths, key=lambda p: p.relative_to(root).as_posix()):
        digest.update(path.relative_to(root).as_posix().encode("utf-8") + b"\0")
        digest.update(path.read_text(encoding="utf-8-sig").encode("utf-8") + b"\0")
    return digest.hexdigest()


if __name__ == "__main__":
    print(ui_source_id())
