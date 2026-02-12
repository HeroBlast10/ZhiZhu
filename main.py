"""
main.py — 知蛛 (ZhiZhu) 知乎内容爬虫入口

使用方法:
    # 登录知乎（首次使用需要）
    python main.py login

    # 爬取用户所有回答和文章
    python main.py scrape <user_url_token>

    # 爬取某个问题下的回答
    python main.py question <问题URL或ID> [-n 20]

    # 爬取单个回答（可选附带评论）
    python main.py answer <回答URL> [--with-comments]

免责声明：
    本工具仅供学习研究使用，请遵守知乎的使用条款。
    请勿用于商业用途或大规模爬取。
"""

import argparse
import asyncio
import sys

from scraper import login, scrape_user, scrape_question, scrape_single_answer
from pathlib import Path


def _add_common_args(parser: argparse.ArgumentParser):
    """为子命令添加公共参数。"""
    parser.add_argument(
        "--output", "-o",
        type=str,
        default=None,
        help="输出目录路径",
    )
    parser.add_argument(
        "--no-images",
        action="store_true",
        help="不下载图片",
    )
    parser.add_argument(
        "--delay-min",
        type=float,
        default=10.0,
        help="请求间最小延迟秒数（默认 10）",
    )
    parser.add_argument(
        "--delay-max",
        type=float,
        default=20.0,
        help="请求间最大延迟秒数（默认 20）",
    )
    parser.add_argument(
        "--headless",
        action="store_true",
        help="使用无头模式（不显示浏览器窗口）",
    )


def main():
    parser = argparse.ArgumentParser(
        description="知蛛 (ZhiZhu) — 知乎内容爬虫，保存为 Markdown",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
使用示例:
  # 登录知乎（首次使用需要，登录状态会保存）
  python main.py login

  # 爬取某用户的所有回答和文章
  python main.py scrape zhang-jia-wei

  # 只爬取回答
  python main.py scrape zhang-jia-wei --only-answers

  # 爬取某个问题下的前 20 个回答
  python main.py question https://www.zhihu.com/question/12345 -n 20

  # 爬取某个问题下的全部回答
  python main.py question 12345

  # 爬取单个回答
  python main.py answer https://www.zhihu.com/question/12345/answer/67890

  # 爬取单个回答及其评论区
  python main.py answer https://www.zhihu.com/question/12345/answer/67890 --with-comments
        """,
    )

    subparsers = parser.add_subparsers(dest="command", help="可用命令")

    # ── login 子命令 ──
    login_parser = subparsers.add_parser("login", help="登录知乎（手动登录，保存 Cookie）")
    login_parser.add_argument(
        "--timeout",
        type=int,
        default=300,
        help="等待登录的超时时间（秒），默认 300",
    )

    # ── scrape 子命令（用户级） ──
    scrape_parser = subparsers.add_parser("scrape", help="爬取指定用户的回答和文章")
    scrape_parser.add_argument(
        "user_url_token",
        type=str,
        help="知乎用户的 URL token（个人主页 URL 中的标识符）",
    )
    scrape_parser.add_argument(
        "--only-answers",
        action="store_true",
        help="只爬取回答",
    )
    scrape_parser.add_argument(
        "--only-articles",
        action="store_true",
        help="只爬取文章",
    )
    _add_common_args(scrape_parser)

    # ── question 子命令（问题级） ──
    question_parser = subparsers.add_parser("question", help="爬取指定问题下的回答")
    question_parser.add_argument(
        "question_input",
        type=str,
        help="知乎问题 URL 或问题 ID",
    )
    question_parser.add_argument(
        "-n", "--max-answers",
        type=int,
        default=None,
        help="最多爬取的回答数量（默认全部）",
    )
    _add_common_args(question_parser)

    # ── answer 子命令（单个回答） ──
    answer_parser = subparsers.add_parser("answer", help="爬取单个回答（可选附带评论）")
    answer_parser.add_argument(
        "answer_url",
        type=str,
        help="知乎回答 URL（格式: .../question/xxx/answer/xxx）",
    )
    answer_parser.add_argument(
        "--with-comments",
        action="store_true",
        help="同时爬取评论区",
    )
    _add_common_args(answer_parser)

    args = parser.parse_args()

    if args.command is None:
        parser.print_help()
        sys.exit(0)

    if args.command == "login":
        asyncio.run(login(timeout=args.timeout))

    elif args.command == "scrape":
        scrape_answers = True
        scrape_articles = True

        if args.only_answers:
            scrape_articles = False
        if args.only_articles:
            scrape_answers = False

        output_dir = Path(args.output) if args.output else None

        asyncio.run(
            scrape_user(
                user_url_token=args.user_url_token,
                output_dir=output_dir,
                scrape_answers=scrape_answers,
                scrape_articles=scrape_articles,
                download_img=not args.no_images,
                delay_min=args.delay_min,
                delay_max=args.delay_max,
                headless=args.headless,
            )
        )

    elif args.command == "question":
        output_dir = Path(args.output) if args.output else None

        asyncio.run(
            scrape_question(
                question_input=args.question_input,
                max_answers=args.max_answers,
                output_dir=output_dir,
                download_img=not args.no_images,
                delay_min=args.delay_min,
                delay_max=args.delay_max,
                headless=args.headless,
            )
        )

    elif args.command == "answer":
        output_dir = Path(args.output) if args.output else None

        asyncio.run(
            scrape_single_answer(
                answer_input=args.answer_url,
                output_dir=output_dir,
                download_img=not args.no_images,
                with_comments=args.with_comments,
                delay_min=args.delay_min,
                delay_max=args.delay_max,
                headless=args.headless,
            )
        )


if __name__ == "__main__":
    main()
