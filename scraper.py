"""
scraper.py — 知乎内容爬虫核心模块

功能：
1. 使用 Playwright 持久化上下文登录知乎（手动登录，保存 Cookie）
2. 爬取指定用户的所有回答和文章链接
3. 爬取指定用户的所有想法（Pins）
4. 爬取指定问题下的所有（或前 N 个）回答
5. 爬取单个回答，可选附带评论区
6. 逐个访问并提取内容，转为 Markdown 保存
7. 保守的自动化兼容处理，不篡改原生浏览器指纹 API
8. 请求间隔随机延迟，降低被封风险
"""

import asyncio
import builtins
import hashlib
import ipaddress
import json
import random
import re
import sys
import time
import uuid
from datetime import datetime
from pathlib import Path
from urllib.parse import urljoin, urlparse, urlunparse

import httpx
from playwright.async_api import async_playwright, Page, BrowserContext

from stealth import STEALTH_JS
from converter import ZhihuConverter, normalize_image_url

# ── 配置 ─────────────────────────────────────────────────────

USER_DATA_DIR = Path(__file__).parent / "browser_data"
DEFAULT_OUTPUT_DIR = Path(__file__).parent / "output"

IMG_HEADERS = {
    "Referer": "https://www.zhihu.com/",
}

# 每次请求之间的延迟范围（秒）
MIN_DELAY: float = 10.0
MAX_DELAY: float = 20.0

MAX_IMAGE_BYTES = 25 * 1024 * 1024
MAX_IMAGE_CONCURRENCY = 5
ALLOWED_IMAGE_SCHEMES = {"http", "https"}


class ScraperError(RuntimeError):
    """爬取流程的基础异常。"""


class AntiBotError(ScraperError):
    """页面触发登录、验证码或反爬限制。"""


class ContentExtractionError(ScraperError):
    """目标页面存在，但没有提取到可信正文。"""


class CommentFetchError(ScraperError):
    """评论 API 请求失败或返回了不完整分页数据。"""


def _safe_print(*values, **kwargs) -> None:
    """在 GBK 等不支持 emoji 的终端中降级字符，避免日志导致任务崩溃。"""
    output = kwargs.get("file") or sys.stdout
    encoding = getattr(output, "encoding", None)
    if encoding:
        replacements = {
            "⚠️": "[WARN]",
            "✅": "[OK]",
            "❌": "[ERROR]",
            "⏳": "[WAIT]",
            "⏭️": "[SKIP]",
            "✨": "[DONE]",
            "🔐": "[LOGIN]",
            "🌍": "[GET]",
            "📜": "[SCROLL]",
            "📋": "[INFO]",
            "📝": "[LIST]",
            "🚀": "[START]",
            "📌": "[RESUME]",
            "📥": "[FETCH]",
            "💾": "[SAVE]",
            "📚": "[TASK]",
            "🖼️": "[IMAGE]",
            "💬": "[COMMENT]",
            "📂": "[DISK]",
        }

        def make_encodable(value: object) -> str:
            text = str(value)
            for symbol, replacement in replacements.items():
                text = text.replace(symbol, replacement)
            return text.encode(encoding, errors="replace").decode(encoding)

        values = tuple(
            make_encodable(value)
            for value in values
        )
    builtins.print(*values, **kwargs)


print = _safe_print


# ── 工具函数 ──────────────────────────────────────────────────

def sanitize_filename(name: str) -> str:
    """清理文件名中不允许的字符。"""
    name = re.sub(r'[/\\:*?"<>|\x00-\x1f]', "_", name)
    name = name.strip(" .")
    if len(name) > 120:
        name = name[:120].rstrip(" .")
    name = name or "untitled"

    # Windows 保留设备名即使带扩展名也不可作为普通文件名。
    reserved = {
        "CON", "PRN", "AUX", "NUL",
        *(f"COM{i}" for i in range(1, 10)),
        *(f"LPT{i}" for i in range(1, 10)),
    }
    if name.split(".", 1)[0].upper() in reserved:
        name = f"_{name}"
    return name


def random_delay(delay_min: float = MIN_DELAY, delay_max: float = MAX_DELAY) -> float:
    """返回一个随机延迟时间。"""
    _validate_delay_range(delay_min, delay_max)
    return random.uniform(delay_min, delay_max)


def _validate_delay_range(delay_min: float, delay_max: float) -> None:
    """验证请求延迟范围。"""
    if delay_min < 0 or delay_max < 0:
        raise ValueError("请求延迟不能为负数")
    if delay_min > delay_max:
        raise ValueError("最小延迟不能大于最大延迟")


def _content_identifier(url: str, content_type: str) -> str:
    """从 URL 提取稳定内容 ID，无法提取时使用 URL 哈希。"""
    patterns = {
        "answer": r"/answer/(\d+)",
        "article": r"/p/(\d+)",
        "pin": r"/pin/(\d+)",
    }
    match = re.search(patterns.get(content_type, r"/(\d+)(?:/)?$"), urlparse(url).path)
    if match:
        return match.group(1)
    return hashlib.sha256(url.encode("utf-8")).hexdigest()[:16]


def _content_path_stem(
    *,
    date: str,
    title: str,
    author: str,
    url: str,
    content_type: str,
    include_author: bool,
) -> str:
    """生成不会因截断丢失内容 ID 的稳定文件名。"""
    identifier = sanitize_filename(_content_identifier(url, content_type))
    suffix = f"__{content_type}_{identifier}"
    display = f"[{date}] {title}"
    if include_author:
        display += f" - {author}"
    display = sanitize_filename(display)
    budget = max(1, 120 - len(suffix))
    display = display[:budget].rstrip(" ._") or "untitled"
    return f"{display}{suffix}"


