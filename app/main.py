import re
import shutil
import tempfile
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, File, Form, HTTPException, Query, UploadFile
from playwright.async_api import Error as PlaywrightError

from app.browser import browser_manager
from app.action_pacing import wait_between_actions
from app.config import settings


@asynccontextmanager
async def lifespan(app: FastAPI):
    # 服务启动时只初始化 Playwright，不主动启动或弹出 Chrome。
    await browser_manager.start(
        cdp_url=settings.browser_cdp_url,
        user_data_dir=settings.browser_user_data_dir,
    )
    yield
    await browser_manager.close()



app = FastAPI(title=settings.app_name, version="1.0.0", lifespan=lifespan)


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
    """从主页面左侧菜单进入“我的产品”，再打开“产品订购申请”。"""
    # DOM 已确认“我的产品”是主页面中的 Ant Design 菜单 li，
    # 且具有稳定的 title 属性；不再用跨 Frame 文本搜索定位该菜单。
    my_products = page.locator('li.ant-menu-item[title="我的产品"]')
    await my_products.first.wait_for(state="visible", timeout=timeout_ms)
    my_products_count = await my_products.count()
    if my_products_count != 1:
        return False, f"主页面中找到 {my_products_count} 个“我的产品”菜单项，无法安全确定点击目标。"

    await my_products.click(timeout=timeout_ms)
    await wait_between_actions()

    # 点击菜单后业务内容可能异步加载到 Frame，继续跨 Frame 等待目标入口。
    deadline_ms = 25_000
    elapsed_ms = 0
    while elapsed_ms < deadline_ms:
        target_frame, target = await find_unique_visible_text(page, "产品订购申请")
        if target_frame is not None:
            await wait_between_actions()
            await target.click(timeout=timeout_ms)
            return True, "已点击主页面“我的产品”，并打开“产品订购申请”。"
        if target > 1:
            return False, "“产品订购申请”出现多个匹配入口，暂未点击，避免误选。"
        await page.wait_for_timeout(500)
        elapsed_ms += 500

    # 将实际页面状态带回接口响应，便于下一轮根据真实页面结构定位，
    # 不输出 URL 查询参数，避免泄露会话信息。
    frame_summary = []
    for frame in page.frames:
        try:
            text = (await frame.locator("body").inner_text(timeout=2_000)).strip()
            frame_summary.append(f"{frame.name or '主页面'}: {text[:180]}")
        except PlaywrightError:
            frame_summary.append(frame.name or "页面 Frame")
    summary = "；".join(frame_summary)[:700]
    return False, f"已点击“我的产品”，但等待 25 秒仍未找到“产品订购申请”入口。当前页面内容：{summary}"


@app.get("/api/health")
async def health():
    """接口用途：供部署平台或上游 Agent 检查 FastAPI 服务是否正常运行。"""
    return {"ok": True, "service": settings.app_name}


@app.get("/api/browser/status")
async def get_browser_status():
    """接口用途：查看专用 Chrome/CDP 连接状态；不会主动启动浏览器。"""
    return browser_manager.status()


