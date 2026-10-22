"""R23 adapters: no network; every request is an injected fake."""
import unittest

import publisher


JOB = {"id": 1, "type": "x", "item_id": 9, "payload_path": "products/9/payload"}


class AdapterTests(unittest.TestCase):
    def adapter_cases(self):
        return ((publisher.GumroadAdapter, {"GUMROAD_TOKEN": "test"}),
                (publisher.PinterestAdapter, {"PINTEREST_TOKEN": "test"}),
                (publisher.TelegramChannelAdapter, {"TELEGRAM_BOT_TOKEN": "test", "TELEGRAM_CHANNEL_ID": "chan"}))

    def test_missing_configuration_skips_each_provider(self):
        for adapter_class, _ in self.adapter_cases():
            self.assertEqual(adapter_class({}).publish(JOB), ("skipped", "not configured", 0))

    def test_dry_run_logs_without_a_request_for_each_provider(self):
        for adapter_class, env in self.adapter_cases():
            adapter = adapter_class(env)
            self.assertEqual(adapter.publish(JOB, http_request=lambda *_: self.fail("request made"), dry_run=True),
                             ("done", None, 0))
            self.assertEqual(adapter.dry_run_log[0]["item_id"], 9)

    def test_success_temporary_and_permanent_results_for_each_provider(self):
        for adapter_class, env in self.adapter_cases():
            adapter = adapter_class(env)
            calls = []
            self.assertEqual(adapter.publish(JOB, lambda *args: calls.append(args) or {"status": 201}),
                             ("done", None, 1))
            self.assertEqual(len(calls), 1)
            replies = iter(({"status": 503}, {"status": 200}))
            self.assertEqual(adapter.publish(JOB, lambda *_: next(replies)), ("done", None, 2))
            self.assertEqual(adapter.publish(JOB, lambda *_: {"status": 400}), ("failed", "permanent error", 1))
            self.assertEqual(adapter.publish(JOB, lambda *_: {"status": 503}), ("failed", "temporary error", 2))


if __name__ == "__main__":
    unittest.main()
