import asyncio

from playwright.async_api import async_playwright, BrowserContext, Page, Playwright


class BrowserManager:
    """管理本机持久化自动化浏览器。VPN 由业务人员手动连接，本模块不操作 VPN。"""

    def __init__(self) -> None:
        self._playwright: Playwright | None = None
        self._context: BrowserContext | None = None
        self._page: Page | None = None
        # 同一浏览器页面同一时间只执行一个任务，避免多个请求互相干扰。
        self.operation_lock = asyncio.Lock()

    async def start(
        self,
        headless: bool = False,
        channel: str = "chrome",
        user_data_dir: str = ".browser-profile",
    ) -> None:
        if self._context:
            return

        self._playwright = await async_playwright().start()
        # 使用独立的自动化专用用户目录，保存自动化会话，但不占用日常 Chrome 配置。
        self._context = await self._playwright.chromium.launch_persistent_context(
            user_data_dir=user_data_dir,
            headless=headless,
            channel=channel,
        )
        self._page = self._context.pages[0] if self._context.pages else await self._context.new_page()

    async def get_page(self, timeout_ms: int = 20_000) -> Page:
        """获取可复用的页面；页面关闭或失效时才创建新页面。"""
        if not self._context:
            raise RuntimeError("浏览器尚未启动")

        if self._page is None or self._page.is_closed():
            self._page = await self._context.new_page()

        self._page.set_default_timeout(timeout_ms)
        return self._page

    async def close(self) -> None:
        if self._context:
            await self._context.close()
            self._context = None
            self._page = None
        if self._playwright:
            await self._playwright.stop()
            self._playwright = None


browser_manager = BrowserManager()
