from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Query
from playwright.async_api import Error as PlaywrightError

from app.browser import browser_manager
from app.config import settings
from app.schemas import (
    BrowserOpenRequest,
    BrowserOpenResponse,
    SiteCheckRequest,
    SiteCheckResponse,
    LoginRequest,
    LoginResponse,
)


@asynccontextmanager
async def lifespan(app: FastAPI):
    # 服务启动时只初始化 Playwright，不主动启动或弹出 Chrome。
    await browser_manager.start(
        cdp_url=settings.browser_cdp_url,
        user_data_dir=settings.browser_user_data_dir,
    )
    yield
    await browser_manager.close()



app = FastAPI(title=settings.app_name, version="0.8.0", lifespan=lifespan)


async def find_unique_visible_text(page, text):
    """跨主页面和所有 Frame 查找唯一可见的精确文本，避免误点重复入口。"""
    matches = []
    for frame in page.frames:
        try:
            locator = frame.get_by_text(text, exact=True)
            count = await locator.count()
            for index in range(count):
                item = locator.nth(index)
                if await item.is_visible():
                    matches.append((frame, item))
        except PlaywrightError:
            continue
    if len(matches) == 1:
        return matches[0]
    return None, len(matches)


async def open_product_order_application(page, timeout_ms: int):
    """从“我的产品”进入并打开“产品订购申请”。"""
    my_products_frame, my_products = await find_unique_visible_text(page, "我的产品")
    if my_products_frame is None:
        if my_products == 0:
            return False, "没有找到“我的产品”入口，请确认登录后的页面已加载。"
        return False, "页面中存在多个“我的产品”匹配项，无法安全确定点击目标。"

    await my_products.click(timeout=timeout_ms)

    # 这是单页应用/嵌套 Frame 页面，不依赖整页跳转，轮询等待业务入口渲染。
    deadline_ms = 25_000
    elapsed_ms = 0
    while elapsed_ms < deadline_ms:
        target_frame, target = await find_unique_visible_text(page, "产品订购申请")
        if target_frame is not None:
            await target.click(timeout=timeout_ms)
            return True, "已点击“我的产品”并打开“产品订购申请”。"
        if target > 1:
            return False, "“产品订购申请”出现多个匹配入口，暂未点击，避免误选产品。"
        await page.wait_for_timeout(500)
        elapsed_ms += 500

    return False, "已点击“我的产品”，但等待 25 秒仍未找到“产品订购申请”入口。"


@app.get("/api/health")
async def health():
    return {"ok": True, "service": settings.app_name}


@app.get("/api/browser/status")
async def get_browser_status():
    """查看浏览器状态；此接口不会为了检查状态而启动 Chrome。"""
    return browser_manager.status()


@app.post("/api/browser/open", response_model=BrowserOpenResponse)
async def open_browser_page(payload: BrowserOpenRequest):
    """按需启动或恢复专用 Chrome，并打开指定网页。"""
    async with browser_manager.operation_lock:
        try:
            page = await browser_manager.get_page(settings.browser_timeout_ms)
            response = await page.goto(
                str(payload.url),
                wait_until="domcontentloaded",
                timeout=settings.browser_timeout_ms,
            )
            return BrowserOpenResponse(
                status="opened",
                message="网页已在专用 Chrome 中打开。",
                final_url=page.url,
                page_title=await page.title(),
                http_status=response.status if response else None,
            )
        except PlaywrightError as exc:
            raise HTTPException(
                status_code=502,
                detail=f"浏览器已启动或正在恢复，但打开网页失败：{str(exc)[:500]}",
            ) from exc
        except (FileNotFoundError, RuntimeError, ValueError) as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc


