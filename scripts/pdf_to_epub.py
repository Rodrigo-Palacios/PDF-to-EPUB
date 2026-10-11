#!/usr/bin/env python3
"""Turn text-based PDFs into EPUB 3 files without interactive prompts."""

from __future__ import annotations

import argparse
import html
import io
import re
import sys
import uuid
import zipfile
from contextlib import redirect_stdout
from datetime import datetime, timezone
from pathlib import Path

import fitz


CONTAINER_XML = """<?xml version="1.0" encoding="UTF-8"?>
<container version="1.0" xmlns="urn:oasis:names:tc:opendocument:xmlns:container">
  <rootfiles><rootfile full-path="EPUB/package.opf" media-type="application/oebps-package+xml"/></rootfiles>
</container>
"""

CSS = """body { font-family: serif; line-height: 1.55; margin: 5%; }
h1 { page-break-before: always; font-size: 1.25em; }
p { margin: 0 0 0.8em; text-indent: 1em; }
table { border-collapse: collapse; width: 100%; margin: 1em 0; page-break-inside: avoid; }
th, td { border: 1px solid #777; padding: 0.35em; text-align: left; vertical-align: top; }
th { background: #eee; }
.cover { margin: 0; padding: 0; text-align: center; }
.cover img { display: block; height: 100vh; max-width: 100%; object-fit: contain; margin: 0 auto; }
"""


def clean_text(value: str) -> str:
    return re.sub(r"[\u200b\u200c\u200d\ufeff]", "", value).strip()


def normalized(value: str) -> str:
    return re.sub(r"\s+", " ", clean_text(value)).casefold().strip()


def make_page_files(page_count: int) -> dict[int, str]:
    return {index: ("cover.xhtml" if index == 0 else f"page-{index + 1:04}.xhtml") for index in range(page_count)}


def outline_entries(pdf: fitz.Document, page_files: dict[int, str]) -> tuple[list[dict], dict[int, str]]:
    entries: list[dict] = []
    titles_by_page: dict[int, str] = {}
    for level, raw_title, page_number, details in pdf.get_toc(simple=False):
        title = clean_text(raw_title)
        target_page = page_number - 1
        if not title or target_page <= 0 or target_page not in page_files:
            continue
        point = details.get("to")
        target_y = point.y if isinstance(point, fitz.Point) and point.y >= 0 else None
        entries.append({
            "level": max(1, level),
            "title": title,
            "page": target_page,
            "y": target_y,
            "anchor": f"chapter-{len(entries) + 1}",
        })
        titles_by_page.setdefault(target_page, title)

    if not entries:
        entries = [
            {"level": 1, "title": f"Página {index + 1}", "page": index, "y": None, "anchor": f"chapter-{index + 1}"}
            for index in range(1, len(page_files))
        ]
    return entries, titles_by_page


def toc_tree(entries: list[dict]) -> list[dict]:
    roots: list[dict] = []
    stack: list[tuple[int, list[dict]]] = [(0, roots)]
    for entry in entries:
        level = max(1, entry["level"])
        while len(stack) > 1 and stack[-1][0] >= level:
            stack.pop()
        if level > stack[-1][0] + 1:
            level = stack[-1][0] + 1
        node = {**entry, "children": []}
        stack[-1][1].append(node)
        stack.append((level, node["children"]))
    return roots


def render_toc(nodes: list[dict], page_files: dict[int, str]) -> str:
    items = []
    for node in nodes:
        href = html.escape(f'{page_files[node["page"]]}#{node["anchor"]}', quote=True)
        title = html.escape(node["title"])
        children = f"<ol>{render_toc(node['children'], page_files)}</ol>" if node["children"] else ""
        items.append(f'<li><a href="{href}">{title}</a>{children}</li>')
    return "".join(items)


def cover_xhtml(title: str) -> bytes:
    document = (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<!DOCTYPE html>\n<html xmlns="http://www.w3.org/1999/xhtml" '
        'xmlns:epub="http://www.idpf.org/2007/ops" lang="es">\n'
        f"<head><title>{html.escape(title)}</title><link rel=\"stylesheet\" "
        'type="text/css" href="styles.css"/></head>\n'
        f'<body epub:type="cover" class="cover"><img src="cover.png" alt="{html.escape(title, quote=True)}"/></body>\n'
        '</html>'
    )
    return document.encode("utf-8")


