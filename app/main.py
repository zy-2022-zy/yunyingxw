from contextlib import asynccontextmanager

from fastapi import FastAPI, Query
from playwright.async_api import Error as PlaywrightError

from app.browser import browser_manager
from app.config import settings
from app.schemas import SiteCheckRequest, SiteCheckResponse


@asynccontextmanager
async def lifespan(app: FastAPI):
    await browser_manager.start(
        headless=settings.browser_headless,
        channel=settings.browser_channel,
        user_data_dir=settings.browser_user_data_dir,
    )
    yield
    await browser_manager.close()


app = FastAPI(title=settings.app_name, version="0.5.0", lifespan=lifespan)


@app.get("/api/health")
async def health():
    return {"ok": True, "service": settings.app_name}


@app.post("/api/site/check", response_model=SiteCheckResponse)
async def check_site(payload: SiteCheckRequest):
    """通过持久化的本机浏览器页面访问目标站点，验证当前 VPN/网络是否可达。"""
    async with browser_manager.operation_lock:
        page = await browser_manager.get_page(settings.browser_timeout_ms)
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

            if status_code is not None and 400 <= status_code < 500:
                if status_code in (401, 403) or any(
                    marker in final_url.lower()
                    for marker in ("login", "signin", "sso")
                ):
                    status = "login_required"
                    message = "目标网站可以访问，但当前可能尚未登录或无权访问。"
                else:
                    status = "http_error"
                    message = f"浏览器已连接到目标网站，但网站返回 HTTP {status_code}，页面不能视为正常打开。"

                return SiteCheckResponse(
                    operator=payload.operator,
                    status=status,
                    reachable=True,
                    message=message,
                    final_url=final_url,
                    page_title=title,
                    http_status=status_code,
                    detail=f"目标网站返回 HTTP {status_code}",
                )

            if any(marker in final_url.lower() for marker in ("login", "signin", "sso")):
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


@app.get("/api/page/dom")
async def get_page_dom(
    include_html: bool = Query(
        default=False,
        description="是否同时返回完整 HTML。完整 HTML 可能包含页面中的业务数据，默认关闭。",
    ),
):
    """读取当前持久化页面的 DOM 摘要、交互控件和 iframe 信息。"""
    async with browser_manager.operation_lock:
        page = await browser_manager.get_page(settings.browser_timeout_ms)

        async def inspect_frame(frame):
            # 不读取输入框 value，避免把用户填写的账号、手机号等内容返回给调用方。
            controls = await frame.locator(
                "input, textarea, select, button, [role='button'], [role='combobox']"
            ).evaluate_all(
                """elements => elements.map((el, index) => ({
                    index,
                    tag: el.tagName.toLowerCase(),
                    type: el.getAttribute('type'),
                    name: el.getAttribute('name'),
                    id: el.id || null,
                    placeholder: el.getAttribute('placeholder'),
                    aria_label: el.getAttribute('aria-label'),
                    text: (el.innerText || el.getAttribute('value') || '').trim().slice(0, 120),
                    disabled: Boolean(el.disabled),
                    required: Boolean(el.required)
                }))"""
            )
            return {
                "frame_url": frame.url,
                "frame_name": frame.name,
                "controls": controls,
                "control_count": len(controls),
                "html": await frame.content() if include_html else None,
            }

        frames = []
        for frame in page.frames:
            try:
                frames.append(await inspect_frame(frame))
            except PlaywrightError as exc:
                frames.append({
                    "frame_url": frame.url,
                    "frame_name": frame.name,
                    "controls": [],
                    "control_count": 0,
                    "html": None,
                    "error": str(exc)[:300],
                })

        main_frame = frames[0] if frames else None
        return {
            "url": page.url,
            "title": await page.title(),
            "frame_count": len(page.frames),
            "iframe_count": max(len(page.frames) - 1, 0),
            "frames": frames,
            "html_included": include_html,
            "html": main_frame["html"] if main_frame else None,
            "message": (
                "已读取当前页面 DOM。输入框当前值不会被读取。"
                if not include_html
                else "已读取当前页面 DOM 和完整 HTML；请注意 HTML 可能包含页面业务数据。"
            ),
        }
