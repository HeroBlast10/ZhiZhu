"""
merge_md.py — 将 output 中某个文件夹的所有 .md 文件合并为一个 Markdown 文件。

兼容两种目录结构：
  - 普通模式（有图片）：<类型目录>/<子文件夹>/index.md
  - --no-images 模式  ：<类型目录>/<日期_标题>.md

用法：
    # 合并某个用户的所有回答
    python merge_md.py output/heroblast/answers

    # 合并某个问题下的所有回答，指定输出文件名
    python merge_md.py output/question_46631426/answers -o merged_gp.md

    # 合并整个用户文件夹（answers + articles + pins）
    python merge_md.py output/heroblast

    # 按文件名升序排序（默认按文件头部日期排序）
    python merge_md.py output/heroblast/answers --sort-by name

    # 指定分隔符（默认为 ---）
    python merge_md.py output/heroblast/answers --separator "====="
"""

import argparse
import re
import sys
from pathlib import Path


# ── 工具函数 ──────────────────────────────────────────────────

def collect_md_files(root: Path) -> list[Path]:
    """
    从指定根目录中收集所有 .md 文件。
    支持两种结构：
      - <root>/<子文件夹>/index.md
      - <root>/<文件>.md
    """
    files: list[Path] = []
    for path in root.rglob("*.md"):
        files.append(path)
    return files


def extract_date_from_header(text: str) -> str:
    """
    从 Markdown 文件头部的元信息中提取日期字符串（YYYY-MM-DD）。
    格式如：> **日期**: 2025-12-24
    找不到时返回空字符串。
    """
    m = re.search(r'>\s*\*\*日期\*\*:\s*(\d{4}-\d{2}-\d{2})', text[:600])
    return m.group(1) if m else ""


def sort_key_by_date(path: Path) -> tuple[str, str]:
    """按文件头部日期排序，日期相同时按文件路径排序。"""
    try:
        text = path.read_text(encoding="utf-8")
    except Exception:
        text = ""
    date = extract_date_from_header(text)
    return (date, str(path))


def sort_key_by_name(path: Path) -> str:
    """按文件路径字母顺序排序。"""
    return str(path)


# ── 合并逻辑 ──────────────────────────────────────────────────

def merge(
    source_dir: Path,
    output_file: Path,
    sort_by: str = "date",
    separator: str = "---",
    title: str = "",
):
    """
    合并 source_dir 下所有 .md 文件到 output_file。

    Args:
        source_dir: 要扫描的根目录
        output_file: 合并后的输出文件路径
        sort_by: 排序方式，"date"（按日期）或 "name"（按文件名）
        separator: 文件间的分隔符
        title: 合并文件的总标题（为空则自动生成）
    """
    if not source_dir.exists():
        print(f"[ERR] 目录不存在: {source_dir}", file=sys.stderr)
        sys.exit(1)

    files = collect_md_files(source_dir)
    if not files:
        print(f"[WARN] 目录中未找到任何 .md 文件: {source_dir}", file=sys.stderr)
        sys.exit(1)

    # 排序
    if sort_by == "date":
        files.sort(key=sort_key_by_date)
    else:
        files.sort(key=sort_key_by_name)

    # 总标题
    if not title:
        title = f"{source_dir.resolve().name} — 合并文档"

    # 构建合并内容
    parts: list[str] = [f"# {title}\n\n"]
    parts.append(
        f"> 共 {len(files)} 篇，来源目录：`{source_dir.resolve()}`\n\n"
    )
    parts.append(f"{separator}\n\n")

    sep_block = f"\n\n{separator}\n\n"

    for i, md_file in enumerate(files):
        try:
            content = md_file.read_text(encoding="utf-8").strip()
        except Exception as e:
            print(f"[WARN] 读取失败，已跳过: {md_file}  ({e})", file=sys.stderr)
            continue

        # 把每个文件的内容追加进去
        parts.append(content)

        if i < len(files) - 1:
            parts.append(sep_block)

    merged = "".join(parts) + "\n"

    # 确保输出目录存在
    output_file.parent.mkdir(parents=True, exist_ok=True)
    output_file.write_text(merged, encoding="utf-8")

    print(f"[OK] 合并完成：共 {len(files)} 个文件 -> {output_file.resolve()}")


# ── 命令行入口 ────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="将 output 中某个目录的所有 .md 文件合并为单个 Markdown 文件",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例：
  # 合并某用户的所有回答
  python merge_md.py output/heroblast/answers

  # 合并某问题下的回答，自定义输出文件名
  python merge_md.py output/question_46631426/answers -o merged_gp.md

  # 合并整个用户文件夹（answers + articles + pins）
  python merge_md.py output/heroblast

  # 按文件名排序
  python merge_md.py output/heroblast/answers --sort-by name

  # 自定义总标题
  python merge_md.py output/heroblast/answers --title "我的知乎回答合集"
        """,
    )
    parser.add_argument(
        "source",
        type=str,
        help="要合并的目录路径（相对或绝对均可）",
    )
    parser.add_argument(
        "--output", "-o",
        type=str,
        default=None,
        help="输出文件路径（默认：<source目录名>_merged.md，保存在 source 同级）",
    )
    parser.add_argument(
        "--sort-by",
        choices=["date", "name"],
        default="date",
        help="排序方式：date（按文件内日期，默认）或 name（按文件名）",
    )
    parser.add_argument(
        "--separator",
        type=str,
        default="---",
        help="文件间的分隔符（默认：---）",
    )
    parser.add_argument(
        "--title",
        type=str,
        default="",
        help="合并文件的总标题（默认：自动生成）",
    )

    args = parser.parse_args()

    source_dir = Path(args.source)

    if args.output:
        output_file = Path(args.output)
    else:
        # 默认输出到 source 目录的同级，文件名为 <目录名>_merged.md
        output_file = source_dir.parent / f"{source_dir.name}_merged.md"

    merge(
        source_dir=source_dir,
        output_file=output_file,
        sort_by=args.sort_by,
        separator=args.separator,
        title=args.title,
    )


if __name__ == "__main__":
    main()
