import asyncio
import sys

import uvicorn


if __name__ == "__main__":
    # Playwright needs subprocess support; use ProactorEventLoop on Windows.
    if sys.platform == "win32":
        asyncio.set_event_loop_policy(
            asyncio.WindowsProactorEventLoopPolicy()
        )

    uvicorn.run(
        "app.main:app",
        host="127.0.0.1",
        port=8000,
        reload=False,
    )