def xhtml_page(title: str, paragraphs: list[str], heading_ids: list[str] | None = None) -> bytes:
    body = "\n".join(paragraphs)
    ids = "".join(f'<span id="{html.escape(anchor, quote=True)}"></span>' for anchor in heading_ids or [])
    document = (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<!DOCTYPE html>\n<html xmlns="http://www.w3.org/1999/xhtml" '
        'xmlns:epub="http://www.idpf.org/2007/ops" lang="es">\n'
        f"<head><title>{html.escape(title)}</title><link rel=\"stylesheet\" "
        'type="text/css" href="styles.css"/></head>\n'
        f"<body><h1>{ids}{html.escape(title)}</h1>{body}</body>\n</html>"
    )
    return document.encode("utf-8")


def table_html(table: fitz.table.Table) -> str:
    rows = table.extract()
    header = table.header
    names = [clean_text(name or "") for name in (header.names or [])]
    has_header = len(names) == table.col_count and any(names)
    if has_header and not header.external and rows:
        rows = rows[1:]

    def cell(value: str | None) -> str:
        return html.escape(clean_text(value or "")).replace("\n", "<br />")

    head = ""
    if has_header:
        head = "<thead><tr>" + "".join(f'<th scope="col">{cell(name)}</th>' for name in names) + "</tr></thead>"

    body_rows = []
    for row in rows:
        values = [cell(value) for value in row]
        populated = [value for value in values if value]
        if len(populated) == 1 and len(values) > 1:
            body_rows.append(f'<tr><th scope="rowgroup" colspan="{len(values)}">{populated[0]}</th></tr>')
        else:
            body_rows.append("<tr>" + "".join(f"<td>{value}</td>" for value in values) + "</tr>")
    return f'<table class="pdf-table">{head}<tbody>{"".join(body_rows)}</tbody></table>'


def link_href(
    link: dict,
    line_text: str,
    source_page: int,
    page_files: dict[int, str],
    toc_entries: list[dict],
) -> str | None:
    uri = link.get("uri", "")
    if uri.startswith(("https://", "http://", "mailto:")):
        return uri

    target = link.get("page", -1)
    visible = normalized(line_text)
    matching_entry = next(
        (
            entry for entry in toc_entries
            if normalized(entry["title"]) and normalized(entry["title"]) in visible
        ),
        None,
    )
    if matching_entry:
        target = matching_entry["page"]
    if target <= 0 or target not in page_files:
        return None
    target_entry = matching_entry or next((entry for entry in toc_entries if entry["page"] == target), None)
    suffix = f'#{target_entry["anchor"]}' if target_entry else ""
    if target in page_files:
        return f"{page_files[target]}{suffix}"
    return None


