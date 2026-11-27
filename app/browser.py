import asyncio
import os
import subprocess
from pathlib import Path

from playwright.async_api import async_playwright, Browser, BrowserContext, Page, Playwright


class BrowserManager:
    """负责启动专用 Chrome，并通过 CDP 接管浏览器。"""

    def __init__(self) -> None:
        self._playwright: Playwright | None = None
        self._browser: Browser | None = None
        self._context: BrowserContext | None = None
        self._page: Page | None = None
        self._chrome_process: subprocess.Popen | None = None
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
        if self._context:
            return

        self._playwright = await async_playwright().start()
        try:
            # 先尝试连接已运行的专用 Chrome；若不存在，再由程序自动启动。
            try:
                self._browser = await self._playwright.chromium.connect_over_cdp(
                    cdp_url, timeout=2_000
                )
            except Exception:
                chrome_path = self._find_chrome()
                profile_dir = Path(
                    user_data_dir
                    or (Path(os.environ.get("LOCALAPPDATA", Path.home())) / "ChromeAutomationTest")
                ).expanduser().resolve()
                profile_dir.mkdir(parents=True, exist_ok=True)

                self._chrome_process = subprocess.Popen(
                    [
                        chrome_path,
                        "--remote-debugging-address=127.0.0.1",
                        "--remote-debugging-port=9222",
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
                for _ in range(startup_timeout_seconds * 2):
                    if self._chrome_process.poll() is not None:
                        raise RuntimeError(
                            "Chrome 启动后立即退出。请检查是否已有其他 Chrome 占用了专用配置目录或调试端口。"
                        )
                    try:
                        self._browser = await self._playwright.chromium.connect_over_cdp(
                            cdp_url, timeout=1_500
                        )
                        break
                    except Exception as exc:
                        last_error = exc
                        await asyncio.sleep(0.5)
                else:
                    raise RuntimeError(
                        f"Chrome 已启动，但在 {startup_timeout_seconds} 秒内未能连接 CDP 接口：{last_error}"
                    )

            contexts = self._browser.contexts
            if not contexts:
                raise RuntimeError("已连接 Chrome，但没有可用的浏览器上下文。")
            self._context = contexts[0]
            pages = self._context.pages
            self._page = pages[0] if pages else await self._context.new_page()

        except Exception:
            if self._playwright:
                await self._playwright.stop()
            self._playwright = None
            self._browser = None
            self._context = None
            self._page = None
            if self._chrome_process and self._chrome_process.poll() is None:
                self._chrome_process.terminate()
            self._chrome_process = None
            raise

    async def get_page(self, timeout_ms: int = 20_000) -> Page:
        """获取可复用的页面；页面关闭或失效时才创建新页面。"""
        if not self._context:
            raise RuntimeError("Chrome 尚未启动或连接，请检查自动化服务启动日志。")
        if self._page is None or self._page.is_closed():
            self._page = await self._context.new_page()
        self._page.set_default_timeout(timeout_ms)
        return self._page

    async def close(self) -> None:
        """断开 Playwright，并仅关闭由本服务启动的 Chrome 进程。"""
        if self._playwright:
            await self._playwright.stop()
        self._playwright = None
        self._browser = None
        self._context = None
        self._page = None

        if self._chrome_process and self._chrome_process.poll() is None:
            self._chrome_process.terminate()
            try:
                self._chrome_process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self._chrome_process.kill()
        self._chrome_process = None


browser_manager = BrowserManager()
