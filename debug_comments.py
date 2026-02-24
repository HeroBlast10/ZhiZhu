"""
诊断脚本：测试知乎评论 API 的实际响应格式。
"""
import asyncio
import json
from playwright.async_api import async_playwright
from stealth import STEALTH_JS
from pathlib import Path

BROWSER_DATA_DIR = Path("browser_data")
ANSWER_ID = "1987244067499828553"


async def main():
    async with async_playwright() as pw:
        context = await pw.chromium.launch_persistent_context(
            user_data_dir=str(BROWSER_DATA_DIR),
            headless=False,
            viewport={"width": 1280, "height": 800},
            locale="zh-CN",
        )
        page = context.pages[0] if context.pages else await context.new_page()
        await page.add_init_script(STEALTH_JS)

        # 先访问知乎页面（确保在知乎域名下）
        url = f"https://www.zhihu.com/question/319652618/answer/{ANSWER_ID}"
        print(f"访问: {url}")
        await page.goto(url, wait_until="domcontentloaded")
        await asyncio.sleep(5)

        # 关闭弹窗
        for sel in [
            'button:has-text("关闭")',
            'button:has-text("我知道了")',
            ".Modal-closeButton",
        ]:
            try:
                btn = page.locator(sel).first
                if await btn.count() > 0 and await btn.is_visible():
                    await btn.click()
                    await asyncio.sleep(0.5)
            except Exception:
                pass

        # 测试 comment_v5 API
        print("\n=== 测试 comment_v5 API ===")
        api_url = f"https://www.zhihu.com/api/v4/comment_v5/answers/{ANSWER_ID}/root_comment?order_by=score&limit=20&offset="
        print(f"请求: {api_url}")

        result = await page.evaluate("""
            async (url) => {
                try {
                    const resp = await fetch(url, { credentials: 'include' });
                    const status = resp.status;
                    const headers = {};
                    resp.headers.forEach((v, k) => headers[k] = v);
                    const text = await resp.text();
                    return { status, headers, body: text.substring(0, 3000) };
                } catch (e) {
                    return { error: e.message };
                }
            }
        """, api_url)

        print(f"状态码: {result.get('status')}")
        if result.get('error'):
            print(f"错误: {result['error']}")
        else:
            body = result.get('body', '')
            print(f"响应体前 3000 字符:")
            try:
                parsed = json.loads(body)
                print(json.dumps(parsed, ensure_ascii=False, indent=2)[:3000])
            except:
                print(body[:3000])

        # 测试旧版 API
        print("\n=== 测试旧版 comments API ===")
        old_api_url = f"https://www.zhihu.com/api/v4/answers/{ANSWER_ID}/comments?limit=20&offset=0&order_by=normal&status=open"
        print(f"请求: {old_api_url}")

        result2 = await page.evaluate("""
            async (url) => {
                try {
                    const resp = await fetch(url, { credentials: 'include' });
                    const status = resp.status;
                    const text = await resp.text();
                    return { status, body: text.substring(0, 3000) };
                } catch (e) {
                    return { error: e.message };
                }
            }
        """, old_api_url)

        print(f"状态码: {result2.get('status')}")
        if result2.get('error'):
            print(f"错误: {result2['error']}")
        else:
            body2 = result2.get('body', '')
            try:
                parsed2 = json.loads(body2)
                print(json.dumps(parsed2, ensure_ascii=False, indent=2)[:3000])
            except:
                print(body2[:3000])

        # 测试 comment_v5 不带 offset
        print("\n=== 测试 comment_v5 不带 offset ===")
        api_url3 = f"https://www.zhihu.com/api/v4/comment_v5/answers/{ANSWER_ID}/root_comment?order_by=score&limit=20"
        print(f"请求: {api_url3}")

        result3 = await page.evaluate("""
            async (url) => {
                try {
                    const resp = await fetch(url, { credentials: 'include' });
                    const status = resp.status;
                    const text = await resp.text();
                    return { status, body: text.substring(0, 3000) };
                } catch (e) {
                    return { error: e.message };
                }
            }
        """, api_url3)

        print(f"状态码: {result3.get('status')}")
        if result3.get('error'):
            print(f"错误: {result3['error']}")
        else:
            body3 = result3.get('body', '')
            try:
                parsed3 = json.loads(body3)
                print(json.dumps(parsed3, ensure_ascii=False, indent=2)[:3000])
            except:
                print(body3[:3000])

        # 测试 comment_v5 带 cursor
        print("\n=== 测试 comment_v5 带 cursor ===")
        api_url4 = f"https://www.zhihu.com/api/v4/comment_v5/answers/{ANSWER_ID}/root_comment?order_by=score&limit=5&offset="
        print(f"请求: {api_url4}")

        result4 = await page.evaluate("""
            async (url) => {
                try {
                    const resp = await fetch(url, { credentials: 'include' });
                    const status = resp.status;
                    const json_data = await resp.json();
                    return {
                        status,
                        data_length: json_data.data ? json_data.data.length : 0,
                        paging: json_data.paging || null,
                        first_comment_keys: json_data.data && json_data.data[0] ? Object.keys(json_data.data[0]) : [],
                        first_comment_id: json_data.data && json_data.data[0] ? json_data.data[0].id : null,
                        first_comment_content: json_data.data && json_data.data[0] ? (json_data.data[0].content || '').substring(0, 100) : null,
                        first_comment_author: json_data.data && json_data.data[0] && json_data.data[0].author ? json_data.data[0].author : null,
                    };
                } catch (e) {
                    return { error: e.message };
                }
            }
        """, api_url4)

        print(f"状态码: {result4.get('status')}")
        print(f"data 长度: {result4.get('data_length')}")
        print(f"paging: {json.dumps(result4.get('paging'), ensure_ascii=False, indent=2) if result4.get('paging') else 'None'}")
        print(f"第一条评论 keys: {result4.get('first_comment_keys')}")
        print(f"第一条评论 id: {result4.get('first_comment_id')}")
        print(f"第一条评论内容: {result4.get('first_comment_content')}")
        print(f"第一条评论作者: {json.dumps(result4.get('first_comment_author'), ensure_ascii=False, indent=2) if result4.get('first_comment_author') else 'None'}")

        input("\n按 Enter 关闭浏览器...")
        await context.close()


if __name__ == "__main__":
    asyncio.run(main())
