"""Tests for llm_providers key in export_status.build_status_private()."""
import importlib
import os
import sys
import tempfile
import unittest

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE_DIR)
import unittest.mock


class TestLlmProvidersKey(unittest.TestCase):
    """build_status() must include 'llm_providers' key from llm_router.provider_status()."""

    def _make_db(self):
        import workforce
        tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        tmp.close()
        workforce.connect(tmp.name).close()
        self._tmp_db = tmp.name
        return tmp.name

    def tearDown(self):
        db = getattr(self, "_tmp_db", None)
        if db and os.path.exists(db):
            os.unlink(db)

    def test_llm_providers_key_present(self):
        """build_status() dict must have 'llm_providers' key."""
        import export_status
        db = self._make_db()
        status = export_status.build_status_private(db_path=db)
        self.assertIn("llm_providers", status)

    def test_llm_providers_is_list(self):
        """llm_providers value must be a list."""
        import export_status
        db = self._make_db()
        status = export_status.build_status_private(db_path=db)
        self.assertIsInstance(status["llm_providers"], list)

    def test_llm_providers_entries_have_required_keys(self):
        """Each provider entry must have 'provider' and 'configured' keys."""
        import export_status
        db = self._make_db()
        status = export_status.build_status_private(db_path=db)
        for entry in status["llm_providers"]:
            self.assertIn("provider", entry)
            self.assertIn("configured", entry)

    def test_llm_providers_configured_is_bool(self):
        """'configured' field must be bool."""
        import export_status
        db = self._make_db()
        status = export_status.build_status_private(db_path=db)
        for entry in status["llm_providers"]:
            self.assertIsInstance(entry["configured"], bool)

    def test_provider_status_not_empty(self):
        """At least one provider must always be listed."""
        import llm_router
        result = llm_router.provider_status()
        self.assertGreater(len(result), 0)

    def test_known_providers_present(self):
        """Groq, Gemini and Cloudflare must all appear in provider_status."""
        import llm_router
        names = [p["provider"] for p in llm_router.provider_status()]
        self.assertIn("Groq", names)
        self.assertIn("Gemini", names)
        self.assertIn("Cloudflare", names)

    def test_groq_unconfigured_when_no_env(self):
        """Groq must report configured=False when GROQ_API_KEY is absent."""
        import llm_router, importlib
        original = os.environ.pop("GROQ_API_KEY", None)
        try:
            importlib.reload(llm_router)
            result = {p["provider"]: p["configured"] for p in llm_router.provider_status()}
            self.assertFalse(result["Groq"])
        finally:
            if original is not None:
                os.environ["GROQ_API_KEY"] = original
            importlib.reload(llm_router)

    def test_groq_configured_when_env_set(self):
        """Groq must report configured=True when GROQ_API_KEY is set."""
        import llm_router, importlib
        original = os.environ.get("GROQ_API_KEY")
        os.environ["GROQ_API_KEY"] = "test_key"
        try:
            importlib.reload(llm_router)
            result = {p["provider"]: p["configured"] for p in llm_router.provider_status()}
            self.assertTrue(result["Groq"])
        finally:
            if original is None:
                os.environ.pop("GROQ_API_KEY", None)
            else:
                os.environ["GROQ_API_KEY"] = original
            importlib.reload(llm_router)

    def test_llm_providers_survives_missing_llm_router(self):
        """build_status() returns empty list gracefully if llm_router is unavailable."""
        import export_status, importlib
        with unittest.mock.patch.dict(sys.modules, {"llm_router": None}):
            importlib.reload(export_status)
            db = self._make_db()
            try:
                status = export_status.build_status_private(db_path=db)
                self.assertIn("llm_providers", status)
                self.assertIsInstance(status["llm_providers"], list)
            finally:
                importlib.reload(export_status)

    def test_write_status_leaves_providers_out_of_the_public_file(self):
        """The public file has no llm_providers (provider setup is private); the local snapshot still has it."""
        import export_status, json, tempfile as tf
        db = self._make_db()
        out = tf.NamedTemporaryFile(suffix=".json", delete=False)
        out.close()
        try:
            export_status.write_status(path=out.name, db_path=db)
            with open(out.name) as f:
                data = json.load(f)
            self.assertNotIn("llm_providers", data)
            self.assertIsInstance(export_status.build_status_private(db_path=db)["llm_providers"], list)
        finally:
            os.unlink(out.name)


import unittest.mock

if __name__ == "__main__":
    unittest.main()
