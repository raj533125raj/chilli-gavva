"""Lead fix (run #1013): the encrypted products archive is stored in git as parts under 100 MB."""
import hashlib
import os
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import product_parts  # noqa: E402


class ProductPartsTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.archive = str(self.root / "products.tar.gz.enc")
        self.parts = str(self.root / "products.tar.gz.enc.parts")

    def write(self, size, seed=b"x"):
        data = (hashlib.sha256(seed).digest() * (size // 32 + 1))[:size]
        Path(self.archive).write_bytes(data)
        return data

    def test_split_then_join_gives_the_same_bytes(self):
        data = self.write(250)
        names = product_parts.split(self.archive, self.parts, part_bytes=100)
        self.assertEqual(names, ["part-000", "part-001", "part-002"])
        self.assertTrue(all(os.path.getsize(os.path.join(self.parts, n)) <= 100 for n in names))
        os.remove(self.archive)
        self.assertTrue(product_parts.join(self.archive, self.parts))
        self.assertEqual(Path(self.archive).read_bytes(), data)

    def test_smaller_archive_removes_stale_parts(self):
        self.write(250)
        product_parts.split(self.archive, self.parts, part_bytes=100)
        data = self.write(120, b"y")
        self.assertEqual(product_parts.split(self.archive, self.parts, part_bytes=100), ["part-000", "part-001"])
        self.assertEqual(sorted(os.listdir(self.parts)), ["SHA256", "part-000", "part-001"])
        os.remove(self.archive)
        product_parts.join(self.archive, self.parts)
        self.assertEqual(Path(self.archive).read_bytes(), data)

    def test_unchanged_archive_does_not_rewrite_parts(self):
        self.write(250)
        product_parts.split(self.archive, self.parts, part_bytes=100)
        stamps = {n: os.stat(os.path.join(self.parts, n)).st_mtime_ns for n in os.listdir(self.parts)}
        product_parts.split(self.archive, self.parts, part_bytes=100)
        self.assertEqual(stamps, {n: os.stat(os.path.join(self.parts, n)).st_mtime_ns for n in os.listdir(self.parts)})

    def test_damaged_part_is_refused_and_old_archive_kept(self):
        self.write(250)
        product_parts.split(self.archive, self.parts, part_bytes=100)
        with open(os.path.join(self.parts, "part-001"), "r+b") as handle:
            handle.write(b"Z")
        before = Path(self.archive).read_bytes()
        with self.assertRaises(ValueError):
            product_parts.join(self.archive, self.parts)
        self.assertEqual(Path(self.archive).read_bytes(), before)
        self.assertFalse(os.path.exists(self.archive + ".joining"))

    def test_no_parts_keeps_the_single_committed_file(self):
        data = self.write(50)
        self.assertFalse(product_parts.join(self.archive, self.parts))
        self.assertEqual(Path(self.archive).read_bytes(), data)

    def test_default_part_size_is_under_githubs_limits(self):
        self.assertLessEqual(product_parts.PART_BYTES, 50 * 1024 * 1024)


if __name__ == "__main__":
    unittest.main()
