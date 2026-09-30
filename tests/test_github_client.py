import unittest

from tests.helpers import has

if has("httpx"):
    import httpx

    from sift.github.client import API, GitHubClient, InstallationTokens


class StaticTokens:
    def __init__(self):
        self.calls = 0

    async def get(self, installation_id: int) -> str:
        self.calls += 1
        return "tok"


@unittest.skipUnless(has("httpx"), "httpx not installed")
class TestGitHubClient(unittest.IsolatedAsyncioTestCase):
    async def test_retries_on_rate_limit_then_succeeds(self):
        responses = [
            httpx.Response(429, headers={"retry-after": "2"}),
            httpx.Response(200, json=[{"name": "bug"}, {"name": "docs"}]),
        ]
        seen_auth = []

        def handler(request: httpx.Request) -> httpx.Response:
            seen_auth.append(request.headers["authorization"])
            return responses.pop(0)

        slept = []

        async def fake_sleep(s):
            slept.append(s)

        async with httpx.AsyncClient(base_url=API, transport=httpx.MockTransport(handler)) as http:
            gh = GitHubClient(StaticTokens(), http, sleep=fake_sleep)
            labels = await gh.list_labels(1, "owner/repo")

        self.assertEqual(labels, ["bug", "docs"])
        self.assertEqual(slept, [2.0])
        self.assertEqual(seen_auth, ["Bearer tok", "Bearer tok"])

    async def test_permission_error_raises_without_retry(self):
        calls = []

        def handler(request):
            calls.append(1)
            return httpx.Response(403, text="Resource not accessible by integration")

        async with httpx.AsyncClient(base_url=API, transport=httpx.MockTransport(handler)) as http:
            gh = GitHubClient(StaticTokens(), http)
            with self.assertRaises(httpx.HTTPStatusError):
                await gh.comment(1, "owner/repo", 7, "hi")
        self.assertEqual(len(calls), 1)


@unittest.skipUnless(has("httpx", "jwt", "cryptography"), "httpx/pyjwt/cryptography not installed")
class TestInstallationTokens(unittest.IsolatedAsyncioTestCase):
    async def test_token_cached_until_near_expiry(self):
        from cryptography.hazmat.primitives import serialization
        from cryptography.hazmat.primitives.asymmetric import rsa

        key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        pem = key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        ).decode()

        issued = []

        def handler(request):
            issued.append(request.url.path)
            return httpx.Response(
                201, json={"token": f"t{len(issued)}", "expires_at": "2026-01-01T01:00:00Z"}
            )

        now = [1767225600.0]  # 2026-01-01T00:00:00Z
        async with httpx.AsyncClient(base_url=API, transport=httpx.MockTransport(handler)) as http:
            tokens = InstallationTokens("123", pem, http, clock=lambda: now[0])
            self.assertEqual(await tokens.get(42), "t1")
            self.assertEqual(await tokens.get(42), "t1")
            now[0] += 3400  # within 5 minutes of expiry
            self.assertEqual(await tokens.get(42), "t2")
        self.assertEqual(issued, ["/app/installations/42/access_tokens"] * 2)


if __name__ == "__main__":
    unittest.main()
