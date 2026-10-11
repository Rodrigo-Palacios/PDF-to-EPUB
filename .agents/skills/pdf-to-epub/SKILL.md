---
name: pdf-to-epub
description: Convert study PDFs into EPUB ebooks without interactive prompts. Automatically process PDFs in the repository's input directory and save EPUBs under output, preserving subdirectories.
---

# PDF to EPUB

Convert one or more study PDFs into EPUB files without asking the user for confirmation or an output path.

## Default workflow

Run commands from the repository root. Install the dependency once, then run the converter:

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
mkdir -p input output
.venv/bin/python scripts/pdf_to_epub.py
```

Put PDFs in `input/`. The converter scans it recursively and writes each EPUB to `output/`, keeping any subdirectory structure. Both source PDFs and generated EPUBs remain in the repository and are visible to Git. It replaces an existing output with the same name. It does not modify or delete source PDFs.

To convert one file directly, without prompting:

```bash
.venv/bin/python scripts/pdf_to_epub.py --input path/to/book.pdf --output output/book.epub
```

Use `--input-dir` and `--output-dir` to change the batch locations. Use `--help` for all options. The EPUB includes the first PDF page as its cover, carries over PDF bookmarks into its linked table of contents, and preserves detectable links on PDF pages. Text is split by page. Image-only or password-protected PDFs need preprocessing before conversion.

## Results

Report the number of PDFs converted and the repository-relative paths of the EPUB files. If there are no PDFs in the input directory, report that and explain that files belong in `input/`; do not ask for confirmation or invent an output path.
