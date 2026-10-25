"""Optional headless browser transport; no pricing or history logic lives here."""
from __future__ import annotations

from pathlib import Path
import re

from bs4 import BeautifulSoup


class FetchError(RuntimeError):
    """A page could not be obtained unattended."""


def check_page(html: str) -> None:
    """Reject known challenges rather than treating them as product pages."""
    soup = BeautifulSoup(html, "html.parser")
    text = soup.get_text(" ", strip=True).lower()
    if soup.select_one("#captchacharacters, form[action*='validateCaptcha']") or any(
        phrase in text for phrase in ("enter the characters you see below", "verify you're a human", "robot check")
    ):
        raise FetchError("retailer returned a CAPTCHA/challenge; unattended fetch stopped")
    if "click the button below to continue shopping" in text:
        raise FetchError("retailer returned a Continue shopping interstitial")


def fetch_html(url: str, state_dir: Path) -> str:
    """Fetch rendered HTML using a persistent, headless Chromium profile.

    There is one bounded navigation and at most one ordinary interstitial click.
    Challenges are never solved or delegated to a human. Playwright is imported
    lazily, so HTTP-only installations do not need it.
    """
    try:
        from playwright.sync_api import sync_playwright, Error
    except ImportError as exc:
        raise FetchError("browser fetching requires: uv pip install -e '.[browser]'; python -m playwright install chromium") from exc

    state_dir.mkdir(parents=True, exist_ok=True)
    try:
        with sync_playwright() as playwright:
            context = playwright.chromium.launch_persistent_context(
                str(state_dir), headless=True, locale="en-CA", timeout=30_000,
            )
            try:
                page = context.new_page()
                page.set_default_timeout(10_000)
                response = page.goto(url, wait_until="domcontentloaded", timeout=30_000)
                if response is None or response.status >= 400:
                    raise FetchError(f"browser navigation failed: HTTP {response.status if response else 'no response'}")
                html = page.content()
                if "click the button below to continue shopping" in BeautifulSoup(html, "html.parser").get_text(" ", strip=True).lower():
                    page.get_by_role("button", name=re.compile(r"^continue shopping$", re.I)).click()
                    page.wait_for_load_state("domcontentloaded", timeout=15_000)
                # Bounded settling time for client-rendered content; no networkidle
                # dependency (retailer analytics can keep connections alive forever).
                page.wait_for_timeout(2_000)
                html = page.content()
                check_page(html)
                return html
            finally:
                context.close()
    except Error as exc:
        raise FetchError(f"browser fetch failed (ensure Chromium is installed): {exc}") from exc
