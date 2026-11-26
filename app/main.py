from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException
from playwright.async_api import Error as PlaywrightError

from app.browser import browser_manager
from app.config import settings
from app.schemas import SiteCheckRequest, SiteCheckResponse


@asynccontextmanager
async def lifespan(app: FastAPI):
    await browser_manager.start(headless=settings.browser_headless)
    yield
    await browser_manager.close()


app = FastAPI(title=settings.app_name, version="0.1.0", lifespan=lifespan)


@app.get("/api/health")
async def health():
    return {"ok": True, "service": settings.app_name}


@app.post("/api/site/check", response_model=SiteCheckResponse)
async def check_site(payload: SiteCheckRequest):
    """通过本机浏览器访问目标站点，验证当前 VPN/网络是否可达。"""
    page = await browser_manager.new_page(settings.browser_timeout_ms)
    try:
        response = await page.goto(
            str(payload.url),
            wait_until="domcontentloaded",
            timeout=settings.browser_timeout_ms,
        )
        title = await page.title()
        final_url = page.url
        status_code = response.status if response else None

        if status_code is not None and status_code >= 500:
            return SiteCheckResponse(
                operator=payload.operator,
                status="site_error",
                reachable=True,
                message="网络已到达目标网站，但网站服务器返回错误。",
                final_url=final_url,
                page_title=title,
                http_status=status_code,
            )

        if status_code in (401, 403) or any(
            marker in final_url.lower()
            for marker in ("login", "signin", "sso")
        ):
            return SiteCheckResponse(
                operator=payload.operator,
                status="login_required",
                reachable=True,
                message="目标网站可以访问，但当前可能尚未登录。",
                final_url=final_url,
                page_title=title,
                http_status=status_code,
            )

        if payload.expected_text:
            body_text = await page.locator("body").inner_text()
            if payload.expected_text not in body_text:
                return SiteCheckResponse(
                    operator=payload.operator,
                    status="page_unexpected",
                    reachable=True,
                    message="网站已打开，但没有找到预期页面内容，请检查页面是否加载完整。",
                    final_url=final_url,
                    page_title=title,
                    http_status=status_code,
                    detail="未找到配置的 expected_text",
                )

        return SiteCheckResponse(
            operator=payload.operator,
            status="reachable",
            reachable=True,
            message="目标网站已成功打开，可以继续后续自动化操作。",
            final_url=final_url,
            page_title=title,
            http_status=status_code,
        )

    except PlaywrightError as exc:
        message = str(exc)
        lowered = message.lower()
        if any(term in lowered for term in (
            "name_not_resolved",
            "err_name_not_resolved",
            "connection_refused",
            "err_connection",
            "timed out",
            "timeout",
            "net::err",
        )):
            status = "vpn_or_network_unavailable"
            friendly = "暂时无法访问目标网站。请检查 VPN 是否已连接，以及当前网络是否允许访问该运营商系统。"
        else:
            status = "site_error"
            friendly = "访问目标网站时发生浏览器错误，请检查网址或稍后重试。"

        return SiteCheckResponse(
            operator=payload.operator,
            status=status,
            reachable=False,
            message=friendly,
            final_url=page.url,
            detail=message[:1000],
        )
    finally:
        await page.close()


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("app.main:app", host=settings.app_host, port=settings.app_port, reload=True)
