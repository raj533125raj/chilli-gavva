"""Tests for new serve.py API routes: /api/recipes, /api/run_recipe, /api/enqueue."""
import importlib
import json
import os
import sys
import tempfile
import threading
import unittest
from http.client import HTTPConnection

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE_DIR)


def _start_server():
    """Start a test instance of serve.py and return (server, port)."""
    import serve
    from http.server import ThreadingHTTPServer
    server = ThreadingHTTPServer(("127.0.0.1", 0), serve.Handler)
    port = server.server_address[1]
    t = threading.Thread(target=server.serve_forever, daemon=True)
    t.start()
    return server, port


def _post(port, path, body=None):
    conn = HTTPConnection("127.0.0.1", port, timeout=5)
    data = json.dumps(body or {}).encode()
    conn.request("POST", path, data, {
        "Content-Type": "application/json",
        "Content-Length": str(len(data)),
        "X-Requested-With": "chilli",
        "Host": "127.0.0.1",
    })
    r = conn.getresponse()
    return r.status, json.loads(r.read())


class TestRecipesRoute(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server, cls.port = _start_server()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()

    def test_recipes_returns_200(self):
        status, body = _post(self.port, "/api/recipes")
        self.assertEqual(status, 200)

    def test_recipes_has_list(self):
        _, body = _post(self.port, "/api/recipes")
        self.assertIn("recipes", body)
        self.assertIsInstance(body["recipes"], list)

    def test_recipes_entries_have_name(self):
        _, body = _post(self.port, "/api/recipes")
        for r in body["recipes"]:
            self.assertIn("name", r)
            self.assertIn("task_count", r)

    def test_run_recipe_missing_name(self):
        status, body = _post(self.port, "/api/run_recipe", {})
        self.assertEqual(status, 400)
        self.assertIn("error", body)

    def test_run_recipe_bad_name(self):
        status, body = _post(self.port, "/api/run_recipe", {"name": "nonexistent_xyz"})
        # 400 (recipe not found) or 500 (exception)
        self.assertIn(status, (400, 500))
        self.assertIn("error", body)

    def test_run_recipe_good_name(self):
        """If daily_fishery_content.yaml exists, /api/run_recipe should enqueue."""
        import recipe_runner as rr
        recipe_path = os.path.join(rr.RECIPES_DIR, "daily_fishery_content.yaml")
        if not os.path.exists(recipe_path):
            self.skipTest("recipe file not found")
        status, body = _post(self.port, "/api/run_recipe", {"name": "daily_fishery_content"})
        self.assertEqual(status, 200)
        self.assertIn("enqueued", body)

    def test_unknown_route_404(self):
        status, body = _post(self.port, "/api/nonexistent_endpoint_zz")
        self.assertEqual(status, 404)


class TestEnqueueRoute(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server, cls.port = _start_server()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()

    def test_enqueue_bad_kind(self):
        status, body = _post(self.port, "/api/enqueue", {"kind": "hack_the_planet"})
        self.assertEqual(status, 400)
        self.assertIn("error", body)

    def test_enqueue_missing_kind(self):
        status, body = _post(self.port, "/api/enqueue", {})
        self.assertEqual(status, 400)

    def test_enqueue_valid_kind(self):
        status, body = _post(self.port, "/api/enqueue", {
            "kind": "research", "payload": {"topic": "test"}
        })
        self.assertEqual(status, 200)
        self.assertIn("id", body)
        self.assertIsInstance(body["id"], int)

    def test_enqueue_all_valid_kinds(self):
        for kind in ("research", "draft", "plan", "review", "code_proposal"):
            status, body = _post(self.port, "/api/enqueue", {"kind": kind})
            self.assertEqual(status, 200, f"{kind} should be accepted")


if __name__ == "__main__":
    unittest.main()
