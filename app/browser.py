import asyncio

from playwright.async_api import async_playwright, Browser, Page, Playwright


class BrowserManager:
    """管理本机持久化自动化浏览器。VPN 由业务人员手动连接，本模块不操作 VPN。"""

    def __init__(self) -> None:
        self._playwright: Playwright | None = None
        self._browser: Browser | None = None
        self._page: Page | None = None
        # 同一浏览器页面同一时间只执行一个任务，避免多个请求互相干扰。
        self.operation_lock = asyncio.Lock()

    async def start(self, headless: bool = False, channel: str = "chrome") -> None:
        if self._browser and self._browser.is_connected():
            return
        self._playwright = await async_playwright().start()
        # 使用本机安装的 Chrome，便于复用其浏览器运行环境和企业策略。
        self._browser = await self._playwright.chromium.launch(
            headless=headless,
            channel=channel,
        )
        self._page = None

    async def get_page(self, timeout_ms: int = 20_000) -> Page:
        """获取可复用的页面；页面关闭或失效时才创建新页面。"""
        if not self._browser or not self._browser.is_connected():
            raise RuntimeError("浏览器尚未启动")

        if self._page is None or self._page.is_closed():
            self._page = await self._browser.new_page()

        self._page.set_default_timeout(timeout_ms)
        return self._page

    async def close(self) -> None:
        if self._browser:
            await self._browser.close()
            self._browser = None
            self._page = None
        if self._playwright:
            await self._playwright.stop()
            self._playwright = None


browser_manager = BrowserManager()
