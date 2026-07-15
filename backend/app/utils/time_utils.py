from __future__ import annotations

from datetime import datetime, timedelta, timezone


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def iso_utc_now() -> str:
    return utc_now().isoformat()


def utc_after(seconds: int) -> datetime:
    return utc_now() + timedelta(seconds=seconds)