def _atomic_write_text(path: Path, content: str) -> None:
    """在同目录先写临时文件，再原子替换目标文件。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    try:
        temp_path.write_text(content, encoding="utf-8")
        temp_path.replace(path)
    finally:
        if temp_path.exists():
            temp_path.unlink()


def _atomic_write_json(path: Path, payload: object, *, indent: int | None = None) -> None:
    """原子写入 JSON，避免中断时留下半个进度文件。"""
    _atomic_write_text(
        path,
        json.dumps(payload, ensure_ascii=False, indent=indent, sort_keys=True),
    )


def _normalize_zhihu_url(href: str, base_url: str = "https://www.zhihu.com/") -> str | None:
    """规范化知乎链接并拒绝外部主机。"""
    full_url = urljoin(base_url, href)
    parsed = urlparse(full_url)
    hostname = (parsed.hostname or "").lower()
    if parsed.scheme not in {"http", "https"}:
        return None
    if hostname != "zhihu.com" and not hostname.endswith(".zhihu.com"):
        return None
    return urlunparse(parsed._replace(query="", fragment=""))


def _nested_get(d: dict, *keys):
    """安全地从嵌套字典中获取值。"""
    for key in keys:
        if isinstance(d, dict):
            d = d.get(key, {})
        else:
            return None
    return d if d != {} else None


def parse_question_id(input_str: str) -> str:
    """从 URL 或纯数字中提取问题 ID。"""
    match = re.search(r'question/(\d+)', input_str)
    if match:
        return match.group(1)
    if input_str.strip().isdigit():
        return input_str.strip()
    raise ValueError(f"无法识别问题 ID: {input_str}")


def parse_answer_url(input_str: str) -> tuple[str, str, str]:
    """
    从 URL 中提取信息，返回 (完整 URL, 问题 ID, 回答 ID)。

    支持格式:
        https://www.zhihu.com/question/12345/answer/67890
        /question/12345/answer/67890
    """
    match = re.search(r'question/(\d+)/answer/(\d+)', input_str)
    if match:
        qid, aid = match.group(1), match.group(2)
        full_url = f"https://www.zhihu.com/question/{qid}/answer/{aid}"
        return full_url, qid, aid
    raise ValueError(f"无法识别回答 URL: {input_str}")


# ── 浏览器上下文管理 ─────────────────────────────────────────

async def create_browser_context(pw, headless=False) -> BrowserContext:
    """创建保持原生浏览器 API 自洽的持久化上下文。"""
    USER_DATA_DIR.mkdir(parents=True, exist_ok=True)

    width = 1920 + random.randint(-100, 100)
    height = 1080 + random.randint(-50, 50)

    launch_args = [
        "--no-first-run",
        "--no-default-browser-check",
        "--disable-blink-features=AutomationControlled",
        f"--window-size={width},{height}",
    ]

    context = await pw.chromium.launch_persistent_context(
        user_data_dir=str(USER_DATA_DIR),
        headless=headless,
        slow_mo=50,
        args=launch_args,
        viewport={"width": width, "height": height},
        locale="zh-CN",
        timezone_id="Asia/Shanghai",
        java_script_enabled=True,
    )

    # 仅隐藏 webdriver 标记，不伪造或破坏原生浏览器 API
    await context.add_init_script(STEALTH_JS)

    return context


# ── 登录 ─────────────────────────────────────────────────────

async def login(timeout: int = 300):
    """
    打开知乎登录页面，等待用户手动登录。
    登录状态会保存在 browser_data 目录中，后续爬取无需重复登录。

    Args:
        timeout: 等待登录的超时时间（秒），默认 300 秒
    """
    if timeout <= 0:
        raise ValueError("登录超时时间必须大于 0 秒")

    print("=" * 60)
    print("🔐 知乎登录")
    print("=" * 60)
    print(f"将打开浏览器，请在 {timeout} 秒内完成登录。")
    print("登录成功后，程序会自动检测并保存登录状态。\n")

    async with async_playwright() as pw:
        context = await create_browser_context(pw, headless=False)
        try:
            page = context.pages[0] if context.pages else await context.new_page()
            await page.goto("https://www.zhihu.com/signin", wait_until="domcontentloaded")

            print("⏳ 等待登录... 请在浏览器中完成登录操作。")

            # 等待用户登录成功（检测跳转到首页或出现用户头像）
            start_time = time.time()
            while time.time() - start_time < timeout:
                url = page.url
                # 登录成功后一般会跳转到首页
                if "signin" not in url and "signup" not in url:
                    # 额外等待几秒确保 Cookie 完全写入
                    await asyncio.sleep(3)
                    print("✅ 登录成功！登录状态已保存。")
                    print(f"   数据目录: {USER_DATA_DIR.resolve()}")
                    return True
                await asyncio.sleep(2)

            print("❌ 登录超时，请重试。")
            return False

        finally:
            await context.close()


# ── 收集用户回答/文章列表 ────────────────────────────────────

async def _scroll_and_collect_links(
    page: Page, base_url: str, css_selector: str, url_filter_keywords: list[str]
) -> list[str]:
    """
    在用户的回答/文章列表页面中不断向下滚动，收集所有内容链接。

    Args:
        page: Playwright 页面对象
        base_url: 用户回答或文章页面 URL
        css_selector: 用于定位链接元素的 CSS 选择器
        url_filter_keywords: 用于过滤有效链接的关键词列表（匹配任一即可）

    Returns:
        去重后的链接列表
    """
    print(f"🌍 访问: {base_url}")
    await page.goto(base_url, wait_until="domcontentloaded")
    await asyncio.sleep(5)

    # 关闭可能的登录弹窗
    await _dismiss_popup(page)

    collected_links: list[str] = []
    seen_links: set[str] = set()
    no_new_count = 0
    max_no_new = 10  # 连续 10 次滚动没有新链接则认为到底了

    scroll_count = 0
    prev_scroll_height = 0

    while no_new_count < max_no_new:
        # 使用 CSS 选择器提取链接（比 JS 正则更可靠）
        link_elements = await page.query_selector_all(css_selector)
        links = []
        for el in link_elements:
            href = await el.get_attribute("href")
            if href:
                normalized = _normalize_zhihu_url(href, base_url)
                if normalized and any(kw in normalized for kw in url_filter_keywords):
                    links.append(normalized)

        prev_count = len(collected_links)
        for link in links:
            if link not in seen_links:
                seen_links.add(link)
                collected_links.append(link)

        new_count = len(collected_links) - prev_count
        if new_count == 0:
            no_new_count += 1
        else:
            no_new_count = 0

        scroll_count += 1
        print(f"   📜 第 {scroll_count} 次滚动，已发现 {len(collected_links)} 个链接"
              + (f"（新增 {new_count}）" if new_count > 0 else "（无新增）"))

        # 检查页面是否包含明确的"到底"标识
        end_marker = await page.evaluate("""() => {
            const bodyText = document.body.innerText;
            return bodyText.includes('已显示全部') || bodyText.includes('没有更多了');
        }""")

        if end_marker and no_new_count >= 3:
            print("   📋 已到达列表底部（页面提示已显示全部）。")
            break

        # 检查页面高度是否还在增长（懒加载是否还在工作）
        current_scroll_height = await page.evaluate("document.body.scrollHeight")
        height_changed = current_scroll_height != prev_scroll_height
        prev_scroll_height = current_scroll_height

        # 只有在页面高度不再变化且连续多次无新链接时才认为到底
        if not height_changed and no_new_count >= 5:
            print("   📋 页面不再加载新内容，停止滚动。")
            break

        # 滚动 — 使用多种方式触发知乎的懒加载
        # window.scrollBy 无法触发知乎的 scroll 事件监听器，
        # 必须使用键盘 End 键或直接操作 documentElement.scrollTop
        scroll_distance = random.randint(800, 1500)
        await page.keyboard.press("End")
        await asyncio.sleep(0.5)
        await page.evaluate(f"document.documentElement.scrollTop += {scroll_distance}")
        await asyncio.sleep(0.3)
        await page.keyboard.press("End")

        # 等待新内容加载
        await asyncio.sleep(2.0 + random.random() * 2)
        # 额外等待：如果上次没有新链接，多等一会让懒加载有时间完成
        if new_count == 0:
            await asyncio.sleep(2.0)

    return collected_links


async def collect_user_answers(page: Page, user_url_token: str) -> list[str]:
    """收集用户的所有回答链接。"""
    url = f"https://www.zhihu.com/people/{user_url_token}/answers"
    # 使用 CSS 选择器定位回答标题链接
    css_selector = ".ContentItem h2 a, .AnswerItem h2 a, h2.ContentItem-title a"
    return await _scroll_and_collect_links(page, url, css_selector, ["/answer/"])


async def collect_user_articles(page: Page, user_url_token: str) -> list[str]:
    """收集用户的所有文章链接。"""
    url = f"https://www.zhihu.com/people/{user_url_token}/posts"
    # 使用 CSS 选择器定位文章标题链接
    css_selector = ".ContentItem h2 a, .ArticleItem h2 a, h2.ContentItem-title a"
    return await _scroll_and_collect_links(page, url, css_selector, ["zhuanlan", "/p/"])


async def collect_user_pins(page: Page, user_url_token: str) -> list[str]:
    """收集用户的所有想法链接。"""
    url = f"https://www.zhihu.com/people/{user_url_token}/pins"
    css_selector = 'a[href*="/pin/"]'
    return await _scroll_and_collect_links(page, url, css_selector, ["/pin/"])


async def collect_question_answer_links(
    page: Page, question_id: str, max_answers: int | None = None
) -> list[str]:
    """
    在问题页面中滚动，收集回答链接。

    Args:
        page: Playwright 页面对象
        question_id: 知乎问题 ID
        max_answers: 最多收集的回答数量（None 表示全部）

    Returns:
        去重后的回答链接列表
    """
    url = f"https://www.zhihu.com/question/{question_id}"
    print(f"🌍 访问: {url}")
    await page.goto(url, wait_until="domcontentloaded")
    await asyncio.sleep(5)

    # 关闭可能的登录弹窗
    await _dismiss_popup(page)

    collected_links: list[str] = []
    seen_links: set[str] = set()
    no_new_count = 0
    max_no_new = 10
    scroll_count = 0
    prev_scroll_height = 0

    while no_new_count < max_no_new:
        # 使用 CSS 选择器提取回答链接
        link_elements = await page.query_selector_all('a[href*="/answer/"]')
        links = []
        for el in link_elements:
            href = await el.get_attribute("href")
            if href:
                normalized = _normalize_zhihu_url(href, url)
                if normalized and f"/question/{question_id}/answer/" in normalized:
                    links.append(normalized)

        prev_count = len(collected_links)
        for link in links:
            if link not in seen_links:
                seen_links.add(link)
                collected_links.append(link)
        new_count = len(collected_links) - prev_count

        if new_count == 0:
            no_new_count += 1
        else:
            no_new_count = 0

        scroll_count += 1
        print(f"   📜 第 {scroll_count} 次滚动，已发现 {len(collected_links)} 个回答链接"
              + (f"（新增 {new_count}）" if new_count > 0 else "（无新增）"))

        # 检查是否已达到目标数量
        if max_answers is not None and len(collected_links) >= max_answers:
            print(f"   📋 已达到目标数量 {max_answers}。")
            break

        # 检查页面是否包含明确的"到底"标识
        end_marker = await page.evaluate("""() => {
            const bodyText = document.body.innerText;
            return bodyText.includes('已显示全部') || bodyText.includes('没有更多了');
        }""")

        if end_marker and no_new_count >= 3:
            print("   📋 已到达列表底部（页面提示已显示全部）。")
            break

        # 检查页面高度是否还在增长
        current_scroll_height = await page.evaluate("document.body.scrollHeight")
        height_changed = current_scroll_height != prev_scroll_height
        prev_scroll_height = current_scroll_height

        if not height_changed and no_new_count >= 5:
            print("   📋 页面不再加载新内容，停止滚动。")
            break

        # 滚动 — 使用多种方式触发知乎的懒加载
        scroll_distance = random.randint(800, 1500)
        await page.keyboard.press("End")
        await asyncio.sleep(0.5)
        await page.evaluate(f"document.documentElement.scrollTop += {scroll_distance}")
        await asyncio.sleep(0.3)
        await page.keyboard.press("End")

        await asyncio.sleep(2.0 + random.random() * 2)
        if new_count == 0:
            await asyncio.sleep(2.0)

    result = collected_links
    if max_answers is not None:
        result = result[:max_answers]
    return result


# ── 页面内容提取 ─────────────────────────────────────────────

async def _dismiss_popup(page: Page) -> None:
    """关闭登录弹窗。"""
    try:
        btn = page.locator("button.Modal-closeButton")
        if await btn.count() > 0:
            await btn.click(timeout=2000)
            await page.wait_for_timeout(500)
    except Exception:
        pass


async def _assert_page_usable(page: Page, expected_url: str) -> None:
    """拒绝登录页、验证页、错误页和意外的站外跳转。"""
    parsed = urlparse(page.url)
    hostname = (parsed.hostname or "").lower()
    if hostname != "zhihu.com" and not hostname.endswith(".zhihu.com"):
        raise ContentExtractionError(
            f"页面被重定向到非知乎地址: {page.url}（原地址: {expected_url}）"
        )
    if "/signin" in parsed.path or "/signup" in parsed.path:
        raise AntiBotError(f"登录状态已失效，请重新登录: {expected_url}")

    body_text = await page.locator("body").inner_text(timeout=10000)
    anti_bot_markers = (
        "40362",
        "请求存在异常",
        "请完成安全验证",
        "帐号或密码错误",
        "系统检测到您的网络环境存在异常",
    )
    marker = next((item for item in anti_bot_markers if item in body_text), None)
    if marker:
        raise AntiBotError(f"触发知乎验证或反爬（{marker}）: {expected_url}")


async def _first_nonempty_html(
    page: Page,
    selectors: tuple[str, ...],
    *,
    content_kind: str,
    url: str,
) -> str:
    """从候选选择器中提取正文，不再回退到整个 body。"""
    for selector in selectors:
        try:
            locator = page.locator(selector).first
            if await locator.count() == 0:
                continue
            html = (await locator.inner_html(timeout=5000)).strip()
            if html:
                return html
        except Exception:
            continue
    raise ContentExtractionError(
        f"未找到有效{content_kind}正文，页面结构可能已变化: {url}"
    )


async def _safe_text(page: Page, selector: str, default: str) -> str:
    """安全获取元素文本。"""
    try:
        el = page.locator(selector).first
        return await el.inner_text(timeout=3000)
    except Exception:
        return default


async def _extract_date(page: Page) -> str:
    """提取发布日期。"""
    try:
        meta = await page.locator('meta[itemprop="datePublished"]').get_attribute(
            "content", timeout=2000
        )
        if meta:
            return meta[:10]
    except Exception:
        pass
    # 尝试从页面内容中提取日期
    try:
        date_text = await _safe_text(page, ".ContentItem-time", "")
        if not date_text:
            date_text = await _safe_text(page, ".Post-Header .ContentItem-time", "")
        match = re.search(r"(\d{4}-\d{2}-\d{2})", date_text)
        if match:
            return match.group(1)
    except Exception:
        pass
    return "未知日期"


async def extract_answer(page: Page, url: str) -> dict:
    """
    提取知乎回答内容。

    Returns:
        {"title": str, "author": str, "html": str, "date": str, "type": "answer", "url": str}
    """
    await page.goto(url, wait_until="domcontentloaded")
    await page.wait_for_timeout(3000)
    await _dismiss_popup(page)

    await _assert_page_usable(page, url)

    # 等待内容加载
    try:
        await page.wait_for_selector(".QuestionAnswer-content, .AnswerCard", timeout=15000)
    except Exception:
        # 有时候页面结构不同，尝试等待 RichText
        await page.wait_for_selector(".RichText", timeout=10000)

    # 点击"阅读全文"
    try:
        read_more = page.locator("button:has-text('阅读全文')").first
        if await read_more.count() > 0:
            await read_more.click()
            await page.wait_for_timeout(1000)
    except Exception:
        pass

    title = await _safe_text(page, "h1.QuestionHeader-title", "未知问题")
    author = await _safe_text(page, ".AuthorInfo-name .UserLink-link", "未知作者")
    if author == "未知作者":
        author = await _safe_text(page, ".AuthorInfo span.UserLink-Name", "未知作者")
    date = await _extract_date(page)

    # 提取回答 HTML
    html = await _first_nonempty_html(
        page,
        (
            ".QuestionAnswer-content .RichText",
            ".AnswerCard .RichText",
            "[data-zop] .RichText",
        ),
        content_kind="回答",
        url=url,
    )

    return {
        "title": title.strip(),
        "author": author.strip(),
        "html": html,
        "date": date,
        "type": "answer",
        "url": url,
    }


async def extract_article(page: Page, url: str) -> dict:
    """
    提取知乎专栏文章内容。

    Returns:
        {"title": str, "author": str, "html": str, "date": str, "type": "article", "url": str}
    """
    await page.goto(url, wait_until="domcontentloaded")
    await page.wait_for_timeout(3000)
    await _dismiss_popup(page)

    await _assert_page_usable(page, url)

    try:
        await page.wait_for_selector("h1.Post-Title", timeout=15000)
    except Exception:
        await page.wait_for_selector(".RichText", timeout=10000)

    title = await _safe_text(page, "h1.Post-Title", "未知标题")
    author = await _safe_text(page, ".AuthorInfo span.UserLink-Name", "未知作者")
    if author == "未知作者":
        author = await _safe_text(page, ".AuthorInfo-name .UserLink-link", "未知作者")
    date = await _extract_date(page)

    html = await _first_nonempty_html(
        page,
        (
            ".Post-RichTextContainer .RichText",
            "article .RichText",
            ".Post-content .RichText",
        ),
        content_kind="文章",
        url=url,
    )

    # 尝试获取头图
    try:
        title_img = page.locator("img.TitleImage").first
        if await title_img.count() > 0:
            src = await title_img.get_attribute("src")
            if src:
                html = f'<img src="{src}" alt="TitleImage"><br>{html}'
    except Exception:
        pass

    return {
        "title": title.strip(),
        "author": author.strip(),
        "html": html,
        "date": date,
        "type": "article",
        "url": url,
    }


async def extract_pin(page: Page, url: str) -> dict:
    """
    提取知乎想法内容。

    Returns:
        {"title": str, "author": str, "html": str, "date": str, "type": "pin", "url": str}
    """
    await page.goto(url, wait_until="domcontentloaded")
    await page.wait_for_timeout(3000)
    await _dismiss_popup(page)

    await _assert_page_usable(page, url)

    # 等待内容加载
    try:
        await page.wait_for_selector(".PinItem, .RichContent, .Pin-content", timeout=15000)
    except Exception:
        pass

    # 提取作者
    author = await _safe_text(page, ".PinItem-author .UserLink-link", "未知作者")
    if author == "未知作者":
        author = await _safe_text(page, ".AuthorInfo-name .UserLink-link", "未知作者")

    date = await _extract_date(page)

    # 提取想法 HTML 内容
    pin_selectors = (
        ".PinItem .RichContent-inner",
        ".PinItem .RichContent",
        ".PinItem .RichText",
        ".Pin-content .RichText",
    )
    html = await _first_nonempty_html(
        page,
        pin_selectors,
        content_kind="想法",
        url=url,
    )

    # 想法没有标题，用内容前 50 字作为标题
    plain_text = await page.locator("body").inner_text()
    # 从正文中提取前 50 字
    for sel in pin_selectors:
        try:
            loc = page.locator(sel).first
            if await loc.count() > 0:
                plain_text = await loc.inner_text()
                break
        except Exception:
            continue

    title = plain_text.strip().replace("\n", " ")[:50]
    if not title:
        title = "想法"

    return {
        "title": title.strip(),
        "author": author.strip(),
        "html": html,
        "date": date,
        "type": "pin",
        "url": url,
    }


# ── 评论提取 ─────────────────────────────────────────────────

async def _fetch_comment_page(page: Page, url: str) -> dict:
    """通过浏览器 fetch 获取一页评论数据，失败时重试并明确报错。"""
    parsed = urlparse(url)
    if (
        parsed.scheme != "https"
        or parsed.hostname != "www.zhihu.com"
        or not parsed.path.startswith("/api/v4/comment_v5/")
    ):
        raise CommentFetchError(f"拒绝访问非预期评论 API: {url}")

    last_error = "未知错误"
    for attempt in range(1, 4):
        result = await page.evaluate("""
            async (url) => {
                try {
                    const resp = await fetch(url, { credentials: 'include' });
                    if (!resp.ok) {
                        return { ok: false, status: resp.status, error: `HTTP ${resp.status}` };
                    }
                    try {
                        return { ok: true, status: resp.status, payload: await resp.json() };
                    } catch (e) {
                        return { ok: false, status: resp.status, error: `JSON: ${e.message}` };
                    }
                } catch (e) {
                    return { ok: false, status: 0, error: e.message };
                }
            }
        """, url)
        if result.get("ok") and isinstance(result.get("payload"), dict):
            return result["payload"]

        last_error = result.get("error") or f"HTTP {result.get('status', 0)}"
        if attempt < 3:
            await asyncio.sleep((2 ** (attempt - 1)) + random.random())

    raise CommentFetchError(f"评论 API 请求失败（已重试 3 次）: {last_error}，URL: {url}")


def _get_comment_author(comment: dict) -> str:
    """从评论数据中提取作者名。comment_v5 API 的 author 结构为 {name: ...}，无 member 层。"""
    author = comment.get("author")
    if isinstance(author, dict):
        # comment_v5: author.name 直接可用
        name = author.get("name")
        if name:
            return name
        # 兼容旧版: author.member.name
        member = author.get("member")
        if isinstance(member, dict):
            return member.get("name", "匿名用户")
    return "匿名用户"


async def extract_comments(page: Page, answer_id: str) -> list[dict]:
    """
    通过知乎 API 提取回答下的所有评论（包含子评论）。

    comment_v5 API 使用游标分页（cursor-based pagination），
    必须使用 paging.next 中的完整 URL 进行翻页，而非简单的整数 offset。

    Args:
        page: Playwright 页面对象（必须在知乎域名下）
        answer_id: 回答 ID

    Returns:
        评论列表，每个评论包含 author, content, created_time, like_count, child_comments
    """
    print(f"   💬 正在获取评论...")

    all_comments = []
    seen_root_pages: set[str] = set()

    # 首次请求：offset 留空，API 会返回第一页
    next_url = (
        f"https://www.zhihu.com/api/v4/comment_v5/answers/{answer_id}"
        f"/root_comment?order_by=score&limit=20&offset="
    )

    while next_url:
        if next_url in seen_root_pages:
            raise CommentFetchError(f"评论分页游标重复，已停止以避免死循环: {next_url}")
        seen_root_pages.add(next_url)
        data = await _fetch_comment_page(page, next_url)

        page_comments = data.get("data")
        if not isinstance(page_comments, list):
            raise CommentFetchError(f"评论 API 返回格式异常: {next_url}")
        if not page_comments:
            if data.get("paging", {}).get("is_end", True):
                break
            raise CommentFetchError(f"评论分页尚未结束但返回空数据: {next_url}")

        for comment in page_comments:
            root = {
                "author": _get_comment_author(comment),
                "content": comment.get("content", ""),
                "created_time": comment.get("created_time", 0),
                "like_count": comment.get("like_count", 0),
                "child_comments": [],
            }

            # 获取子评论（同样使用游标分页）
            child_count = int(comment.get("child_comment_count", 0) or 0)
            if child_count > 0:
                comment_id = comment.get("id", "")
                if not comment_id:
                    raise CommentFetchError("评论包含子评论计数，但缺少评论 ID")
                child_next_url = (
                    f"https://www.zhihu.com/api/v4/comment_v5/comment/{comment_id}"
                    f"/child_comment?order_by=ts&limit=20&offset="
                )
                seen_child_pages: set[str] = set()
                while child_next_url:
                    if child_next_url in seen_child_pages:
                        raise CommentFetchError(
                            f"子评论分页游标重复，已停止以避免死循环: {child_next_url}"
                        )
                    seen_child_pages.add(child_next_url)
                    child_data = await _fetch_comment_page(page, child_next_url)

                    child_comments = child_data.get("data")
                    if not isinstance(child_comments, list):
                        raise CommentFetchError(f"子评论 API 返回格式异常: {child_next_url}")
                    if not child_comments:
                        if child_data.get("paging", {}).get("is_end", True):
                            break
                        raise CommentFetchError(
                            f"子评论分页尚未结束但返回空数据: {child_next_url}"
                        )

                    for child in child_comments:
                        reply_to_author = child.get("reply_to_author")
                        reply_to_name = ""
                        if isinstance(reply_to_author, dict):
                            reply_to_name = reply_to_author.get("name", "")
                            if not reply_to_name:
                                member = reply_to_author.get("member")
                                if isinstance(member, dict):
                                    reply_to_name = member.get("name", "")

                        root["child_comments"].append({
                            "author": _get_comment_author(child),
                            "content": child.get("content", ""),
                            "created_time": child.get("created_time", 0),
                            "like_count": child.get("like_count", 0),
                            "reply_to": reply_to_name,
                        })

                    child_paging = child_data.get("paging", {})
                    if child_paging.get("is_end", True):
                        break
                    child_next_url = child_paging.get("next", "")
                    await asyncio.sleep(0.3)

            all_comments.append(root)

        paging = data.get("paging", {})
        if paging.get("is_end", True):
            break
        next_url = paging.get("next", "")
        await asyncio.sleep(0.5)

    total = len(all_comments)
    child_total = sum(len(c["child_comments"]) for c in all_comments)
    print(f"   💬 共获取 {total} 条根评论，{child_total} 条子评论")

    return all_comments


def format_comments_markdown(comments: list[dict]) -> str:
    """将评论数据格式化为 Markdown 文本。"""
    if not comments:
        return ""

    lines = ["\n\n---\n", "## 评论区\n"]

    for i, comment in enumerate(comments, 1):
        ts = comment.get("created_time", 0)
        time_str = datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M") if ts else "未知时间"
        author = comment.get("author", "匿名用户")
        likes = comment.get("like_count", 0)
        content = comment.get("content", "")

        lines.append(f"### {i}楼 · {author} · {time_str} · 👍 {likes}\n")
        lines.append(f"{content}\n")

        # 子评论
        for child in comment.get("child_comments", []):
            child_ts = child.get("created_time", 0)
            child_time = datetime.fromtimestamp(child_ts).strftime("%Y-%m-%d %H:%M") if child_ts else "未知时间"
            child_author = child.get("author", "匿名用户")
            child_likes = child.get("like_count", 0)
            child_content = child.get("content", "")
            reply_to = child.get("reply_to", "")

            reply_prefix = f"回复 {reply_to} " if reply_to else ""
            lines.append(f"> **{child_author}** {reply_prefix}· {child_time} · 👍 {child_likes}  ")
            lines.append(f"> {child_content}")
            lines.append(">")

        lines.append("")

    return "\n".join(lines)


# ── 图片下载 ─────────────────────────────────────────────────

async def download_images(img_urls: list[str], dest: Path) -> dict[str, str]:
    """并发下载图片到本地，返回规范化 URL → 本地路径的映射。"""
    dest.mkdir(parents=True, exist_ok=True)
    url_to_local: dict[str, str] = {}
    failures: list[tuple[str, str]] = []
    semaphore = asyncio.Semaphore(MAX_IMAGE_CONCURRENCY)

    limits = httpx.Limits(max_keepalive_connections=5, max_connections=10)
    async with httpx.AsyncClient(
        headers=IMG_HEADERS,
        timeout=30.0,
        follow_redirects=False,
        limits=limits,
    ) as client:

        async def download_one(raw_url: str) -> None:
            img_url = normalize_image_url(raw_url)
            if not img_url or "data:image" in img_url or "equation" in img_url:
                return

            parsed = urlparse(img_url)
            hostname = (parsed.hostname or "").lower()
            if parsed.scheme not in ALLOWED_IMAGE_SCHEMES or not hostname:
                failures.append((img_url, "不支持的 URL"))
                return
            if hostname == "localhost" or hostname.endswith(".localhost"):
                failures.append((img_url, "拒绝访问本机地址"))
                return
            try:
                ip = ipaddress.ip_address(hostname)
                if not ip.is_global:
                    failures.append((img_url, "拒绝访问私有或保留地址"))
                    return
            except ValueError:
                pass

            async with semaphore:
                temp_path: Path | None = None
                response: httpx.Response | None = None
                try:
                    current_url = img_url
                    for _ in range(6):
                        request = client.build_request("GET", current_url)
                        response = await client.send(request, stream=True)
                        if response.is_redirect:
                            location = response.headers.get("location")
                            if not location:
                                raise httpx.HTTPError("重定向响应缺少 Location")
                            await response.aclose()
                            response = None
                            current_url = normalize_image_url(urljoin(current_url, location))
                            redirect_host = (urlparse(current_url).hostname or "").lower()
                            if redirect_host in {"", "localhost"} or redirect_host.endswith(".localhost"):
                                raise httpx.HTTPError("重定向到了不安全地址")
                            try:
                                redirect_ip = ipaddress.ip_address(redirect_host)
                                if not redirect_ip.is_global:
                                    raise httpx.HTTPError("重定向到了私有或保留地址")
                            except ValueError:
                                pass
                            continue
                        break
                    else:
                        raise httpx.TooManyRedirects("图片重定向次数超过 5 次")

                    if response is None:
                        raise httpx.HTTPError("未收到响应")
                    response.raise_for_status()

                    content_type = response.headers.get("content-type", "").split(";", 1)[0].lower()
                    url_ext = Path(urlparse(current_url).path).suffix.lower()
                    allowed_exts = {".jpg", ".jpeg", ".png", ".gif", ".webp", ".bmp", ".svg", ".avif"}
                    if not content_type.startswith("image/") and url_ext not in allowed_exts:
                        raise ValueError(f"响应不是图片（Content-Type: {content_type or '未知'}）")

                    content_length = response.headers.get("content-length")
                    if content_length and int(content_length) > MAX_IMAGE_BYTES:
                        raise ValueError("图片超过 25 MiB 限制")

                    mime_exts = {
                        "image/jpeg": ".jpg",
                        "image/png": ".png",
                        "image/gif": ".gif",
                        "image/webp": ".webp",
                        "image/bmp": ".bmp",
                        "image/svg+xml": ".svg",
                        "image/avif": ".avif",
                    }
                    ext = url_ext if url_ext in allowed_exts else mime_exts.get(content_type, ".jpg")
                    fname = hashlib.sha256(img_url.encode("utf-8")).hexdigest()[:16] + ext
                    fpath = dest / fname
                    temp_path = fpath.with_name(f".{fpath.name}.{uuid.uuid4().hex}.tmp")

                    total_bytes = 0
                    with temp_path.open("wb") as handle:
                        async for chunk in response.aiter_bytes():
                            total_bytes += len(chunk)
                            if total_bytes > MAX_IMAGE_BYTES:
                                raise ValueError("图片超过 25 MiB 限制")
                            handle.write(chunk)
                    temp_path.replace(fpath)
                    url_to_local[img_url] = f"images/{fname}"
                except Exception as exc:
                    failures.append((img_url, str(exc)))
                finally:
                    if response is not None:
                        await response.aclose()
                    if temp_path and temp_path.exists():
                        temp_path.unlink()

        await asyncio.gather(*(download_one(url) for url in img_urls))

    for failed_url, reason in failures[:5]:
        print(f"   ⚠️  图片下载失败: {failed_url}（{reason}）")
    if len(failures) > 5:
        print(f"   ⚠️  另有 {len(failures) - 5} 张图片下载失败")

    return url_to_local


# ── 保存单篇内容为 Markdown ──────────────────────────────────

async def save_content_as_markdown(
    info: dict, output_dir: Path, download_img: bool = True,
    comments: list[dict] | None = None,
) -> Path:
    """
    将提取到的内容保存为 Markdown 文件。

    当 download_img=True 时（默认模式）：
        输出结构为  <type_dir>/<日期_标题_作者__类型_ID>/index.md，图片存于 images/ 子目录。
    当 download_img=False 时（--no-images 模式）：
        输出结构为  <type_dir>/<日期_标题__类型_ID>.md，图片以 [图片] 占位符替代。

    Args:
        info: extract_answer 或 extract_article 返回的字典
        output_dir: 输出根目录
        download_img: 是否下载图片到本地
        comments: 评论列表（可选，传入则追加评论区）

    Returns:
        保存的文件路径
    """
    title = info["title"]
    author = info["author"]
    date = info["date"]
    html = info["html"]
    content_type = info["type"]
    url = info["url"]

    type_labels = {"answer": "回答", "article": "文章", "pin": "想法"}
    type_dirs = {"answer": "answers", "article": "articles", "pin": "pins"}
    type_label = type_labels.get(content_type, "内容")

    # 按类型分目录
    type_dir = output_dir / type_dirs.get(content_type, "other")

    if download_img:
        # 普通模式：每篇内容一个子文件夹，图片存于 images/ 子目录
        folder_name = _content_path_stem(
            date=date,
            title=title,
            author=author,
            url=url,
            content_type=content_type,
            include_author=True,
        )
        folder = type_dir / folder_name
        folder.mkdir(parents=True, exist_ok=True)

        img_map = {}
        img_urls = ZhihuConverter.extract_image_urls(html)
        if img_urls:
            print(f"   🖼️  发现 {len(img_urls)} 张图片，正在下载...")
            img_dir = folder / "images"
            img_map = await download_images(img_urls, img_dir)
            print(f"   ✅ 成功下载 {len(img_map)} 张图片")
            if img_dir.exists() and not any(img_dir.iterdir()):
                img_dir.rmdir()

        converter = ZhihuConverter(img_map=img_map)
        md_path = folder / "index.md"
    else:
        # --no-images 模式：稳定 ID 防止同日同标题内容互相覆盖
        type_dir.mkdir(parents=True, exist_ok=True)
        file_name = _content_path_stem(
            date=date,
            title=title,
            author=author,
            url=url,
            content_type=content_type,
            include_author=False,
        ) + ".md"
        md_path = type_dir / file_name

        converter = ZhihuConverter(no_images=True)

    # HTML → Markdown
    md = converter.convert(html)

    # 拼接元信息头
    header = (
        f"# {title}\n\n"
        f"> **类型**: {type_label}  \n"
        f"> **作者**: {author}  \n"
        f"> **来源**: [{url}]({url})  \n"
        f"> **日期**: {date}\n\n"
        f"---\n\n"
    )

    # 拼接评论区
    comments_md = ""
    if comments:
        comments_md = format_comments_markdown(comments)

    _atomic_write_text(md_path, header + md + comments_md)

    return md_path


def _scan_done_items_from_disk(
    output_dir: Path,
    subdirs: tuple[str, ...] = ("answers", "articles", "pins"),
) -> dict[str, Path]:
    """
    扫描输出目录中已存在的 Markdown 文件，返回来源 URL → 文件路径。
    兼容两种结构：
      - 普通模式（有图片）：<type_dir>/<子文件夹>/index.md
      - --no-images 模式：<type_dir>/<日期_标题__类型_ID>.md（直接在类型目录中）
    """
    done: dict[str, Path] = {}
    url_pattern = re.compile(r'>\s*\*\*来源\*\*:\s*\[([^\]]+)\]')
    for subdir in subdirs:
        type_dir = output_dir / subdir
        if not type_dir.exists():
            continue
        # 兼容两种结构：递归匹配所有 .md 文件
        for md_file in type_dir.rglob("*.md"):
            try:
                text = md_file.read_text(encoding="utf-8")[:500]
                m = url_pattern.search(text)
                if m:
                    done[m.group(1)] = md_file
            except Exception:
                pass
    return done


def _scan_done_urls_from_disk(output_dir: Path) -> set[str]:
    """兼容旧调用：返回磁盘上拥有有效 Markdown 文件的 URL 集合。"""
    return set(_scan_done_items_from_disk(output_dir))


def _read_recorded_progress_urls(progress_file: Path) -> set[str]:
    """读取新旧两种进度格式，仅用于识别失效记录并给出提示。"""
    if not progress_file.exists():
        return set()
    try:
        payload = json.loads(progress_file.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return set()

    urls: set[str] = set()
    done = payload.get("done", [])
    if isinstance(done, list):
        urls.update(str(url) for url in done if isinstance(url, str))
    items = payload.get("items", {})
    if isinstance(items, dict):
        urls.update(str(url) for url in items if isinstance(url, str))
    return urls


def _load_verified_progress(
    output_dir: Path,
    progress_file: Path,
    subdirs: tuple[str, ...] = ("answers", "articles", "pins"),
) -> dict[str, Path]:
    """以磁盘文件为权威来源，丢弃指向已删除文件的幽灵进度。"""
    recorded_urls = _read_recorded_progress_urls(progress_file)
    disk_items = _scan_done_items_from_disk(output_dir, subdirs)
    stale_urls = recorded_urls - set(disk_items)
    if stale_urls:
        print(f"⚠️  已移除 {len(stale_urls)} 条缺少对应文件的失效进度")
    recovered_urls = set(disk_items) - recorded_urls
    if recovered_urls:
        print(f"📂 从磁盘恢复 {len(recovered_urls)} 条未写入进度文件的内容")
    return disk_items


def _write_progress(
    progress_file: Path,
    output_dir: Path,
    done_items: dict[str, Path],
) -> None:
    """写入带文件映射的 v2 进度格式。"""
    items: dict[str, str] = {}
    for url, path in sorted(done_items.items()):
        try:
            relative = path.resolve().relative_to(output_dir.resolve()).as_posix()
        except ValueError:
            relative = str(path.resolve())
        items[url] = relative
    _atomic_write_json(
        progress_file,
        {
            "version": 2,
            "done": sorted(items),
            "items": items,
        },
        indent=2,
    )


# ── 主爬取流程 ────────────────────────────────────────────────

async def scrape_user(
    user_url_token: str,
    output_dir: Path | None = None,
    scrape_answers: bool = True,
    scrape_articles: bool = True,
    download_img: bool = True,
    delay_min: float = 10.0,
    delay_max: float = 20.0,
    headless: bool = False,
):
    """
    爬取指定知乎用户的所有回答和/或文章。

    Args:
        user_url_token: 知乎用户的 URL token（个人主页 URL 中的标识符）
                        例如 https://www.zhihu.com/people/xxx 中的 xxx
        output_dir: 输出目录
        scrape_answers: 是否爬取回答
        scrape_articles: 是否爬取文章
        download_img: 是否下载图片
        delay_min: 请求间最小延迟（秒）
        delay_max: 请求间最大延迟（秒）
        headless: 是否使用无头模式
    """
    _validate_delay_range(delay_min, delay_max)
    if not scrape_answers and not scrape_articles:
        raise ValueError("至少需要选择爬取回答或文章中的一种")
    user_url_token = user_url_token.strip()
    if not user_url_token:
        raise ValueError("用户 URL Token 不能为空")

    if output_dir is None:
        output_dir = DEFAULT_OUTPUT_DIR / sanitize_filename(user_url_token)

    output_dir.mkdir(parents=True, exist_ok=True)

    print("=" * 60)
    print(f"📚 开始爬取用户: {user_url_token}")
    print(f"   输出目录: {output_dir.resolve()}")
    print(f"   爬取回答: {'是' if scrape_answers else '否'}")
    print(f"   爬取文章: {'是' if scrape_articles else '否'}")
    print(f"   下载图片: {'是' if download_img else '否'}")
    print(f"   请求延迟: {delay_min}-{delay_max} 秒")
    print("=" * 60)

    async with async_playwright() as pw:
        context = await create_browser_context(pw, headless=headless)

        try:
            page = context.pages[0] if context.pages else await context.new_page()

            # ── 收集链接 ──
            all_urls = []

            if scrape_answers:
                print("\n📝 正在收集回答列表...")
                answer_urls = await collect_user_answers(page, user_url_token)
                print(f"   共发现 {len(answer_urls)} 个回答")
                all_urls.extend([(url, "answer") for url in answer_urls])

            if scrape_articles:
                print("\n📝 正在收集文章列表...")
                # 在收集文章之前添加延迟
                if scrape_answers:
                    delay = random_delay(delay_min, delay_max)
                    print(f"   ⏳ 等待 {delay:.1f} 秒...")
                    await asyncio.sleep(delay)
                article_urls = await collect_user_articles(page, user_url_token)
                print(f"   共发现 {len(article_urls)} 篇文章")
                all_urls.extend([(url, "article") for url in article_urls])

            if not all_urls:
                print("\n⚠️  未发现任何内容，请检查用户 URL token 是否正确。")
                return

            total = len(all_urls)
            print(f"\n🚀 共计 {total} 项内容待爬取\n")

            # ── 保存本次发现的链接清单 ──
            links_file = output_dir / "links.json"
            links_data = [{"url": url, "type": t} for url, t in all_urls]
            _atomic_write_json(links_file, links_data, indent=2)
            print(f"📋 链接列表已保存到: {links_file}\n")

            # ── 检查已爬取的内容（断点续传） ──
            progress_file = output_dir / "progress.json"
            done_items = _load_verified_progress(
                output_dir,
                progress_file,
                ("answers", "articles"),
            )
            done_urls = set(done_items)

            if done_urls:
                # 只统计与当前链接列表匹配的数量
                matched = sum(1 for url, _ in all_urls if url in done_urls)
                print(f"📌 检测到之前的进度，已完成 {matched}/{total} 项，将跳过。\n")

            if progress_file.exists() or done_items:
                _write_progress(progress_file, output_dir, done_items)

            # ── 逐个爬取 ──
            success_count = 0
            fail_count = 0

            for idx, (url, content_type) in enumerate(all_urls, 1):
                if url in done_urls:
                    print(f"[{idx}/{total}] ⏭️  跳过（已完成）: {url}")
                    success_count += 1
                    continue

                print(f"[{idx}/{total}] 📥 正在爬取{' 回答' if content_type == 'answer' else '文章'}: {url}")

                try:
                    if content_type == "answer":
                        info = await extract_answer(page, url)
                    else:
                        info = await extract_article(page, url)

                    md_path = await save_content_as_markdown(info, output_dir, download_img)
                    print(f"   💾 已保存: {md_path}")

                    success_count += 1
                    done_urls.add(url)
                    done_items[url] = md_path

                    # 更新进度
                    _write_progress(progress_file, output_dir, done_items)

                except Exception as e:
                    fail_count += 1
                    print(f"   ❌ 失败: {e}")

                    # 如果触发反爬，加大延迟
                    if isinstance(e, AntiBotError):
                        extra_wait = 30 + random.random() * 30
                        print(f"   ⚠️  触发反爬机制，额外等待 {extra_wait:.0f} 秒...")
                        await asyncio.sleep(extra_wait)

                # 请求间延迟
                if idx < total:
                    delay = random_delay(delay_min, delay_max)
                    print(f"   ⏳ 等待 {delay:.1f} 秒...\n")
                    await asyncio.sleep(delay)

            # ── 汇总 ──
            print("\n" + "=" * 60)
            print("✨ 爬取完成！")
            print(f"   成功: {success_count}")
            print(f"   失败: {fail_count}")
            print(f"   输出目录: {output_dir.resolve()}")
            print("=" * 60)

        finally:
            await context.close()


async def scrape_question(
    question_input: str,
    max_answers: int | None = None,
    output_dir: Path | None = None,
    download_img: bool = True,
    delay_min: float = 10.0,
    delay_max: float = 20.0,
    headless: bool = False,
):
    """
    爬取指定知乎问题下的回答。

    Args:
        question_input: 问题 URL 或纯数字 ID
        max_answers: 最多爬取的回答数量（None 表示全部）
        output_dir: 输出目录
        download_img: 是否下载图片
        delay_min: 请求间最小延迟（秒）
        delay_max: 请求间最大延迟（秒）
        headless: 是否使用无头模式
    """
    _validate_delay_range(delay_min, delay_max)
    if max_answers is not None and max_answers <= 0:
        raise ValueError("最大回答数必须是正整数")

    question_id = parse_question_id(question_input)

    if output_dir is None:
        output_dir = DEFAULT_OUTPUT_DIR / f"question_{question_id}"
    output_dir.mkdir(parents=True, exist_ok=True)

    limit_str = f"前 {max_answers} 个" if max_answers is not None else "全部"

    print("=" * 60)
    print(f"📚 开始爬取问题: {question_id}")
    print(f"   问题链接: https://www.zhihu.com/question/{question_id}")
    print(f"   爬取数量: {limit_str}")
    print(f"   输出目录: {output_dir.resolve()}")
    print(f"   下载图片: {'是' if download_img else '否'}")
    print(f"   请求延迟: {delay_min}-{delay_max} 秒")
    print("=" * 60)

    async with async_playwright() as pw:
        context = await create_browser_context(pw, headless=headless)

        try:
            page = context.pages[0] if context.pages else await context.new_page()

            # ── 收集回答链接 ──
            print("\n📝 正在收集回答列表...")
            answer_urls = await collect_question_answer_links(page, question_id, max_answers)
            print(f"   共发现 {len(answer_urls)} 个回答")

            if not answer_urls:
                print("\n⚠️  未发现任何回答，请检查问题 ID 是否正确。")
                return

            total = len(answer_urls)
            print(f"\n🚀 共计 {total} 个回答待爬取\n")

            # ── 保存链接列表 ──
            links_file = output_dir / "links.json"
            links_data = [{"url": url, "type": "answer"} for url in answer_urls]
            _atomic_write_json(links_file, links_data, indent=2)
            print(f"📋 链接列表已保存到: {links_file}\n")

            # ── 断点续传 ──
            progress_file = output_dir / "progress.json"
            done_items = _load_verified_progress(
                output_dir,
                progress_file,
                ("answers",),
            )
            done_urls = set(done_items)

            if done_urls:
                matched = sum(1 for u in answer_urls if u in done_urls)
                print(f"📌 检测到之前的进度，已完成 {matched}/{total} 项，将跳过。\n")

            if progress_file.exists() or done_items:
                _write_progress(progress_file, output_dir, done_items)

            # ── 逐个爬取 ──
            success_count = 0
            fail_count = 0

            for idx, url in enumerate(answer_urls, 1):
                if url in done_urls:
                    print(f"[{idx}/{total}] ⏭️  跳过（已完成）: {url}")
                    success_count += 1
                    continue

                print(f"[{idx}/{total}] 📥 正在爬取回答: {url}")

                try:
                    info = await extract_answer(page, url)
                    md_path = await save_content_as_markdown(info, output_dir, download_img)
                    print(f"   💾 已保存: {md_path}")

                    success_count += 1
                    done_urls.add(url)
                    done_items[url] = md_path

                    _write_progress(progress_file, output_dir, done_items)

                except Exception as e:
                    fail_count += 1
                    print(f"   ❌ 失败: {e}")

                    if isinstance(e, AntiBotError):
                        extra_wait = 30 + random.random() * 30
                        print(f"   ⚠️  触发反爬机制，额外等待 {extra_wait:.0f} 秒...")
                        await asyncio.sleep(extra_wait)

                if idx < total:
                    delay = random_delay(delay_min, delay_max)
                    print(f"   ⏳ 等待 {delay:.1f} 秒...\n")
                    await asyncio.sleep(delay)

            # ── 问题爬取汇总 ──
            print("\n" + "=" * 60)
            print("✨ 问题回答爬取完成！")
            print(f"   成功: {success_count}")
            print(f"   失败: {fail_count}")
            print(f"   输出目录: {output_dir.resolve()}")
            print("=" * 60)

        finally:
            await context.close()


async def scrape_single_answer(
    answer_input: str,
    output_dir: Path | None = None,
    download_img: bool = True,
    with_comments: bool = False,
    delay_min: float = 10.0,
    delay_max: float = 20.0,
    headless: bool = False,
):
    """
    爬取单个知乎回答（可选附带评论区）。

    Args:
        answer_input: 回答 URL（包含 /question/xxx/answer/xxx）
        output_dir: 输出目录
        download_img: 是否下载图片
        with_comments: 是否同时爬取评论区
        delay_min: 请求间最小延迟（秒）
        delay_max: 请求间最大延迟（秒）
        headless: 是否使用无头模式
    """
    _validate_delay_range(delay_min, delay_max)

    answer_url, question_id, answer_id = parse_answer_url(answer_input)

    if output_dir is None:
        output_dir = DEFAULT_OUTPUT_DIR / f"answer_{answer_id}"
    output_dir.mkdir(parents=True, exist_ok=True)

    print("=" * 60)
    print(f"📚 爬取单个回答")
    print(f"   回答链接: {answer_url}")
    print(f"   包含评论: {'是' if with_comments else '否'}")
    print(f"   输出目录: {output_dir.resolve()}")
    print(f"   下载图片: {'是' if download_img else '否'}")
    print("=" * 60)

    async with async_playwright() as pw:
        context = await create_browser_context(pw, headless=headless)

        try:
            page = context.pages[0] if context.pages else await context.new_page()

            print(f"\n📥 正在爬取回答: {answer_url}")
            info = await extract_answer(page, answer_url)

            # 获取评论
            comments = None
            if with_comments:
                comments = await extract_comments(page, answer_id)

            md_path = await save_content_as_markdown(
                info, output_dir, download_img, comments=comments
            )
            print(f"   💾 已保存: {md_path}")

            print("\n" + "=" * 60)
            print("✨ 爬取完成！")
            print(f"   输出目录: {output_dir.resolve()}")
            print("=" * 60)

        finally:
            await context.close()


async def scrape_user_pins(
    user_url_token: str,
    output_dir: Path | None = None,
    download_img: bool = True,
    delay_min: float = 10.0,
    delay_max: float = 20.0,
    headless: bool = False,
):
    """
    爬取指定知乎用户的所有想法。

    Args:
        user_url_token: 知乎用户的 URL token
        output_dir: 输出目录
        download_img: 是否下载图片
        delay_min: 请求间最小延迟（秒）
        delay_max: 请求间最大延迟（秒）
        headless: 是否使用无头模式
    """
    _validate_delay_range(delay_min, delay_max)
    user_url_token = user_url_token.strip()
    if not user_url_token:
        raise ValueError("用户 URL Token 不能为空")

    if output_dir is None:
        output_dir = DEFAULT_OUTPUT_DIR / sanitize_filename(user_url_token)

    output_dir.mkdir(parents=True, exist_ok=True)

    print("=" * 60)
    print(f"📚 开始爬取用户想法: {user_url_token}")
    print(f"   输出目录: {output_dir.resolve()}")
    print(f"   下载图片: {'是' if download_img else '否'}")
    print(f"   请求延迟: {delay_min}-{delay_max} 秒")
    print("=" * 60)

    async with async_playwright() as pw:
        context = await create_browser_context(pw, headless=headless)

        try:
            page = context.pages[0] if context.pages else await context.new_page()

            # ── 收集想法链接 ──
            print("\n📝 正在收集想法列表...")
            pin_urls = await collect_user_pins(page, user_url_token)
            print(f"   共发现 {len(pin_urls)} 条想法")

            if not pin_urls:
                print("\n⚠️  未发现任何想法，请检查用户 URL token 是否正确。")
                return

            all_urls = [(url, "pin") for url in pin_urls]
            total = len(all_urls)
            print(f"\n🚀 共计 {total} 条想法待爬取\n")

            # ── 保存链接列表 ──
            links_file = output_dir / "pin_links.json"
            links_data = [{"url": url, "type": "pin"} for url in pin_urls]
            _atomic_write_json(links_file, links_data, indent=2)
            print(f"📋 链接列表已保存到: {links_file}\n")

            # ── 断点续传 ──
            progress_file = output_dir / "pin_progress.json"
            done_items = _load_verified_progress(
                output_dir,
                progress_file,
                ("pins",),
            )
            done_urls = set(done_items)

            if done_urls:
                matched = sum(1 for url, _ in all_urls if url in done_urls)
                if matched > 0:
                    print(f"📌 检测到之前的进度，已完成 {matched}/{total} 项，将跳过。\n")

            if progress_file.exists() or done_items:
                _write_progress(progress_file, output_dir, done_items)

            # ── 逐个爬取 ──
            success_count = 0
            fail_count = 0

            for idx, (url, content_type) in enumerate(all_urls, 1):
                if url in done_urls:
                    print(f"[{idx}/{total}] ⏭️  跳过（已完成）: {url}")
                    success_count += 1
                    continue

                print(f"[{idx}/{total}] 📥 正在爬取想法: {url}")

                try:
                    info = await extract_pin(page, url)

                    md_path = await save_content_as_markdown(info, output_dir, download_img)
                    print(f"   💾 已保存: {md_path}")

                    success_count += 1
                    done_urls.add(url)
                    done_items[url] = md_path

                    _write_progress(progress_file, output_dir, done_items)

                except Exception as e:
                    fail_count += 1
                    print(f"   ❌ 失败: {e}")

                    if isinstance(e, AntiBotError):
                        extra_wait = 30 + random.random() * 30
                        print(f"   ⚠️  触发反爬机制，额外等待 {extra_wait:.0f} 秒...")
                        await asyncio.sleep(extra_wait)

                # 请求间延迟
                if idx < total:
                    delay = random_delay(delay_min, delay_max)
                    print(f"   ⏳ 等待 {delay:.1f} 秒...\n")
                    await asyncio.sleep(delay)

            # ── 汇总 ──
            print("\n" + "=" * 60)
            print("✨ 想法爬取完成！")
            print(f"   成功: {success_count}")
            print(f"   失败: {fail_count}")
            print(f"   输出目录: {output_dir.resolve()}")
            print("=" * 60)

        finally:
            await context.close()
