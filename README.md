# Gallica Scraper

A Python pipeline for building a text corpus of 18th-century French scientific
publications from Gallica's API (https://gallica.bnf.fr), the digital library of the
Bibliothèque nationale de France.

## Context

This tool was developed for historical research on the reception of Benjamin
Franklin's electrical theory in 18th-century France. It
turns a catalogue export from Gallica into a folder of plain-text documents,
organized by year, ready for computational analysis.

## How it works

The pipeline has two stages:

1. **`fromexceltosql.py`** — reads a Gallica catalogue export (Excel) and loads
   it into a SQLite database, cleaning column names and empty cells.
2. **`fromsqltowebscraping.py`** — filters duplicate records, then retrieves the
   text of each document:
   - If Gallica's own OCR quality score (NQA) is above 70, it downloads the
     existing plain text (`.texteBrut`).
   - Otherwise, or if that text is empty or unavailable, it downloads the
     high-resolution page images and runs its own OCR with Tesseract (French).
   - For documents catalogued with a range of years, it reads the Roman-numeral
     imprint date (e.g. *M. DCC. LII*) from the text and files the document
     under the correct year.

Output: one `.txt` file per document, in folders named by year.

## Requirements

- Python 3.12+
- [Tesseract OCR](https://github.com/tesseract-ocr/tesseract) with the French
  language pack installed on your system
- Python packages:

```bash
  pip install pandas openpyxl requests beautifulsoup4 lxml playwright pytesseract pillow tenacity roman-numerals
  playwright install chromium
```

## Usage

```bash
# 1. Load the catalogue into SQLite
python fromexceltosql.py --filename my_export --sheetname Sheet1

# 2. Scrape and OCR the documents
python fromsqltowebscraping.py --databasename gallica_database
```

Set your own User-Agent in `fromsqltowebscraping.py` before running.

## Responsible use

The scraper is deliberately slow: it pauses between requests and uses
exponential backoff on errors, to avoid overloading Gallica's servers.

## Status

This is a work in progress! What's missing — e.g. Retronews-hosted
documents are currently skipped; the query is limited to 10 records for
testing. There is room for improvement.

## Author

Lucas Nardi