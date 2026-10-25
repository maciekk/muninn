"""Project-wide, persistent spacing between top-level vendor fetch starts."""
from __future__ import annotations

import math
import sqlite3
import time
from urllib.parse import urlparse


VENDORS = ("amazon.ca", "canadacomputers.com", "newegg.ca", "memoryexpress.com")


def vendor_key(url: str) -> str:
    host = (urlparse(url).hostname or "").lower().rstrip(".")
    for domain in VENDORS:
        if host == domain or host.endswith("." + domain):
            return domain
    return host.removeprefix("www.")


def validate_delay(value: float) -> float:
    if not math.isfinite(value) or value < 0:
        raise ValueError("vendor delay must be a finite, non-negative number of seconds")
    return value


def wait_for_vendor(db: sqlite3.Connection, url: str, delay: float) -> None:
    """Claim a fetch start, sleeping outside the transaction if needed.

    Failed fetches count too: the claim is persisted before calling the network.
    BEGIN IMMEDIATE prevents overlapping processes from claiming the same slot.
    Each caller uses its own delay; projects with different databases are isolated.
    """
    validate_delay(delay)
    vendor = vendor_key(url)
    while True:
        db.execute("BEGIN IMMEDIATE")
        try:
            row = db.execute("SELECT last_started_at FROM vendor_fetches WHERE vendor=?", (vendor,)).fetchone()
            timestamp = time.time()
            remaining = max(0.0, row[0] + delay - timestamp) if row and delay else 0.0
            if not remaining:
                db.execute(
                    "INSERT INTO vendor_fetches(vendor,last_started_at) VALUES(?,?) "
                    "ON CONFLICT(vendor) DO UPDATE SET last_started_at=excluded.last_started_at",
                    (vendor, timestamp),
                )
            db.commit()
        except BaseException:
            db.rollback()
            raise
        if not remaining:
            return
        time.sleep(remaining)
