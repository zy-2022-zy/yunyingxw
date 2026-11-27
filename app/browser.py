import asyncio
import os
import subprocess
from pathlib import Path
from urllib.parse import urlsplit

from playwright.async_api import async_playwright, Browser, BrowserContext, Page, Playwright


class BrowserManager:
    """按需启动专用 Chrome，并通过 CDP 管理浏览器连接。"""

    def __init__(self) -> None:
        self._playwright: Playwright | None = None
        self._browser: Browser | None = None
        self._context: BrowserContext | None = None
        self._page: Page | None = None
        self._chrome_process: subprocess.Popen | None = None
        self._cdp_url = "http://127.0.0.1:9222"
        self._user_data_dir: str | None = None
        self._startup_timeout_seconds = 20
        self.operation_lock = asyncio.Lock()

    @staticmethod
    def _find_chrome() -> str:
        """在常见安装位置查找 Google Chrome。"""
        candidates = [
            Path(os.environ.get("PROGRAMFILES", "")) / "Google/Chrome/Application/chrome.exe",
            Path(os.environ.get("PROGRAMFILES(X86)", "")) / "Google/Chrome/Application/chrome.exe",
            Path(os.environ.get("LOCALAPPDATA", "")) / "Google/Chrome/Application/chrome.exe",
        ]
        for candidate in candidates:
            if candidate.is_file():
                return str(candidate)
        raise FileNotFoundError(
            "没有找到 Google Chrome。请确认已安装 Chrome，或检查安装路径。"
        )

    async def start(
        self,
        cdp_url: str = "http://127.0.0.1:9222",
        user_data_dir: str | None = None,
        startup_timeout_seconds: int = 20,
    ) -> None:
        """只初始化 Playwright，不在 FastAPI 启动时弹出 Chrome。"""
        self._cdp_url = cdp_url
        self._user_data_dir = user_data_dir
        self._startup_timeout_seconds = startup_timeout_seconds
        if self._playwright is None:
            self._playwright = await async_playwright().start()

    def _cdp_host_port(self) -> tuple[str, int]:
        parsed = urlsplit(self._cdp_url)
        host = parsed.hostname or "127.0.0.1"
        if host not in ("127.0.0.1", "localhost", "::1"):
            raise ValueError("Chrome CDP 只能监听本机地址，不能暴露到局域网或公网。")
        return host, parsed.port or 9222

    async def _connect_existing(self) -> bool:
        if self._playwright is None:
            self._playwright = await async_playwright().start()
        try:
            self._browser = await self._playwright.chromium.connect_over_cdp(
                self._cdp_url, timeout=2_000
            )
            contexts = self._browser.contexts
            if not contexts:
                raise RuntimeError("已连接 Chrome，但没有可用的浏览器上下文。")
            self._context = contexts[0]
            pages = self._context.pages
            self._page = pages[0] if pages else None
            return True
        except Exception:
            self._browser = None
            self._context = None
            self._page = None
            return False

    async def _stop_owned_chrome(self) -> None:
        process = self._chrome_process
        self._chrome_process = None
        if process and process.poll() is None:
            process.terminate()
            try:
                await asyncio.to_thread(process.wait, 5)
            except subprocess.TimeoutExpired:
                process.kill()
                await asyncio.to_thread(process.wait)

    async def _launch_chrome_and_connect(self) -> None:
        chrome_path = self._find_chrome()
        profile_dir = Path(
            self._user_data_dir
            or (Path(os.environ.get("LOCALAPPDATA", str(Path.home()))) / "ChromeAutomationTest")
        ).expanduser().resolve()
        profile_dir.mkdir(parents=True, exist_ok=True)
        host, port = self._cdp_host_port()

        self._chrome_process = subprocess.Popen(
            [
                chrome_path,
                f"--remote-debugging-address={host}",
                f"--remote-debugging-port={port}",
                f"--user-data-dir={profile_dir}",
                "--no-first-run",
                "--no-default-browser-check",
            ],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )

        last_error: Exception | None = None
        for _ in range(self._startup_timeout_seconds * 2):
            if self._chrome_process.poll() is not None:
                raise RuntimeError(
                    "Chrome 启动后立即退出。请检查专用配置目录或调试端口是否被占用。"
                )
            try:
                if await self._connect_existing():
                    return
            except Exception as exc:
                last_error = exc
            await asyncio.sleep(0.5)

        raise RuntimeError(
            f"Chrome 已启动，但在 {self._startup_timeout_seconds} 秒内未能连接 CDP 接口：{last_error}"
        )

    async def ensure_connected(self) -> None:
        """确保浏览器连接可用；浏览器被关闭后，在下一次请求时自动重启。"""
        if self._browser and self._browser.is_connected() and self._context:
            return

        # 清理已失效的连接。若之前由本服务启动的 Chrome 仍残留，则先结束它。
        self._browser = None
        self._context = None
        self._page = None
        await self._stop_owned_chrome()

        if self._playwright is None:
            self._playwright = await async_playwright().start()

        if not await self._connect_existing():
            await self._launch_chrome_and_connect()

    async def get_page(self, timeout_ms: int = 20_000) -> Page:
        """获取可复用页面；浏览器或页面关闭后自动恢复。"""
        await self.ensure_connected()
        if self._context is None:
            raise RuntimeError("Chrome 尚未连接，无法创建页面。")
        if self._page is None or self._page.is_closed():
            self._page = await self._context.new_page()
        self._page.set_default_timeout(timeout_ms)
        return self._page

    def status(self) -> dict:
        connected = bool(
            self._browser
            and self._browser.is_connected()
            and self._context is not None
        )
        page_open = bool(self._page and not self._page.is_closed())
        return {
            "browser_connected": connected,
            "page_open": page_open,
            "chrome_process_owned": bool(
                self._chrome_process and self._chrome_process.poll() is None
            ),
            "cdp_url": self._cdp_url,
        }

    async def close(self) -> None:
        """停止服务时只断开 Playwright，不关闭 Chrome，以保留浏览器登录会话。"""
        if self._playwright:
            await self._playwright.stop()
        self._playwright = None
        self._browser = None
        self._context = None
        self._page = None
        # 故意不调用 _stop_owned_chrome()：
        # Chrome 使用固定 user_data_dir，留在后台可让下次启动服务时复用原登录态。
        # 用户仍可手动关闭专用 Chrome；若 Chrome 被关闭，下次请求会自动重新启动。
        self._chrome_process = None


browser_manager = BrowserManager()