@app.post("/api/login", response_model=LoginResponse)
async def login(payload: LoginRequest):
    """登录运营商系统，并自动进入“我的产品 -> 产品订购申请”。"""
    async with browser_manager.operation_lock:
        try:
            page = await browser_manager.get_page(settings.browser_timeout_ms)

            # 新启动的专用 Chrome 通常是 about:blank，需要先打开登录页；
            # 若当前已在运营商系统内，则复用现有登录会话。
            if page.url == "about:blank" or "10.253.61.35" not in page.url:
                await page.goto(
                    "http://10.253.61.35/cmopchannel/login",
                    wait_until="domcontentloaded",
                    timeout=settings.browser_timeout_ms,
                )

            if "login" in page.url.lower():
                account_input = page.locator("#loginNumber")
                password_input = page.locator("#password")
                await account_input.wait_for(state="visible")
                await password_input.wait_for(state="visible")
                await account_input.fill(payload.username)
                await password_input.fill(payload.password.get_secret_value())
                await page.locator('button[type="submit"]').click()

                try:
                    await page.wait_for_url(
                        lambda url: "login" not in str(url).lower(),
                        timeout=15_000,
                    )
                except PlaywrightError:
                    pass

            final_url = page.url
            title = await page.title()
            if "login" in final_url.lower():
                body_text = (await page.locator("body").inner_text())[:2000]
                if any(word in body_text for word in ("验证码", "短信", "验证", "安全校验")):
                    return LoginResponse(
                        status="verification_required",
                        message="页面仍停留在登录页，可能需要完成验证码或其他人工验证。",
                        final_url=final_url,
                        page_title=title,
                        current_step="登录验证",
                    )
                return LoginResponse(
                    status="login_failed",
                    message="登录后仍停留在登录页，请检查账号密码或页面提示。",
                    final_url=final_url,
                    page_title=title,
                    current_step="登录",
                )

            # 登录成功后自动进入产品订购申请入口。
            try:
                opened, message = await open_product_order_application(
                    page, settings.browser_timeout_ms
                )
            except PlaywrightError as exc:
                opened, message = False, f"自动点击页面入口失败：{str(exc)[:300]}"

            return LoginResponse(
                status="login_success" if opened else "navigation_failed",
                message=message,
                final_url=page.url,
                page_title=await page.title(),
                current_step="产品订购申请" if opened else "进入产品订购申请失败",
            )
        except PlaywrightError as exc:
            raise HTTPException(
                status_code=502,
                detail=f"自动化登录或页面导航失败：{str(exc)[:300]}",
            ) from exc
        except (FileNotFoundError, RuntimeError, ValueError) as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc


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
        description="是否返回完整 HTML。完整 HTML 可能包含业务数据，默认关闭。",
    ),
):
    """读取当前页面及所有 Frame 的可见文本、交互元素和页面容器。"""
    from urllib.parse import urlsplit, urlunsplit

    def safe_frame_url(raw_url: str) -> str:
        # Frame URL 可能在 query/hash 中携带 token、operId 等会话参数，只返回来源和路径。
        try:
            parsed = urlsplit(raw_url)
            return urlunsplit((parsed.scheme, parsed.netloc, parsed.path, "", ""))
        except Exception:
            return raw_url.split("?", 1)[0].split("#", 1)[0]

    async with browser_manager.operation_lock:
        page = await browser_manager.get_page(settings.browser_timeout_ms)

        async def inspect_frame(frame):
            result = await frame.evaluate(
                """() => {
                    const cleanText = (value, limit = 180) =>
                        (value || '').replace(/\\s+/g, ' ').trim().slice(0, limit);
                    const visible = el => {
                        const style = getComputedStyle(el);
                        const rect = el.getBoundingClientRect();
                        return style.display !== 'none' &&
                            style.visibility !== 'hidden' &&
                            Number(style.opacity) !== 0 &&
                            rect.width > 0 && rect.height > 0;
                    };
                    const selectorFor = el => {
                        if (el.id) return '#' + CSS.escape(el.id);
                        const testId = el.getAttribute('data-testid');
                        if (testId) return '[data-testid="' + CSS.escape(testId) + '"]';
                        const parts = [];
                        let node = el;
                        while (node && node.nodeType === 1 && parts.length < 5) {
                            let part = node.tagName.toLowerCase();
                            const cls = [...node.classList].filter(Boolean).slice(0, 2);
                            if (cls.length) part += '.' + cls.map(c => CSS.escape(c)).join('.');
                            const parent = node.parentElement;
                            if (parent) {
                                const same = [...parent.children].filter(x => x.tagName === node.tagName);
                                if (same.length > 1) part += ':nth-of-type(' + (same.indexOf(node) + 1) + ')';
                            }
                            parts.unshift(part);
                            if (node.id) break;
                            node = parent;
                        }
                        return parts.join(' > ');
                    };
                    const describe = (el, index) => ({
                        index,
                        tag: el.tagName.toLowerCase(),
                        type: el.getAttribute('type'),
                        id: el.id || null,
                        name: el.getAttribute('name'),
                        class_name: cleanText(typeof el.className === 'string' ? el.className : ''),
                        role: el.getAttribute('role'),
                        text: cleanText(el.innerText || el.getAttribute('aria-label') || el.getAttribute('title')),
                        aria_label: el.getAttribute('aria-label'),
                        title: el.getAttribute('title'),
                        href: el.tagName === 'A' ? el.getAttribute('href') : null,
                        placeholder: el.getAttribute('placeholder'),
                        disabled: Boolean(el.disabled),
                        visible: visible(el),
                        selector: selectorFor(el)
                    });

                    const candidates = [...document.querySelectorAll(
                        'a,button,input,textarea,select,[role="button"],[role="link"],[role="menuitem"],[role="tab"],[onclick],[tabindex]'
                    )].filter(visible);
                    const unique = [...new Set(candidates)];
                    const interactive = unique.slice(0, 400).map(describe);

                    const controls = [...document.querySelectorAll(
                        'input,textarea,select,button,[role="button"],[role="combobox"]'
                    )].map(describe).slice(0, 300);

                    const containers = [...document.querySelectorAll(
                        'main,article,section,[class*="card"],[class*="product"],[class*="item"],[class*="panel"],[role="main"]'
                    )].filter(visible).map((el, index) => ({
                        index,
                        tag: el.tagName.toLowerCase(),
                        id: el.id || null,
                        class_name: cleanText(typeof el.className === 'string' ? el.className : ''),
                        text: cleanText(el.innerText, 500),
                        selector: selectorFor(el)
                    })).filter(item => item.text).slice(0, 150);

                    const bodyText = document.body ? cleanText(document.body.innerText, 20000) : '';
                    return {
                        title: document.title || '',
                        document_ready_state: document.readyState,
                        body_text: bodyText,
                        visible_text_length: bodyText.length,
                        controls,
                        control_count: controls.length,
                        interactive_elements: interactive,
                        interactive_count: unique.length,
                        containers,
                        container_count: containers.length
                    };
                }"""
            )
            return {
                "frame_url": safe_frame_url(frame.url),
                "frame_name": frame.name,
                "title": result["title"],
                "document_ready_state": result["document_ready_state"],
                "body_text": result["body_text"],
                "visible_text_length": result["visible_text_length"],
                "controls": result["controls"],
                "control_count": result["control_count"],
                "interactive_elements": result["interactive_elements"],
                "interactive_count": result["interactive_count"],
                "containers": result["containers"],
                "container_count": result["container_count"],
                # 完整 HTML 仅在显式请求时返回；常规采集不会暴露原始页面源码。
                "html": await frame.content() if include_html else None,
            }

        frames = []
        for frame in page.frames:
            try:
                frames.append(await inspect_frame(frame))
            except PlaywrightError as exc:
                frames.append({
                    "frame_url": safe_frame_url(frame.url),
                    "frame_name": frame.name,
                    "controls": [],
                    "control_count": 0,
                    "interactive_elements": [],
                    "interactive_count": 0,
                    "containers": [],
                    "container_count": 0,
                    "body_text": "",
                    "html": None,
                    "error": str(exc)[:300],
                })

        main_frame = frames[0] if frames else None
        return {
            "url": safe_frame_url(page.url),
            "title": await page.title(),
            "frame_count": len(page.frames),
            "iframe_count": max(len(page.frames) - 1, 0),
            "frames": frames,
            "html_included": include_html,
            "html": main_frame["html"] if main_frame else None,
            "message": (
                "已读取当前页面及所有 Frame 的可见文本、控件、可交互元素和页面容器；不会读取输入框 value，Frame URL 中的查询参数和片段已脱敏。"
            ),
        }
