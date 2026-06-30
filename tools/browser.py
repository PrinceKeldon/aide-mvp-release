"""
AIDE -- Browser tool (Sprint 3)
Gives AIDE the ability to navigate websites, extract content,
fill forms, and click buttons using a headless Chromium browser.
No internet API needed -- she reads the web like a human.
"""
import asyncio
from loguru import logger
from tools.base import BaseTool, SafetyTier

try:
    from playwright.async_api import async_playwright, Browser, Page
    PLAYWRIGHT_AVAILABLE = True
except ImportError:
    PLAYWRIGHT_AVAILABLE = False
    logger.warning("Playwright not installed -- browser tool disabled")


class BrowserTool(BaseTool):
    """
    Headless browser. AIDE can visit any URL and read the full page.
    Much richer than search results -- gets the actual content.
    """

    MAX_CONTENT_CHARS = 8000

    @property
    def name(self) -> str:
        return "browse_url"

    @property
    def description(self) -> str:
        return (
            "Visit a URL and read the full page content. "
            "Use when search results aren't enough and you need the actual page. "
            "Input: a full URL starting with https://"
        )

    @property
    def safety_tier(self) -> SafetyTier:
        return SafetyTier.AUTONOMOUS

    async def execute(self, input_text) -> str:
        if not PLAYWRIGHT_AVAILABLE:
            return "Browser tool not available. Run: pip3 install playwright && playwright install chromium"

        if isinstance(input_text, dict):
            url = input_text.get("input", "") or input_text.get("url", "")
        else:
            url = str(input_text).strip()

        if not url.startswith("http"):
            url = "https://" + url

        logger.info(f"Browsing: {url}")

        try:
            async with async_playwright() as p:
                browser = await p.chromium.launch(headless=True)
                page    = await browser.new_page()

                await page.set_extra_http_headers({
                    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"
                })

                await page.goto(url, wait_until="domcontentloaded", timeout=15000)
                await asyncio.sleep(1)

                # Extract clean text content
                content = await page.evaluate("""() => {
                    // Remove scripts, styles, nav, footer
                    const remove = document.querySelectorAll(
                        'script,style,nav,footer,header,aside,[class*="ad"],[class*="cookie"]'
                    );
                    remove.forEach(el => el.remove());

                    // Get main content
                    const main = document.querySelector('main,article,[role="main"]');
                    const body = main || document.body;
                    return body.innerText;
                }""")

                title = await page.title()
                await browser.close()

                # Clean and truncate
                lines   = [l.strip() for l in content.split("\n") if l.strip()]
                cleaned = "\n".join(lines)[:self.MAX_CONTENT_CHARS]

                return f"Page: {title}\nURL: {url}\n\n{cleaned}"

        except Exception as e:
            logger.error(f"Browser error: {e}")
            return f"Could not browse {url}: {e}"


class PageClickTool(BaseTool):
    """
    Fill a form and submit it on a webpage.
    Input: JSON with url, fields dict, and submit_selector.
    Safety tier: APPROVE -- always asks before submitting forms.
    """

    @property
    def name(self) -> str:
        return "fill_form"

    @property
    def description(self) -> str:
        return (
            "Fill and submit a web form. "
            "Input: JSON with 'url', 'fields' (dict of selector->value), "
            "'submit' (CSS selector of submit button)."
        )

    @property
    def safety_tier(self) -> SafetyTier:
        return SafetyTier.APPROVE   # always needs approval

    async def execute(self, input_text) -> str:
        if not PLAYWRIGHT_AVAILABLE:
            return "Browser tool not available."

        import json
        try:
            if isinstance(input_text, dict):
                data = input_text
            else:
                data = json.loads(str(input_text))
        except Exception:
            return "Error: input must be JSON with url, fields, submit."

        url     = data.get("url", "")
        fields  = data.get("fields", {})
        submit  = data.get("submit", "")

        if not url or not fields:
            return "Error: need url and fields."

        logger.info(f"Filling form at: {url}")

        try:
            async with async_playwright() as p:
                browser = await p.chromium.launch(headless=True)
                page    = await browser.new_page()
                await page.goto(url, wait_until="domcontentloaded", timeout=15000)

                for selector, value in fields.items():
                    await page.fill(selector, str(value))
                    await asyncio.sleep(0.3)

                if submit:
                    await page.click(submit)
                    await asyncio.sleep(2)

                title = await page.title()
                await browser.close()

                return f"Form submitted successfully on: {title}"

        except Exception as e:
            logger.error(f"Form fill error: {e}")
            return f"Form fill failed: {e}"


class PriceMonitorTool(BaseTool):
    """
    Check the price of a product on any website.
    AIDE can monitor prices and alert when they change.
    Input: URL of a product page.
    """

    @property
    def name(self) -> str:
        return "check_price"

    @property
    def description(self) -> str:
        return (
            "Visit a product page and extract the current price. "
            "Use for price monitoring, deal checking, or comparison. "
            "Input: URL of the product page."
        )

    @property
    def safety_tier(self) -> SafetyTier:
        return SafetyTier.AUTONOMOUS

    async def execute(self, input_text) -> str:
        if not PLAYWRIGHT_AVAILABLE:
            return "Browser tool not available."

        if isinstance(input_text, dict):
            url = input_text.get("input", "") or input_text.get("url", "")
        else:
            url = str(input_text).strip()

        if not url.startswith("http"):
            url = "https://" + url

        logger.info(f"Checking price at: {url}")

        try:
            async with async_playwright() as p:
                browser = await p.chromium.launch(headless=True)
                page    = await browser.new_page()
                await page.goto(url, wait_until="domcontentloaded", timeout=15000)
                await asyncio.sleep(1)

                # Try common price selectors
                price = await page.evaluate("""() => {
                    const selectors = [
                        '[class*="price"]', '[id*="price"]',
                        '[class*="Price"]', '[data-price]',
                        '.a-price', '.product-price',
                        '[itemprop="price"]', '[class*="cost"]'
                    ];
                    for (const sel of selectors) {
                        const el = document.querySelector(sel);
                        if (el && el.innerText.match(/[\\d.,]+/)) {
                            return el.innerText.trim();
                        }
                    }
                    return null;
                }""")

                title = await page.title()
                await browser.close()

                if price:
                    return f"Product: {title}\nURL: {url}\nPrice: {price}"
                else:
                    return f"Could not find price on {title}. URL: {url}"

        except Exception as e:
            logger.error(f"Price check error: {e}")
            return f"Could not check price: {e}"