def page_paragraphs(
    page: fitz.Page,
    page_number: int,
    page_files: dict[int, str],
    toc_entries: list[dict],
) -> tuple[list[str], list[str], int]:
    content_blocks: list[tuple[float, str]] = []
    blocks = page.get_text("dict", sort=True).get("blocks", [])
    with redirect_stdout(io.StringIO()):
        tables = page.find_tables(strategy="lines").tables
    table_regions = [fitz.Rect(table.bbox) for table in tables]
    text_lines: dict[tuple[int, int], str] = {}
    line_rects: dict[tuple[int, int], fitz.Rect] = {}
    for block_index, block in enumerate(blocks):
        for line_index, line in enumerate(block.get("lines", [])):
            key = (block_index, line_index)
            text_lines[key] = clean_text("".join(span.get("text", "") for span in line["spans"]))
            line_rects[key] = fitz.Rect(line["bbox"])

    chapter_ids_by_line: dict[tuple[int, int], list[str]] = {}
    heading_ids: list[str] = []
    line_keys = [
        key for key, rect in line_rects.items()
        if text_lines[key] and not any(region.contains(fitz.Point((rect.x0 + rect.x1) / 2, (rect.y0 + rect.y1) / 2)) for region in table_regions)
    ]
    for entry in toc_entries:
        if entry["page"] != page_number:
            continue
        if not line_keys:
            heading_ids.append(entry["anchor"])
            continue
        target_y = entry.get("y")
        if target_y is None:
            heading_ids.append(entry["anchor"])
            continue
        nearest = min(line_keys, key=lambda key: abs(line_rects[key].y0 - target_y))
        chapter_ids_by_line.setdefault(nearest, []).append(entry["anchor"])

    # Resolve a link against all text in its rectangle so split title/page-number
    # spans remain one clickable table-of-contents entry.
    linked_lines: dict[tuple[int, int], tuple[int, str, str]] = {}
    for link_index, link in enumerate(page.get_links()):
        rect = fitz.Rect(link.get("from", (0, 0, 0, 0)))
        keys = [key for key, line_rect in line_rects.items() if text_lines[key] and rect.intersects(line_rect)]
        if not keys:
            continue
        visible_text = " ".join(text_lines[key] for key in keys)
        href = link_href(link, visible_text, page_number, page_files, toc_entries)
        if not href:
            continue
        group_text = " ".join(text_lines[key] for key in keys)
        for key in keys:
            linked_lines.setdefault(key, (link_index, href, group_text))

    emitted_links: set[int] = set()
    for block_index, block in enumerate(blocks):
        if "lines" not in block:
            continue
        lines: list[str] = []
        for line_index, _line in enumerate(block["lines"]):
            key = (block_index, line_index)
            text = text_lines[key]
            if not text:
                continue
            rect = line_rects[key]
            if any(region.contains(fitz.Point((rect.x0 + rect.x1) / 2, (rect.y0 + rect.y1) / 2)) for region in table_regions):
                continue
            linked = linked_lines.get(key)
            if linked:
                link_index, href, group_text = linked
                if link_index in emitted_links:
                    continue
                emitted_links.add(link_index)
                group_ids = []
                for group_key, candidate in linked_lines.items():
                    if candidate[0] == link_index:
                        group_ids.extend(chapter_ids_by_line.get(group_key, []))
                prefix = "".join(f'<span id="{html.escape(anchor, quote=True)}"></span>' for anchor in dict.fromkeys(group_ids))
                lines.append(f'{prefix}<a href="{html.escape(href, quote=True)}">{html.escape(group_text)}</a>')
            else:
                prefix = "".join(
                    f'<span id="{html.escape(anchor, quote=True)}"></span>'
                    for anchor in chapter_ids_by_line.get(key, [])
                )
                lines.append(f"{prefix}{html.escape(text)}")
        if lines:
            content_blocks.append((float(block["bbox"][1]), f'<p>{" ".join(lines)}</p>'))

    for table in tables:
        content_blocks.append((float(table.bbox[1]), table_html(table)))
    content_blocks.sort(key=lambda item: item[0])
    return [content for _, content in content_blocks], heading_ids, len(tables)


