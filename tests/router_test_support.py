"""Per-case router fixtures, including module reloads and plain unittest runs."""
import os
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

import guardrails
import llm_provider_state
import llm_router


class RouterTestCase(unittest.TestCase):
    def setUp(self):
        super().setUp()
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.db = os.path.join(directory.name, "router.db")
        memory = sqlite3.connect(":memory:", check_same_thread=False)
        self.addCleanup(memory.close)
        self.enterContext(patch.object(guardrails, "DB_PATH", self.db))
        self.enterContext(patch.object(guardrails, "_TESTING", True))
        self.enterContext(patch.object(llm_provider_state, "_memory", memory))
        self.enterContext(patch.object(llm_router, "_MEMORY_STATE", memory))
        self.enterContext(patch.object(llm_router, "_MEM_COOLDOWN", {}))
        self.enterContext(patch.object(llm_router, "_RATE_LIMITED", set()))
