from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from hashlib import sha256
from pathlib import Path
import sqlite3
from datetime import datetime, timezone


@dataclass(frozen=True)
class Observation:
    fetched_at: datetime
    price: Decimal | None
    success: bool
    error: str | None


class Store:
    def __init__(self, project_dir: Path):
        self.root = project_dir / ".snipe" / "v1"
        self.pages = self.root / "pages"
        self.pages.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(self.root / "history.sqlite3")
        self.db.row_factory = sqlite3.Row
        self.db.execute("""CREATE TABLE IF NOT EXISTS observations (
            id INTEGER PRIMARY KEY, url TEXT NOT NULL, product TEXT NOT NULL,
            retailer TEXT NOT NULL, fetched_at TEXT NOT NULL, price TEXT,
            success INTEGER NOT NULL, error TEXT, cache_path TEXT)
        """)
        self.db.execute("CREATE INDEX IF NOT EXISTS observation_url_time ON observations(url, fetched_at)")
        self.db.execute("""CREATE TABLE IF NOT EXISTS vendor_fetches (
            vendor TEXT PRIMARY KEY, last_started_at REAL NOT NULL)
        """)
        self.db.commit()

    def close(self) -> None:
        self.db.close()

    @staticmethod
    def _dt(value: str) -> datetime:
        return datetime.fromisoformat(value).astimezone(timezone.utc)

    def latest_attempt(self, url: str) -> Observation | None:
        row = self.db.execute("SELECT * FROM observations WHERE url=? ORDER BY id DESC LIMIT 1", (url,)).fetchone()
        return self._observation(row) if row else None

    def successful(self, url: str) -> list[Observation]:
        rows = self.db.execute("SELECT * FROM observations WHERE url=? AND success=1 AND price IS NOT NULL ORDER BY id", (url,)).fetchall()
        return [self._observation(row) for row in rows]

    def cache_html(self, url: str) -> str | None:
        row = self.db.execute("SELECT cache_path FROM observations WHERE url=? AND success=1 AND cache_path IS NOT NULL ORDER BY id DESC LIMIT 1", (url,)).fetchone()
        if not row:
            return None
        try:
            return (self.root / row["cache_path"]).read_text(encoding="utf-8")
        except OSError:
            return None

    def write_html(self, url: str, html: str, at: datetime) -> tuple[str, str]:
        """Save an immutable successful-page snapshot and return its path and contents."""
        stamp = at.astimezone(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
        filename = f"{sha256(url.encode()).hexdigest()}-{stamp}.html"
        relative = Path("pages") / filename
        path = self.root / relative
        temporary = path.with_suffix(".tmp")
        temporary.write_text(html, encoding="utf-8")
        temporary.replace(path)
        return str(relative), path.read_text(encoding="utf-8")

    def record_success(self, url: str, product: str, retailer: str, price: Decimal | None, cache_path: str, at: datetime) -> None:
        self._record(url, product, retailer, price, True, None, cache_path, at)

    def record_failure(self, url: str, product: str, retailer: str, error: str, at: datetime) -> None:
        self._record(url, product, retailer, None, False, error, None, at)

    def _record(self, url: str, product: str, retailer: str, price: Decimal | None, success: bool, error: str | None, cache_path: str | None, at: datetime) -> None:
        self.db.execute("INSERT INTO observations(url,product,retailer,fetched_at,price,success,error,cache_path) VALUES(?,?,?,?,?,?,?,?)",
            (url, product, retailer, at.astimezone(timezone.utc).isoformat(), str(price) if price is not None else None, int(success), error, cache_path))
        self.db.commit()

    def _observation(self, row: sqlite3.Row) -> Observation:
        return Observation(self._dt(row["fetched_at"]), Decimal(row["price"]) if row["price"] is not None else None, bool(row["success"]), row["error"])