def convert_pdf(source: Path, destination: Path) -> tuple[int, int, int]:
    """Convert a PDF, preserving its first-page cover, outline, and internal links."""
    with fitz.open(source) as pdf:
        if pdf.needs_pass:
            raise ValueError("el PDF está protegido con contraseña")
        if pdf.page_count == 0:
            raise ValueError("el PDF no contiene páginas")

        title = clean_text((pdf.metadata or {}).get("title", "")) or source.stem
        page_files = make_page_files(pdf.page_count)
        toc_entries, titles_by_page = outline_entries(pdf, page_files)
        cover = pdf[0].get_pixmap(matrix=fitz.Matrix(2, 2), alpha=False).tobytes("png")
        page_documents: list[tuple[str, bytes]] = []
        preserved_link_count = 0
        preserved_table_count = 0
        for index in range(1, pdf.page_count):
            page = pdf[index]
            page_title = titles_by_page.get(index, f"{title} — página {index + 1}")
            paragraphs, heading_ids, table_count = page_paragraphs(page, index, page_files, toc_entries)
            preserved_table_count += table_count
            for paragraph in paragraphs:
                preserved_link_count += paragraph.count("<a href=")
            page_documents.append((page_files[index], xhtml_page(page_title, paragraphs, heading_ids)))

        book_id = f"urn:uuid:{uuid.uuid4()}"
        modified = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        manifest_items = [
            '<item id="nav" href="nav.xhtml" media-type="application/xhtml+xml" properties="nav"/>',
            '<item id="css" href="styles.css" media-type="text/css"/>',
            '<item id="cover-page" href="cover.xhtml" media-type="application/xhtml+xml"/>',
            '<item id="cover-image" href="cover.png" media-type="image/png" properties="cover-image"/>',
        ]
        spine_items = ['<itemref idref="cover-page"/>']
        for index, (filename, _) in enumerate(page_documents, start=2):
            page_id = f"page{index}"
            manifest_items.append(
                f'<item id="{page_id}" href="{filename}" media-type="application/xhtml+xml"/>'
            )
            spine_items.append(f'<itemref idref="{page_id}"/>')

        package = (
            '<?xml version="1.0" encoding="UTF-8"?>\n'
            '<package xmlns="http://www.idpf.org/2007/opf" version="3.0" '
            'unique-identifier="book-id" xml:lang="es">\n'
            f"<metadata xmlns:dc=\"http://purl.org/dc/elements/1.1/\">"
            f'<dc:identifier id="book-id">{book_id}</dc:identifier>'
            f"<dc:title>{html.escape(title)}</dc:title><dc:language>es</dc:language>"
            f'<meta property="dcterms:modified">{modified}</meta></metadata>\n'
            f"<manifest>{''.join(manifest_items)}</manifest>\n"
            f"<spine>{''.join(spine_items)}</spine></package>"
        ).encode("utf-8")
        toc_html = render_toc(toc_tree(toc_entries), page_files)
        nav = (
            '<?xml version="1.0" encoding="UTF-8"?>\n'
            '<!DOCTYPE html>\n<html xmlns="http://www.w3.org/1999/xhtml" '
            'xmlns:epub="http://www.idpf.org/2007/ops" lang="es">'
            f"<head><title>{html.escape(title)}</title></head>"
            f'<body><nav epub:type="toc"><h1>Contenido</h1><ol>{toc_html}</ol>'
            "</nav></body></html>"
        ).encode("utf-8")

    destination.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(destination, "w") as epub:
        epub.writestr("mimetype", "application/epub+zip", compress_type=zipfile.ZIP_STORED)
        epub.writestr("META-INF/container.xml", CONTAINER_XML)
        epub.writestr("EPUB/package.opf", package)
        epub.writestr("EPUB/nav.xhtml", nav)
        epub.writestr("EPUB/styles.css", CSS)
        epub.writestr("EPUB/cover.xhtml", cover_xhtml(title))
        epub.writestr("EPUB/cover.png", cover)
        for filename, content in page_documents:
            epub.writestr(f"EPUB/{filename}", content)
    return len(page_documents) + 1, preserved_link_count, preserved_table_count


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, help="convert one PDF")
    parser.add_argument("--output", type=Path, help="destination EPUB for --input")
    parser.add_argument("--input-dir", type=Path, default=Path("input"), help="PDF folder for batch conversion")
    parser.add_argument("--output-dir", type=Path, default=Path("output"), help="EPUB folder for batch conversion")
    args = parser.parse_args()
    if args.output and not args.input:
        parser.error("--output requiere --input")
    if args.input and args.input.suffix.lower() != ".pdf":
        parser.error("--input debe apuntar a un archivo PDF")
    return args


def main() -> int:
    args = parse_args()
    if args.input:
        if not args.input.is_file():
            print(f"No se encontró el PDF: {args.input}", file=sys.stderr)
            return 2
        destination = args.output or (args.output_dir / f"{args.input.stem}.epub")
        sources = [(args.input, destination)]
    else:
        if not args.input_dir.is_dir():
            print(f"No existe {args.input_dir}; coloca allí los PDF que quieras convertir.", file=sys.stderr)
            return 2
        pdfs = sorted(path for path in args.input_dir.rglob("*") if path.is_file() and path.suffix.lower() == ".pdf")
        if not pdfs:
            print(f"No se encontraron PDF en {args.input_dir}; coloca allí los archivos que quieras convertir.")
            return 0
        sources = [
            (source, args.output_dir / source.relative_to(args.input_dir).with_suffix(".epub"))
            for source in pdfs
        ]

    failed = 0
    for source, destination in sources:
        try:
            pages, links, tables = convert_pdf(source, destination)
            print(f"OK {source} -> {destination} ({pages} páginas; {links} enlaces y {tables} tablas conservados)")
        except (OSError, ValueError, RuntimeError) as exc:
            failed += 1
            print(f"ERROR {source}: {exc}", file=sys.stderr)
    print(f"Conversión: {len(sources) - failed}/{len(sources)} PDF procesados.")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
