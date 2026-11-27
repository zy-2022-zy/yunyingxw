import asyncio
import sys

import uvicorn

from app.config import settings


if __name__ == "__main__":
    # Playwright needs subprocess support; use ProactorEventLoop on Windows.
    if sys.platform == "win32":
        asyncio.set_event_loop_policy(
            asyncio.WindowsProactorEventLoopPolicy()
        )

    uvicorn.run(
        "app.main:app",
        host=settings.app_host,
        port=settings.app_port,
        reload=False,
    )
