#!/usr/bin/env python
"""把专家评审报告（Markdown）转成 Word。

只做**结构转换**：标题、段落、无序列表、表格、代码块。
不做任何内容增删 —— 数字必须与原 Markdown 逐字一致。

用法：
    python scripts/md_to_docx.py <in.md> <out.docx>
"""

from __future__ import annotations

import re
import sys

from docx import Document
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.shared import Pt


def main() -> int:
    src, dst = sys.argv[1], sys.argv[2]
    with open(src, encoding="utf-8") as f:
        lines = f.read().split("\n")

    doc = Document()
    style = doc.styles["Normal"]
    style.font.name = "Microsoft YaHei"
    style.font.size = Pt(10.5)

    def unquote(s: str) -> str:
        """去掉 Markdown 引用块前缀（`> ` / `>`），用于让引用块内的表格也能渲染。

        历史行为：只识别行首 `|`，导致 `> | a | b |` 这类引用块表格被当成
        普通段落输出原始竖线（v4 的 docx 里有 25 行这样的残留）。
        """
        t = s.strip()
        while t.startswith(">"):
            t = t[1:].lstrip()
        return t

    def split_cells(s: str) -> list[str]:
        r"""按未转义的 `|` 切分单元格，把 \| 还原为字面竖线。

        历史行为：朴素 split("|") 会把 `\`|ret|\`` 这类转义内容切断
        （v4/v5 的 §9.2 表格里 PC2 那一行曾被切成两个单元格）。
        """
        t = s.strip()
        if t.startswith("|"):
            t = t[1:]
        if t.endswith("|") and not t.endswith("\\|"):
            t = t[:-1]
        return [c.replace("\\|", "|").strip()
                for c in re.split(r"(?<!\\)\|", t)]

    # 合并跨行加粗：源码是硬换行排版，`**...**` 常跨两行，逐行转换会留下裸 `**`
    merged: list[str] = []
    buf: str | None = None
    for raw in lines:
        if buf is not None:
            buf += raw.strip()
            if buf.count("**") % 2 == 0:
                merged.append(buf)
                buf = None
            continue
        stripped = raw.strip()
        if (raw.count("**") % 2 == 1
                and not stripped.startswith(("|", "```", "#"))):
            buf = raw.rstrip()
            continue
        merged.append(raw)
    if buf is not None:
        merged.append(buf)
    lines = merged

    i = 0
    while i < len(lines):
        ln = lines[i].rstrip()

        # 代码块
        if ln.startswith("```"):
            i += 1
            buf = []
            while i < len(lines) and not lines[i].startswith("```"):
                buf.append(lines[i])
                i += 1
            i += 1
            p = doc.add_paragraph()
            r = p.add_run("\n".join(buf))
            r.font.name = "Consolas"
            r.font.size = Pt(9)
            continue

        # 表格（含引用块内的表格）
        head = unquote(ln)
        if head.startswith("|") and i + 1 < len(lines) and re.match(
                r"^\|[\s:|-]+\|$", unquote(lines[i + 1])):
            header = split_cells(head)
            i += 2
            rows = []
            while i < len(lines) and unquote(lines[i]).startswith("|"):
                cells = split_cells(unquote(lines[i]))
                if all(re.match(r"^:?-{2,}:?$", c) for c in cells):
                    i += 1
                    continue
                rows.append(cells)
                i += 1
            t = doc.add_table(rows=1, cols=len(header))
            t.style = "Table Grid"
            for j, h in enumerate(header):
                cell = t.rows[0].cells[j]
                cell.text = ""
                add_rich(cell.paragraphs[0], h, bold=True, size=Pt(9))
            for row in rows:
                cells_row = t.add_row().cells
                for j in range(min(len(row), len(header))):
                    cells_row[j].text = ""
                    add_rich(cells_row[j].paragraphs[0], row[j], size=Pt(9))
            doc.add_paragraph()
            continue

        # 标题
        m = re.match(r"^(#{1,4})\s+(.*)$", ln)
        if m:
            lvl = len(m.group(1))
            doc.add_heading(clean(m.group(2).strip()), level=min(lvl, 4))
            i += 1
            continue

        # 分隔线
        if re.match(r"^-{3,}$", ln.strip()):
            i += 1
            continue

        # 引用
        if ln.startswith(">"):
            txt = ln.lstrip("> ").strip()
            if txt:
                p = doc.add_paragraph()
                p.paragraph_format.left_indent = Pt(18)
                add_rich(p, txt, italic=True)
            i += 1
            continue

        # 列表
        if re.match(r"^\s*[-*]\s+", ln):
            txt = re.sub(r"^\s*[-*]\s+", "", ln)
            add_rich(doc.add_paragraph(style="List Bullet"), txt)
            i += 1
            continue
        m = re.match(r"^\s*(\d+)\.\s+(.*)$", ln)
        if m:
            # ⚠️ 不用 Word 的 "List Number" 自动编号：一篇里有多处有序列表时，
            #    Word 会把它们连成一条编号序列（第二章从 7 开始），而源码里
            #    每块都从 1 开始。手写编号 + 悬挂缩进，与 Markdown 逐字一致，
            #    也让「数字 token 数」在 md 与 docx 之间可以对上。
            para = doc.add_paragraph()
            para.paragraph_format.left_indent = Pt(18)
            para.paragraph_format.first_line_indent = Pt(-14)
            add_rich(para, f"{m.group(1)}. {m.group(2)}")
            i += 1
            continue

        if ln.strip():
            add_rich(doc.add_paragraph(), ln)
        i += 1

    doc.save(dst)
    print(f"[OK] {dst}")
    return 0


def clean_plain(s: str) -> str:
    """只做符号替换，**不动**行内标记（标记由 add_rich 负责转成格式）。"""
    return (s.replace("⚠️", "[注意]").replace("✅", "[通过]")
             .replace("❌", "[否证]").replace("⭕", "[禁止]"))


def clean(s: str) -> str:
    """去掉 Markdown 行内标记，但**不动任何数字**。

    仅用于标题与表格等无法承载 run 级格式的位置。
    """
    s = re.sub(r"\*\*(.+?)\*\*", r"\1", s)
    s = re.sub(r"`(.+?)`", r"\1", s)
    s = re.sub(r"~~(.+?)~~", r"\1", s)
    return clean_plain(s)


#: 行内标记：**粗体** 与 `代码`。用非贪婪、成对匹配，避免吃掉跨段的星号。
_INLINE = re.compile(r"(\*\*.+?\*\*|`[^`]+?`)")


def add_rich(paragraph, text: str, *, italic: bool = False, bold: bool = False,
             size=None) -> None:
    """把一段带行内标记的文字写进段落，**保留粗体与等宽**。

    历史行为：v1–v5 的 docx 里所有 `**强调**` 都被剥成纯文本（`clean()` 直接
    去标记），于是「加粗 run 数 = 0」——关键结论全靠粗体分层，丢了之后整篇
    变成同权重的平铺文字。这里按 run 逐个还原。
    """
    for part in _INLINE.split(text):
        if not part:
            continue
        is_bold = part.startswith("**") and part.endswith("**") and len(part) > 4
        is_code = part.startswith("`") and part.endswith("`") and len(part) > 2
        body = part[2:-2] if is_bold else (part[1:-1] if is_code else part)
        run = paragraph.add_run(clean_plain(body))
        if is_bold or bold:
            run.bold = True
        if is_code:
            run.font.name = "Consolas"
            run.font.size = Pt(9)
        elif size is not None:
            run.font.size = size
        if italic:
            run.italic = True


if __name__ == "__main__":
    raise SystemExit(main())