@app.post("/api/product-order/batch-import")
async def receive_batch_import_task(
    task_id: str = Form(..., min_length=1, max_length=64, description="上游 Agent 生成的唯一任务编号。"),
    username: str = Form(..., min_length=1, max_length=100, description="运营商系统登录账号，仅用于本次任务。"),
    password: str = Form(..., min_length=1, description="运营商系统登录密码，仅用于本次任务，不保存。"),
    si_code: str = Form(..., min_length=1, max_length=100, description="预期 SI 编码，用于后续页面校验。"),
    product_name: str = Form(..., min_length=1, max_length=200, description="预期产品名称，用于后续页面校验。"),
    business_name: str = Form(..., min_length=1, max_length=200, description="预期业务名称，用于后续页面校验。"),
    excel_file: UploadFile = File(..., description="批量导入 Excel 文件。"),
    attachment_file: UploadFile = File(..., description="业务附件文件。"),
):
    """接口用途：接收上游 Agent 的完整订购任务，安全暂存两个文件，并登录进入产品订购申请页面。
    当前版本只完成任务接收和页面准备，不会上传文件到运营商或点击业务确认。
    """
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", task_id):
        raise HTTPException(status_code=400, detail="task_id 仅允许字母、数字、下划线和短横线。")

    allowed_excel = {".xlsx", ".xls"}
    allowed_attachment = {".pdf", ".doc", ".docx", ".xls", ".xlsx", ".zip", ".rar", ".jpg", ".jpeg", ".png"}
    excel_suffix = Path(excel_file.filename or "").suffix.lower()
    attachment_suffix = Path(attachment_file.filename or "").suffix.lower()
    if excel_suffix not in allowed_excel:
        raise HTTPException(status_code=400, detail="批量导入文件仅支持 .xlsx 或 .xls 格式。")
    if attachment_suffix not in allowed_attachment:
        raise HTTPException(status_code=400, detail="附件格式暂不支持，请使用 PDF、Office、压缩包或常见图片格式。")

    task_dir = Path(tempfile.gettempdir()) / "yunyingxw_tasks" / task_id
    if task_dir.exists():
        raise HTTPException(status_code=409, detail="该 task_id 已存在，请使用新的唯一任务编号。")
    task_dir.mkdir(parents=True, exist_ok=False)

    async def save_upload(upload: UploadFile, destination: Path) -> int:
        """分块保存上游文件并限制单个文件大小，避免一次性读入内存。"""
        max_file_size = 50 * 1024 * 1024
        total = 0
        try:
            with destination.open("wb") as output:
                while True:
                    chunk = await upload.read(1024 * 1024)
                    if not chunk:
                        break
                    total += len(chunk)
                    if total > max_file_size:
                        raise HTTPException(status_code=413, detail="单个上传文件不能超过 50 MB。")
                    output.write(chunk)
        finally:
            await upload.close()
        if total == 0:
            raise HTTPException(status_code=400, detail="不允许上传空文件。")
        return total

    try:
        excel_path = task_dir / ("批量导入" + excel_suffix)
        attachment_path = task_dir / ("业务附件" + attachment_suffix)
        excel_size = await save_upload(excel_file, excel_path)
        attachment_size = await save_upload(attachment_file, attachment_path)

        # 一个业务任务独占浏览器操作锁，避免不同任务交叉操作同一 Chrome 页面。
        async with browser_manager.operation_lock:
            page = await browser_manager.get_page(settings.browser_timeout_ms)
            if page.url == "about:blank" or "10.253.61.35" not in page.url:
                await page.goto(
                    "http://10.253.61.35/cmopchannel/login",
                    wait_until="domcontentloaded",
                    timeout=settings.browser_timeout_ms,
                )

            if "login" in page.url.lower():
                await page.locator("#loginNumber").fill(username)
                await wait_between_actions()
                await page.locator("#password").fill(password)
                await wait_between_actions()
                await page.locator('button[type="submit"]').click()
                try:
                    await page.wait_for_url(
                        lambda url: "login" not in str(url).lower(),
                        timeout=15_000,
                    )
                except PlaywrightError:
                    pass

            if "login" in page.url.lower():
                raise HTTPException(status_code=401, detail="登录未成功，页面仍停留在登录页；请检查账号或完成页面验证。")

            opened, message = await open_product_order_application(
                page, settings.browser_timeout_ms
            )
            if not opened:
                raise HTTPException(status_code=502, detail=message)

        # 不返回本机绝对路径，也不保存账号密码；后续上传阶段通过 task_id 找到暂存文件。
        return {
            "task_id": task_id,
            "status": "files_received_page_ready",
            "message": "任务信息和两个文件已接收，浏览器已进入产品订购申请页面；尚未向运营商上传或提交业务。",
            "files": {
                "excel": {"filename": excel_file.filename, "size_bytes": excel_size},
                "attachment": {"filename": attachment_file.filename, "size_bytes": attachment_size},
            },
            "product_info": {
                "si_code": si_code,
                "product_name": product_name,
                "business_name": business_name,
            },
        }
    except HTTPException:
        shutil.rmtree(task_dir, ignore_errors=True)
        raise
    except (PlaywrightError, FileNotFoundError, RuntimeError, ValueError) as exc:
        shutil.rmtree(task_dir, ignore_errors=True)
        raise HTTPException(status_code=502, detail="任务接收或页面准备失败：" + str(exc)[:400]) from exc
    except Exception:
        shutil.rmtree(task_dir, ignore_errors=True)
        raise


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
