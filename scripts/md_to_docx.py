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

        # 表格
        if ln.startswith("|") and i + 1 < len(lines) and re.match(r"^\|[\s:|-]+\|$",
                                                                  lines[i + 1].strip()):
            header = [c.strip() for c in ln.strip("|").split("|")]
            i += 2
            rows = []
            while i < len(lines) and lines[i].strip().startswith("|"):
                cells = [c.strip() for c in lines[i].strip().strip("|").split("|")]
                if all(re.match(r"^:?-{2,}:?$", c) for c in cells):
                    i += 1
                    continue
                rows.append(cells)
                i += 1
            t = doc.add_table(rows=1, cols=len(header))
            t.style = "Table Grid"
            for j, h in enumerate(header):
                cell = t.rows[0].cells[j]
                cell.text = h
                for r_ in cell.paragraphs[0].runs:
                    r_.bold = True
                    r_.font.size = Pt(9)
            for row in rows:
                cells_row = t.add_row().cells
                for j in range(min(len(row), len(header))):
                    cells_row[j].text = row[j]
                    for r_ in cells_row[j].paragraphs[0].runs:
                        r_.font.size = Pt(9)
            doc.add_paragraph()
            continue

        # 标题
        m = re.match(r"^(#{1,4})\s+(.*)$", ln)
        if m:
            lvl = len(m.group(1))
            doc.add_heading(m.group(2).strip(), level=min(lvl, 4))
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
                r = p.add_run(txt)
                r.italic = True
            i += 1
            continue

        # 列表
        if re.match(r"^\s*[-*]\s+", ln):
            txt = re.sub(r"^\s*[-*]\s+", "", ln)
            doc.add_paragraph(clean(txt), style="List Bullet")
            i += 1
            continue
        m = re.match(r"^\s*(\d+)\.\s+(.*)$", ln)
        if m:
            doc.add_paragraph(clean(m.group(2)), style="List Number")
            i += 1
            continue

        if ln.strip():
            doc.add_paragraph(clean(ln))
        i += 1

    doc.save(dst)
    print(f"[OK] {dst}")
    return 0


def clean(s: str) -> str:
    """去掉 Markdown 行内标记，但**不动任何数字**。"""
    s = re.sub(r"\*\*(.+?)\*\*", r"\1", s)
    s = re.sub(r"`(.+?)`", r"\1", s)
    s = re.sub(r"~~(.+?)~~", r"\1", s)
    return s.replace("⚠️", "[注意]").replace("✅", "[通过]").replace("❌", "[否证]")


if __name__ == "__main__":
    raise SystemExit(main())
