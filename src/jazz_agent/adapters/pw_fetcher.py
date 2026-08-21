"""Playwright/Chromium fetcher, used only when club.render_mode == 'js' (ADR-004).

No club needs this today. Playwright is imported lazily inside get() rather
than at module scope, so importing this module -- or this whole package --
never pulls in the browser-automation stack for the common http-only path.
"""

from __future__ import annotations


class PlaywrightFetcher:
    def get(self, url: str, render_mode: str = "js") -> str:
        if render_mode != "js":
            raise ValueError(
                f"PlaywrightFetcher only handles render_mode='js', got {render_mode!r}"
            )

        import trafilatura
        from playwright.sync_api import sync_playwright

        with sync_playwright() as pw:
            browser = pw.chromium.launch()
            try:
                page = browser.new_page()
                page.goto(url, wait_until="networkidle")
                html = page.content()
            finally:
                browser.close()

        return trafilatura.extract(html) or ""
