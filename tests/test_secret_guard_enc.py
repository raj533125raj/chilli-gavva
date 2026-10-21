"""The encrypted database (agent_data.db.enc) is random bytes, so the guard must not scan it as text."""
import os
import tempfile
import unittest

import secret_guard as sg


class EncryptedFileTests(unittest.TestCase):
    def test_enc_file_is_skipped(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "agent_data.db.enc")
            with open(path, "wb") as f:
                f.write(b"Salted__" + b"x" * 20 + b" someone@example.org " + os.urandom(64))
            self.assertEqual(sg.scan_file(path), [])

    def test_plain_text_is_still_scanned(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "notes.txt")
            with open(path, "w") as f:
                f.write("mail someone" + "@" + "realmail.org now\n")
            self.assertNotEqual(sg.scan_file(path), [])


if __name__ == "__main__":
    unittest.main()
