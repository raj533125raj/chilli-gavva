"""Encrypt/decrypt the agent database. Uses a made-up test key and temp files only."""
import os
import shutil
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "scripts"))

import db_crypt  # noqa: E402

TEST_KEY = "unit-test-only-key-123"
MARK = b"plaintext-marker-row"


@unittest.skipUnless(shutil.which("openssl"), "openssl is not installed")
class DbCryptTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.plain = os.path.join(self.tmp.name, "agent_data.db")
        self.enc = os.path.join(self.tmp.name, "agent_data.db.enc")
        self.back = os.path.join(self.tmp.name, "back.db")
        with open(self.plain, "wb") as f:
            f.write(MARK * 50)

    def tearDown(self):
        self.tmp.cleanup()

    def test_roundtrip_gives_the_same_bytes(self):
        db_crypt.encrypt(self.plain, self.enc, environ={"DB_KEY": TEST_KEY})
        db_crypt.decrypt(self.enc, self.back, environ={"DB_KEY": TEST_KEY})
        with open(self.plain, "rb") as a, open(self.back, "rb") as b:
            self.assertEqual(a.read(), b.read())

    def test_encrypted_file_hides_the_plaintext(self):
        db_crypt.encrypt(self.plain, self.enc, environ={"DB_KEY": TEST_KEY})
        with open(self.enc, "rb") as f:
            self.assertNotIn(MARK, f.read())

    def test_wrong_key_fails_and_leaves_no_file(self):
        db_crypt.encrypt(self.plain, self.enc, environ={"DB_KEY": TEST_KEY})
        with self.assertRaises(db_crypt.CryptError) as ctx:
            db_crypt.decrypt(self.enc, self.back, environ={"DB_KEY": "a-different-test-key"})
        self.assertIn("wrong or the file is damaged", str(ctx.exception))
        self.assertNotIn(TEST_KEY, str(ctx.exception))
        self.assertFalse(os.path.exists(self.back), "no plaintext is left behind")
        leftovers = [n for n in os.listdir(self.tmp.name) if n.startswith(".db-crypt-")]
        self.assertEqual(leftovers, [])

    def test_missing_key_fails_before_anything_is_read_or_written(self):
        with self.assertRaises(db_crypt.CryptError) as ctx:
            db_crypt.encrypt(self.plain, self.enc, environ={})
        self.assertIn("DB_KEY is not set", str(ctx.exception))
        self.assertFalse(os.path.exists(self.enc))

    def test_blank_key_counts_as_missing(self):
        with self.assertRaises(db_crypt.CryptError):
            db_crypt.decrypt(self.enc, self.back, environ={"DB_KEY": "   "})

    def test_key_is_never_on_the_command_line(self):
        captured = {}
        real_run = db_crypt.subprocess.run

        def spy(cmd, **kwargs):
            captured["cmd"] = cmd
            captured["env"] = kwargs.get("env", {})
            return real_run(cmd, **kwargs)

        with mock.patch.object(db_crypt.subprocess, "run", side_effect=spy):
            db_crypt.encrypt(self.plain, self.enc, environ={"DB_KEY": TEST_KEY})
        self.assertNotIn(TEST_KEY, " ".join(captured["cmd"]))
        self.assertEqual(captured["env"].get("DB_CRYPT_PASS"), TEST_KEY)

    def test_missing_source_says_so(self):
        with self.assertRaises(db_crypt.CryptError) as ctx:
            db_crypt.decrypt(os.path.join(self.tmp.name, "nope.enc"), self.back, environ={"DB_KEY": TEST_KEY})
        self.assertIn("not found", str(ctx.exception))

    def test_products_bundle_roundtrip_and_checksum_comparison(self):
        source = os.path.join(self.tmp.name, "products")
        restored = os.path.join(self.tmp.name, "restored")
        os.makedirs(os.path.join(source, "fake-product"))
        with open(os.path.join(source, "fake-product", "sample.txt"), "wb") as handle:
            handle.write(b"fake product fixture only")
        bundle = os.path.join(self.tmp.name, "products.tar.gz.enc")
        db_crypt.pack_products(source, bundle, environ={"DB_KEY": TEST_KEY})
        db_crypt.unpack_products(bundle, restored, environ={"DB_KEY": TEST_KEY})
        self.assertTrue(db_crypt.compare_product_trees(source, restored))

    def test_products_bundle_repack_is_stable_when_contents_match(self):
        source = os.path.join(self.tmp.name, "products")
        os.mkdir(source)
        with open(os.path.join(source, "sample.txt"), "wb") as handle:
            handle.write(b"fake fixture")
        bundle = os.path.join(self.tmp.name, "products.tar.gz.enc")
        db_crypt.pack_products(source, bundle, environ={"DB_KEY": TEST_KEY})
        before = db_crypt._file_digest(bundle)
        db_crypt.pack_products(source, bundle, environ={"DB_KEY": TEST_KEY})
        self.assertEqual(before, db_crypt._file_digest(bundle))

    def test_product_pack_requires_key_before_reading_source(self):
        with self.assertRaises(db_crypt.CryptError) as ctx:
            db_crypt.pack_products(os.path.join(self.tmp.name, "missing"), environ={})
        self.assertIn("DB_KEY is not set", str(ctx.exception))

    def test_product_unpack_requires_key_and_writes_nothing(self):
        destination = os.path.join(self.tmp.name, "restored")
        with self.assertRaises(db_crypt.CryptError) as ctx:
            db_crypt.unpack_products(os.path.join(self.tmp.name, "missing.enc"), destination, environ={})
        self.assertIn("DB_KEY is not set", str(ctx.exception))
        self.assertFalse(os.path.exists(destination))


class CliTests(unittest.TestCase):
    def test_cli_without_key_exits_with_an_error(self):
        with mock.patch.dict(os.environ, {}, clear=True):
            self.assertEqual(db_crypt.main(["db_crypt.py", "decrypt"]), 1)

    def test_cli_usage(self):
        self.assertEqual(db_crypt.main(["db_crypt.py", "nonsense"]), 2)


if __name__ == "__main__":
    unittest.main()
