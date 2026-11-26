from playwright.async_api import async_playwright, Browser, Playwright


class BrowserManager:
    """管理本机自动化浏览器。VPN 由业务人员手动连接，本模块不操作 VPN。"""

    def __init__(self) -> None:
        self._playwright: Playwright | None = None
        self._browser: Browser | None = None

    async def start(self, headless: bool = False) -> None:
        if self._browser and self._browser.is_connected():
            return
        self._playwright = await async_playwright().start()
        self._browser = await self._playwright.chromium.launch(headless=headless)

    async def new_page(self, timeout_ms: int = 20_000):
        if not self._browser or not self._browser.is_connected():
            raise RuntimeError("浏览器尚未启动")
        page = await self._browser.new_page()
        page.set_default_timeout(timeout_ms)
        return page

    async def close(self) -> None:
        if self._browser:
            await self._browser.close()
            self._browser = None
        if self._playwright:
            await self._playwright.stop()
            self._playwright = None


browser_manager = BrowserManager()
