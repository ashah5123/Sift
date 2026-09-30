"""Download a repo's issue history with ground truth, using only the standard library.

Ground truth for duplicates comes from what maintainers did:
- the issue was closed with reason "duplicate", or carries a duplicate label, and
- a comment names the original ("Duplicate of #123", GitHub's own marker).
Closed-as-not-planned issues are checked too, since maintainers often close
duplicates that way and only say so in a comment.
"""
import json
import os
import re
import subprocess
import sys
import time
import urllib.error
import urllib.request
from collections.abc import Callable, Iterator
from concurrent.futures import ThreadPoolExecutor
from typing import Any
from urllib.parse import urlencode

from sift.github.ratelimit import retry_delay

API = "https://api.github.com"
MAX_ATTEMPTS = 6

_DUP_LABEL = re.compile(r"dup", re.I)
_NEXT_LINK = re.compile(r'<([^>]+)>;\s*rel="next"')


def github_token() -> str:
    token = os.environ.get("GITHUB_TOKEN")
    if token:
        return token
    try:
        return subprocess.run(
            ["gh", "auth", "token"], capture_output=True, text=True, check=True
        ).stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        raise SystemExit("Set GITHUB_TOKEN or log in with `gh auth login`.")


def next_link(link_header: str) -> str | None:
    match = _NEXT_LINK.search(link_header or "")
    return match.group(1) if match else None


def duplicate_target(text: str, repo: str, number: int) -> int | None:
    """First issue number that `text` says this issue duplicates."""
    pattern = re.compile(
        r"\b(?:duplicate|dupe?)\s+of\s*:?\s*"
        rf"(?:https?://github\.com/{re.escape(repo)}/issues/|#)(\d+)",
        re.I,
    )
    for match in pattern.finditer(text):
        target = int(match.group(1))
        if target != number:
            return target
    return None


def row_from_api(repo: str, item: dict[str, Any]) -> dict[str, Any]:
    labels = [label["name"] for label in item.get("labels") or []]
    return {
        "repo": repo,
        "number": item["number"],
        "title": item.get("title") or "",
        "body": item.get("body") or "",
        "created_at": item["created_at"],
        "closed_at": item.get("closed_at"),
        "state": item.get("state"),
        "state_reason": item.get("state_reason"),
        "labels": labels,
        "author": (item.get("user") or {}).get("login"),
        "is_duplicate": item.get("state_reason") == "duplicate" or any(_DUP_LABEL.search(l) for l in labels),
        "duplicate_of": None,
    }


def needs_comment_check(row: dict[str, Any]) -> bool:
    return row["is_duplicate"] or row["state_reason"] == "not_planned"


class GitHubREST:
    def __init__(
        self,
        token: str,
        opener: Callable[..., Any] = urllib.request.urlopen,
        sleep: Callable[[float], None] = time.sleep,
    ):
        self._token = token
        self._open = opener
        self._sleep = sleep

    def get(self, url: str) -> tuple[Any, dict[str, str]]:
        if not url.startswith("http"):
            url = API + url
        for attempt in range(MAX_ATTEMPTS):
            req = urllib.request.Request(
                url,
                headers={
                    "Authorization": f"Bearer {self._token}",
                    "Accept": "application/vnd.github+json",
                    "X-GitHub-Api-Version": "2022-11-28",
                    "User-Agent": "sift-backtest",
                },
            )
            try:
                with self._open(req, timeout=30) as resp:
                    headers = {k.lower(): v for k, v in resp.headers.items()}
                    return json.load(resp), headers
            except urllib.error.HTTPError as e:
                headers = {k.lower(): v for k, v in e.headers.items()}
                body = e.read().decode(errors="replace")
                e.close()
                delay = retry_delay(e.code, headers, attempt, time.time(), body)
                if delay is None or attempt == MAX_ATTEMPTS - 1:
                    raise
            except (urllib.error.URLError, TimeoutError, ConnectionError):
                if attempt == MAX_ATTEMPTS - 1:
                    raise
                delay = float(2 ** attempt)
            print(f"  waiting {delay:.0f}s (attempt {attempt + 1})", file=sys.stderr)
            self._sleep(delay)
        raise AssertionError("unreachable")

    def paginate(self, path: str, params: dict[str, Any]) -> Iterator[list[Any]]:
        url: str | None = f"{API}{path}?{urlencode(params)}"
        while url:
            data, headers = self.get(url)
            yield data
            url = next_link(headers.get("link", ""))


def fetch_repo(
    repo: str, api: GitHubREST, limit: int = 5000, workers: int = 6, log=print
) -> list[dict[str, Any]]:
    """The `limit` most recent issues (PRs excluded), with duplicate ground truth."""
    rows: list[dict[str, Any]] = []
    params = {"state": "all", "sort": "created", "direction": "desc", "per_page": 100}
    for page in api.paginate(f"/repos/{repo}/issues", params):
        rows += [row_from_api(repo, item) for item in page if "pull_request" not in item]
        log(f"  {min(len(rows), limit)}/{limit} issues")
        if len(rows) >= limit:
            break
    rows = rows[:limit]

    to_check = [row for row in rows if needs_comment_check(row)]
    log(f"  checking comments on {len(to_check)} closed-as-duplicate/not-planned issues")

    def resolve(row: dict[str, Any]) -> None:
        comments, _ = api.get(f"/repos/{repo}/issues/{row['number']}/comments?per_page=100")
        text = "\n".join(c.get("body") or "" for c in comments)
        target = duplicate_target(text, repo, row["number"])
        if target is not None:
            row["duplicate_of"] = target
            row["is_duplicate"] = True

    done = 0
    with ThreadPoolExecutor(max_workers=workers) as pool:
        for _ in pool.map(resolve, to_check):
            done += 1
            if done % 100 == 0:
                log(f"  {done}/{len(to_check)} comment threads")
    return rows
