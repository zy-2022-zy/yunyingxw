import asyncio

from playwright.async_api import async_playwright, Browser, BrowserContext, Page, Playwright


class BrowserManager:
    """通过 CDP 接管由用户正常启动的本机 Chrome。VPN 由业务人员手动连接。"""

    def __init__(self) -> None:
        self._playwright: Playwright | None = None
        self._browser: Browser | None = None
        self._context: BrowserContext | None = None
        self._page: Page | None = None
        # 同一浏览器页面同一时间只执行一个任务，避免多个请求互相干扰。
        self.operation_lock = asyncio.Lock()

    async def start(self, cdp_url: str = "http://127.0.0.1:9222") -> None:
        if self._context:
            return

        self._playwright = await async_playwright().start()
        try:
            # 连接到通过 --remote-debugging-port 启动的 Chrome，
            # 不再由 Playwright 自己创建浏览器进程。
            self._browser = await self._playwright.chromium.connect_over_cdp(cdp_url)
            contexts = self._browser.contexts
            if not contexts:
                raise RuntimeError(
                    "已连接 Chrome，但没有可用的浏览器上下文。请检查 Chrome 是否正常启动。"
                )

            # CDP 通常只有一个默认 context，复用其中现有标签页。
            self._context = contexts[0]
            pages = self._context.pages
            self._page = pages[0] if pages else await self._context.new_page()
        except Exception:
            await self._playwright.stop()
            self._playwright = None
            self._browser = None
            self._context = None
            self._page = None
            raise

    async def get_page(self, timeout_ms: int = 20_000) -> Page:
        """获取可复用的页面；页面关闭或失效时才创建新页面。"""
        if not self._context:
            raise RuntimeError("尚未连接 Chrome。请先启动带 CDP 端口的 Chrome，再启动自动化服务。")

        if self._page is None or self._page.is_closed():
            self._page = await self._context.new_page()

        self._page.set_default_timeout(timeout_ms)
        return self._page

    async def close(self) -> None:
        # 这里只断开 Playwright 的 CDP 连接，不关闭用户启动的 Chrome。
        if self._playwright:
            await self._playwright.stop()
        self._playwright = None
        self._browser = None
        self._context = None
        self._page = None


browser_manager = BrowserManager()
