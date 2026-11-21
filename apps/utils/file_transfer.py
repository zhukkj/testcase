import shutil

import pypandoc


def _has_content(path):
    try:
        with open(path, "r", encoding="utf-8") as f:
            return bool(f.read().strip())
    except OSError:
        return False


def _docx_to_markdown_by_python_docx(input_file):
    """不依赖 pandoc 的兜底方案：用 python-docx 提取段落与表格。

    说明：pandoc 能保留更完整的排版语义（标题层级、列表、加粗等），
    这里只是纯文本级别提取，但对 LLM 分析需求文档而言已经足够。
    """
    from docx import Document

    doc = Document(input_file)
    lines = []
    for para in doc.paragraphs:
        text = para.text.strip()
        if not text:
            continue
        style = (para.style.name or "").lower()
        if style.startswith("heading"):
            # Heading 1 / Heading 2 ... 映射为 Markdown 标题层级
            level = "".join(ch for ch in style if ch.isdigit()) or "1"
            lines.append(f"{'#' * min(int(level), 6)} {text}")
        else:
            lines.append(text)
    for table in doc.tables:
        for row in table.rows:
            cells = [c.text.strip().replace("\n", " ") for c in row.cells]
            lines.append("| " + " | ".join(cells) + " |")
        lines.append("")
    return "\n\n".join(lines)


def word_to_markdown(input_file, output_file):
    """将 Word 文档转换为 Markdown。

    优先使用 pandoc（排版还原度高）；未安装 pandoc 时自动降级到 python-docx。
    转换失败会抛出异常，避免上层读到不存在的文件才报错。
    """
    if shutil.which("pandoc"):
        pypandoc.convert_file(input_file, "markdown", outputfile=output_file)
        if not _has_content(output_file):
            raise RuntimeError(f"pandoc 转换结果为空: {input_file}")
        return output_file

    print("未检测到 pandoc，使用 python-docx 兜底转换")
    markdown = _docx_to_markdown_by_python_docx(input_file)
    if not markdown.strip():
        raise RuntimeError(f"文档中未提取到任何文本内容: {input_file}")
    with open(output_file, "w", encoding="utf-8") as f:
        f.write(markdown)
    return output_file
