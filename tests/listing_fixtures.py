"""Real gallery fixtures for tests of listing fields and CLI readers."""
from pathlib import Path


def write_gallery(folder):
    from PIL import Image
    folder = Path(folder)
    for name in ("cover.png", "preview-1.png", "preview-2.png", "preview-3.png"):
        Image.new("RGB", (1200, 1600), "white").save(folder / name)
