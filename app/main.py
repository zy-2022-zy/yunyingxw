import json
import shutil
import uuid
from urllib.parse import urlsplit

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


def safe_original_name(raw: str | None, fallback: str) -> str:
    """保留上游原始文件名并去掉路径和非法字符；弹窗和业务列表会展示该文件名。"""
    name = Path(raw or "").name.strip()
    name = "".join(ch for ch in name if ch not in '\\/:*?"<>|\r\n\t')
    name = name.strip()
    return (name[:120]) if name else fallback


async def find_adcesop_frame(page):
    """定位业务系统 iframe（ADC 承载批量导入页面）。"""
    for frame in page.frames:
        if "/adcesop" in frame.url:
            return frame
    return None


async def submit_batch_import_popup(page, excel_path: Path, attachment_path: Path, timeout_ms: int):
    """在产品订购申请页面点击“批量导入订购申请”，上传 Excel 与附件并点击确认。

    页面结构来自 /api/page/dom 实测：
    - 业务内容在 /adcesop iframe 内，入口为文本“批量导入订购申请”的按钮；
    - 弹窗标题“批量新增产品订购申请”，内含两个 input[type=file]（Excel 在前、附件在后）；
    - 底部为“确 认”主按钮和“取 消”按钮。
    """
    frame = await find_adcesop_frame(page)
    if frame is None:
        return False, "未找到业务系统 iframe（/adcesop），无法执行批量导入。"

    # 1. 点击列表页的“批量导入订购申请”入口按钮。
    entry = frame.get_by_role("button", name="批量导入订购申请")
    try:
        await entry.wait_for(state="visible", timeout=timeout_ms)
    except PlaywrightError:
        return False, "产品订购申请列表页上未找到“批量导入订购申请”按钮。"
    entry_count = await entry.count()
    if entry_count != 1:
        return False, f"“批量导入订购申请”按钮匹配到 {entry_count} 个元素，避免误点已中止。"
    await entry.click(timeout=timeout_ms)
    await wait_between_actions()

    # 2. 等待“批量新增产品订购申请”弹窗出现；hash 类名不稳定，用标题文本定位。
    modal = frame.locator(".ant-modal", has=frame.get_by_text("批量新增产品订购申请", exact=True))
    try:
        await modal.wait_for(state="visible", timeout=timeout_ms)
    except PlaywrightError:
        return False, "点击入口按钮后未出现“批量新增产品订购申请”弹窗。"

    # 3. 填入两个文件：第一个是批量导入 Excel，第二个是附件。
    file_inputs = modal.locator('input[type="file"]')
    input_count = await file_inputs.count()
    if input_count < 2:
        return False, f"弹窗内只找到 {input_count} 个文件输入框，预期 2 个（Excel 和附件）。"
    try:
        await file_inputs.nth(0).set_input_files(str(excel_path), timeout=timeout_ms)
        await wait_between_actions()
        await file_inputs.nth(1).set_input_files(str(attachment_path), timeout=timeout_ms)
    except PlaywrightError as exc:
        return False, "向弹窗文件输入框写入文件失败：" + str(exc)[:300]

    # 4. 等待两个文件都上传完成：antd 的“未选择任何文件”提示消失、文件名出现在弹窗内。
    deadline_ms = 60_000
    elapsed_ms = 0
    while elapsed_ms < deadline_ms:
        modal_text = await modal.inner_text()
        if "未选择任何文件" not in modal_text:
            break
        await page.wait_for_timeout(500)
        elapsed_ms += 500
    else:
        return False, "等待 60 秒后弹窗仍显示“未选择任何文件”，文件可能上传失败。"

    # 确认弹窗中能同时看到两个文件名，避免只传成功一个就提交。
    if excel_path.name not in await modal.inner_text():
        return False, f"弹窗中未看到 Excel 文件名（{excel_path.name}），上传结果未知，已中止提交。"
    if attachment_path.name not in await modal.inner_text():
        return False, f"弹窗中未看到附件文件名（{attachment_path.name}），上传结果未知，已中止提交。"
    await wait_between_actions()

    # 5. 点击弹窗底部“确 认”按钮提交。
    confirm = modal.locator(".ant-modal-footer button", has_text="确 认")
    confirm_count = await confirm.count()
    if confirm_count != 1:
        return False, f"弹窗底部“确 认”按钮匹配到 {confirm_count} 个元素，避免误点已中止。"
    await confirm.click(timeout=timeout_ms)

    # 6. 等待弹窗关闭，并收集页面提示（成功或错误信息）。
    try:
        await modal.wait_for(state="hidden", timeout=30_000)
        closed = True
    except PlaywrightError:
        closed = False
    await page.wait_for_timeout(1_000)

    notice_texts = []
    notices = frame.locator(".ant-message-notice, .ant-modal-confirm")
    try:
        notice_count = await notices.count()
        for index in range(min(notice_count, 5)):
            text = (await notices.nth(index).inner_text()).strip()
            if text:
                notice_texts.append(text[:200])
    except PlaywrightError:
        pass

    if not closed:
        detail = "；".join(notice_texts) or "弹窗未关闭，可能存在校验错误。"
        return False, "点击确认后 30 秒弹窗仍未关闭：" + detail[:400]

    message = "已上传 Excel 与附件并点击确认，弹窗已关闭。"
    if notice_texts:
        message += "页面提示：" + "；".join(notice_texts)[:300]
    return True, message


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
    url: str = Form(..., min_length=1, description="运营商系统登录页或入口 URL。"),
    username: str = Form(..., min_length=1, max_length=100, description="运营商系统登录账号，仅用于本次任务。"),
    password: str = Form(..., min_length=1, description="运营商系统登录密码，仅用于本次任务，不保存。"),
    excel_file: UploadFile = File(..., description="批量导入 Excel 文件。"),
    attachment_file: UploadFile = File(..., description="业务附件文件。"),
):
    """接口用途：接收上游 Agent 传入的目标 URL、登录凭据和两个业务文件，暂存文件并打开目标登录页；
    登录后进入产品订购申请页面，点击“批量导入订购申请”，在弹窗中上传 Excel 与附件并点击确认。
    """
    parsed_url = urlsplit(url.strip())
    if parsed_url.scheme not in ("http", "https") or not parsed_url.netloc:
        raise HTTPException(status_code=400, detail="url 必须是有效的 http 或 https 地址。")

    # 上游暂不需要传 task_id，由服务端生成独立任务编号。
    task_id = uuid.uuid4().hex

    allowed_excel = {".xlsx", ".xls"}
    allowed_attachment = {".pdf", ".doc", ".docx", ".xls", ".xlsx", ".zip", ".rar", ".jpg", ".jpeg", ".png"}
    excel_suffix = Path(excel_file.filename or "").suffix.lower()
    attachment_suffix = Path(attachment_file.filename or "").suffix.lower()
    if excel_suffix not in allowed_excel:
        raise HTTPException(status_code=400, detail="批量导入文件仅支持 .xlsx 或 .xls 格式。")
    if attachment_suffix not in allowed_attachment:
        raise HTTPException(status_code=400, detail="附件格式暂不支持，请使用 PDF、Office、压缩包或常见图片格式。")

    task_dir = Path(tempfile.gettempdir()) / "yunyingxw_tasks" / task_id
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
        # 保留原始文件名：弹窗与业务列表会按文件名展示“批量导入文件名”，改名会影响业务展示。
        excel_path = task_dir / safe_original_name(excel_file.filename, "批量导入" + excel_suffix)
        attachment_path = task_dir / safe_original_name(attachment_file.filename, "业务附件" + attachment_suffix)
        excel_size = await save_upload(excel_file, excel_path)
        attachment_size = await save_upload(attachment_file, attachment_path)

        # 保存不含账号密码和敏感 URL 参数的任务元数据，供后续网页上传阶段读取。
        task_metadata = {
            "task_id": task_id,
            "target_origin": parsed_url.scheme + "://" + parsed_url.netloc,
            "excel_filename": excel_file.filename,
            "attachment_filename": attachment_file.filename,
            "excel_size_bytes": excel_size,
            "attachment_size_bytes": attachment_size,
            "status": "files_received",
        }
        (task_dir / "task.json").write_text(
            json.dumps(task_metadata, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

        # 一个业务任务独占浏览器操作锁，避免不同任务交叉操作同一 Chrome 页面。
        async with browser_manager.operation_lock:
            page = await browser_manager.get_page(settings.browser_timeout_ms)

            # 每个任务都使用上游传入的 URL 打开目标登录页/系统入口。
            await page.goto(
                url.strip(),
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
                        lambda current_url: "login" not in str(current_url).lower(),
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

            submitted, upload_message = await submit_batch_import_popup(
                page, excel_path, attachment_path, settings.browser_timeout_ms
            )
            if not submitted:
                raise HTTPException(status_code=502, detail=upload_message)

        # 更新任务元数据状态，不保存账号密码；文件保留在本机暂存目录以便排查。
        task_metadata["status"] = "submitted"
        (task_dir / "task.json").write_text(
            json.dumps(task_metadata, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

        # 不返回本机绝对路径，也不保存账号密码。
        return {
            "task_id": task_id,
            "status": "submitted",
            "message": upload_message,
            "files": {
                "excel": {"filename": excel_file.filename, "size_bytes": excel_size},
                "attachment": {"filename": attachment_file.filename, "size_bytes": attachment_size},
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
    """接口用途：调试时读取当前页面及所有 Frame 的可见文本、交互元素和页面容器；不会读取输入框 value。"""
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



@app.get("/api/page/table")
async def get_page_table(
    page: int | None = Query(
        default=None,
        ge=1,
        description="可选页码；不传时只读取当前页，不改变网页当前分页。",
    ),
):
    """接口用途：读取当前业务 Frame 中可见表格的数据；可选指定页码，为后续批量翻页读取预留能力。"""
    async with browser_manager.operation_lock:
        page_obj = await browser_manager.get_page(settings.browser_timeout_ms)
        frame = await find_adcesop_frame(page_obj)
        if frame is None:
            raise HTTPException(status_code=404, detail="未找到业务系统 iframe（/adcesop）。")

        # 默认不切换页面；只有明确传入 page 参数时才执行分页跳转。
        if page is not None:
            active_page = frame.locator(".ant-pagination-item-active")
            active_text = (await active_page.first.inner_text()).strip() if await active_page.count() else ""
            if active_text != str(page):
                target_page = frame.locator(f'.ant-pagination-item[title="{page}"]')
                target_count = await target_page.count()
                if target_count != 1:
                    raise HTTPException(
                        status_code=404,
                        detail=f"当前分页控件中未找到第 {page} 页；请确认目标页码在可用分页范围内。",
                    )
                await target_page.click()
                try:
                    await frame.wait_for_function(
                        "(expected) => { const active = document.querySelector('.ant-pagination-item-active'); return active && active.textContent.trim() === String(expected); }",
                        arg=page,
                        timeout=settings.browser_timeout_ms,
                    )
                except PlaywrightError:
                    raise HTTPException(status_code=504, detail=f"等待列表切换到第 {page} 页超时。")

        try:
            result = await frame.evaluate(
                """() => {
                    const visible = el => {
                        const style = getComputedStyle(el);
                        const rect = el.getBoundingClientRect();
                        return style.display !== 'none' && style.visibility !== 'hidden' &&
                            rect.width > 0 && rect.height > 0;
                    };
                    const clean = value => (value || '').replace(/\\s+/g, ' ').trim();
                    const tables = [...document.querySelectorAll('.ant-table-wrapper table, table')]
                        .filter(visible);
                    const table = tables[0];
                    if (!table) {
                        return {found: false, columns: [], rows: [], pagination: null};
                    }

                    const headerRow = table.querySelector('thead tr:last-child');
                    const headerCells = headerRow ? [...headerRow.querySelectorAll('th')] : [];
                    const columns = headerCells.map((cell, index) => clean(cell.innerText) || ('列_' + (index + 1)));
                    const bodyRows = [...table.querySelectorAll('tbody tr')]
                        .filter(visible)
                        .filter(row => !row.classList.contains('ant-table-placeholder'));
                    const rows = bodyRows.map(row => {
                        const cells = [...row.querySelectorAll(':scope > td')];
                        const values = cells.map(cell => clean(cell.innerText));
                        const record = {};
                        values.forEach((value, index) => {
                            const key = columns[index] || ('列_' + (index + 1));
                            record[key] = value;
                        });
                        return record;
                    });

                    const active = document.querySelector('.ant-pagination-item-active');
                    const currentPage = active ? Number(clean(active.innerText)) || 1 : 1;
                    const totalText = clean(document.querySelector('.ant-pagination-total-text')?.innerText);
                    const totalMatch = totalText.match(/(?:共)?\\s*(\\d+)\\s*条/);
                    const total = totalMatch ? Number(totalMatch[1]) : null;
                    const sizeText = clean(document.querySelector('.ant-pagination-options-size-changer .ant-select-selection-selected-value')?.innerText ||
                        document.querySelector('.ant-pagination-options-size-changer .ant-select-selection')?.innerText);
                    const sizeMatch = sizeText.match(/(\\d+)/);
                    const pageSize = sizeMatch ? Number(sizeMatch[1]) : rows.length;
                    const pageItems = [...document.querySelectorAll('.ant-pagination-item')]
                        .map(item => Number(item.getAttribute('title') || clean(item.innerText)))
                        .filter(number => Number.isInteger(number) && number > 0);
                    const lastVisiblePage = pageItems.length ? Math.max(...pageItems) : currentPage;
                    const totalPages = total !== null && pageSize > 0
                        ? Math.ceil(total / pageSize)
                        : lastVisiblePage;
                    return {
                        found: true,
                        columns,
                        rows,
                        pagination: {
                            current_page: currentPage,
                            page_size: pageSize,
                            total,
                            total_pages: totalPages,
                            has_previous: currentPage > 1,
                            has_next: total !== null ? currentPage < totalPages : currentPage < lastVisiblePage
                        }
                    };
                }"""
            )
        except PlaywrightError as exc:
            raise HTTPException(status_code=502, detail="读取网页表格失败：" + str(exc)[:300]) from exc

        if not result.get("found"):
            raise HTTPException(status_code=404, detail="当前业务页面没有找到可见表格。")

        return {
            "url": page_obj.url.split("?", 1)[0].split("#", 1)[0],
            "frame": "adcesop",
            "columns": result["columns"],
            "rows": result["rows"],
            "row_count": len(result["rows"]),
            "pagination": result["pagination"],
            "message": "已读取当前页可见表格数据；不传 page 参数不会切换网页分页。",
        }
