#!/usr/bin/env python3
"""Turn text-based PDFs into EPUB 3 files without interactive prompts."""

from __future__ import annotations

import argparse
import html
import re
import sys
import uuid
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from xml.etree import ElementTree as ET

import fitz


CONTAINER_XML = """<?xml version="1.0" encoding="UTF-8"?>
<container version="1.0" xmlns="urn:oasis:names:tc:opendocument:xmlns:container">
  <rootfiles><rootfile full-path="EPUB/package.opf" media-type="application/oebps-package+xml"/></rootfiles>
</container>
"""

CSS = """body { font-family: serif; line-height: 1.55; margin: 5%; }
h1 { page-break-before: always; font-size: 1.25em; }
p { margin: 0 0 0.8em; text-indent: 1em; }
"""


def paragraphs_from_page(text: str) -> list[str]:
    """Split extracted page text into readable paragraphs."""
    result: list[str] = []
    for block in re.split(r"\n\s*\n", text.strip()):
        lines = [re.sub(r"\s+", " ", line).strip() for line in block.splitlines()]
        paragraph = " ".join(line for line in lines if line)
        if paragraph:
            result.append(paragraph)
    return result


def xhtml_page(title: str, paragraphs: list[str]) -> bytes:
    body = "\n".join(f"<p>{html.escape(p)}</p>" for p in paragraphs)
    document = (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<!DOCTYPE html>\n<html xmlns="http://www.w3.org/1999/xhtml" '
        'xmlns:epub="http://www.idpf.org/2007/ops" lang="es">\n'
        f"<head><title>{html.escape(title)}</title><link rel=\"stylesheet\" "
        'type="text/css" href="styles.css"/></head>\n'
        f"<body><h1>{html.escape(title)}</h1>{body}</body>\n</html>"
    )
    return document.encode("utf-8")


def convert_pdf(source: Path, destination: Path) -> int:
    """Convert a text-based PDF and return its number of pages."""
    with fitz.open(source) as pdf:
        if pdf.needs_pass:
            raise ValueError("el PDF está protegido con contraseña")
        if pdf.page_count == 0:
            raise ValueError("el PDF no contiene páginas")

        title = (pdf.metadata or {}).get("title", "").strip() or source.stem
        pages: list[tuple[str, bytes]] = []
        for index, page in enumerate(pdf, start=1):
            paragraphs = paragraphs_from_page(page.get_text("text", sort=True))
            if paragraphs:
                page_title = f"{title} — página {index}"
                pages.append((f"page-{index:04}.xhtml", xhtml_page(page_title, paragraphs)))
        if not pages:
            raise ValueError("no se encontró texto; puede ser un PDF escaneado que necesite OCR")

        book_id = f"urn:uuid:{uuid.uuid4()}"
        modified = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        manifest_items = [
            '<item id="nav" href="nav.xhtml" media-type="application/xhtml+xml" properties="nav"/>',
            '<item id="css" href="styles.css" media-type="text/css"/>',
        ]
        spine_items = []
        nav_items = []
        for index, (filename, _) in enumerate(pages, start=1):
            page_id = f"page{index}"
            manifest_items.append(
                f'<item id="{page_id}" href="{filename}" media-type="application/xhtml+xml"/>'
            )
            spine_items.append(f'<itemref idref="{page_id}"/>')
            nav_items.append(
                f'<li><a href="{filename}">Página {index}</a></li>'
            )

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
        nav = (
            '<?xml version="1.0" encoding="UTF-8"?>\n'
            '<!DOCTYPE html>\n<html xmlns="http://www.w3.org/1999/xhtml" '
            'xmlns:epub="http://www.idpf.org/2007/ops" lang="es">'
            f"<head><title>{html.escape(title)}</title></head>"
            f'<body><nav epub:type="toc"><h1>Contenido</h1><ol>{"".join(nav_items)}</ol>'
            "</nav></body></html>"
        ).encode("utf-8")

    destination.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(destination, "w") as epub:
        epub.writestr("mimetype", "application/epub+zip", compress_type=zipfile.ZIP_STORED)
        epub.writestr("META-INF/container.xml", CONTAINER_XML)
        epub.writestr("EPUB/package.opf", package)
        epub.writestr("EPUB/nav.xhtml", nav)
        epub.writestr("EPUB/styles.css", CSS)
        for filename, content in pages:
            epub.writestr(f"EPUB/{filename}", content)
    return len(pages)


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
            pages = convert_pdf(source, destination)
            print(f"OK {source} -> {destination} ({pages} páginas con texto)")
        except (OSError, ValueError, RuntimeError, ET.ParseError) as exc:
            failed += 1
            print(f"ERROR {source}: {exc}", file=sys.stderr)
    print(f"Conversión: {len(sources) - failed}/{len(sources)} PDF procesados.")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
