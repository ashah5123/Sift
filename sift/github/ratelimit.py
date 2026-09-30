"""When to retry a GitHub API response, per
https://docs.github.com/en/rest/using-the-rest-api/rate-limits-for-the-rest-api
"""
from collections.abc import Mapping

MAX_BACKOFF = 60.0
SECONDARY_DEFAULT_WAIT = 60.0


def retry_delay(
    status: int, headers: Mapping[str, str], attempt: int, now: float, body: str = ""
) -> float | None:
    """Seconds to wait before retrying, or None if the request should not be retried."""
    if status in (403, 429):
        if "retry-after" in headers:
            return float(headers["retry-after"])
        if headers.get("x-ratelimit-remaining") == "0" and "x-ratelimit-reset" in headers:
            return max(0.0, float(headers["x-ratelimit-reset"]) - now) + 1.0
        if status == 429 or "secondary rate limit" in body.lower():
            return SECONDARY_DEFAULT_WAIT * (2 ** attempt)
        return None  # a real 403: missing permission
    if status >= 500:
        return min(MAX_BACKOFF, float(2 ** attempt))
    return None
