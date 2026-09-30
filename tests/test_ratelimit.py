import unittest

from sift.github.ratelimit import SECONDARY_DEFAULT_WAIT, retry_delay


class TestRetryDelay(unittest.TestCase):
    def test_success_and_client_errors_not_retried(self):
        self.assertIsNone(retry_delay(200, {}, 0, 0))
        self.assertIsNone(retry_delay(404, {}, 0, 0))
        self.assertIsNone(retry_delay(422, {}, 0, 0))

    def test_retry_after_header(self):
        self.assertEqual(retry_delay(429, {"retry-after": "7"}, 0, 0), 7.0)
        self.assertEqual(retry_delay(403, {"retry-after": "3"}, 0, 0), 3.0)

    def test_primary_limit_waits_until_reset(self):
        headers = {"x-ratelimit-remaining": "0", "x-ratelimit-reset": "1100"}
        self.assertEqual(retry_delay(403, headers, 0, now=1000), 101.0)

    def test_secondary_limit_without_headers(self):
        body = '{"message": "You have exceeded a secondary rate limit"}'
        self.assertEqual(retry_delay(403, {}, 0, 0, body), SECONDARY_DEFAULT_WAIT)
        self.assertEqual(retry_delay(403, {}, 1, 0, body), SECONDARY_DEFAULT_WAIT * 2)

    def test_permission_403_not_retried(self):
        self.assertIsNone(retry_delay(403, {"x-ratelimit-remaining": "4000"}, 0, 0, "Forbidden"))

    def test_server_errors_back_off(self):
        self.assertEqual(retry_delay(502, {}, 0, 0), 1.0)
        self.assertEqual(retry_delay(502, {}, 3, 0), 8.0)
        self.assertEqual(retry_delay(503, {}, 10, 0), 60.0)


if __name__ == "__main__":
    unittest.main()
