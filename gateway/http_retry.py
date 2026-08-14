"""Shared requests retry mounting for gateway HTTP clients.

Only idempotent GET requests are retried, with bounded attempts and backoff,
so transient 5xx / connection errors no longer fail registration or generation
flows outright (P2-12 fix). Non-idempotent POSTs are intentionally not retried.
"""

from __future__ import annotations

from urllib3.util.retry import Retry
from requests.adapters import HTTPAdapter


def mount_get_retry(session, *, total: int = 2, backoff_factor: float = 0.5) -> None:
    """Mount a GET-only retry adapter (502/503/504 + connection errors).

    No-op when the session object does not support ``mount`` (e.g. test fakes).
    """
    mount = getattr(session, "mount", None)
    if mount is None:
        return
    retry = Retry(
        total=total,
        connect=total,
        read=total,
        status=total,
        backoff_factor=backoff_factor,
        status_forcelist=(502, 503, 504),
        allowed_methods=frozenset(["GET"]),
        raise_on_status=False,
    )
    adapter = HTTPAdapter(max_retries=retry)
    mount("https://", adapter)
    mount("http://", adapter)
