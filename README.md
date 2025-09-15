# Website Scraper Tool

A Python tool to crawl websites, extract text and metadata, and save results in PDF and TXT formats.

## Requirements

- Python 3.6+
- Required packages:
  - requests
  - beautifulsoup4
  - tqdm
  - reportlab

## Setup Steps

1. Install Python 3.6 or higher from python.org

2. Install required packages using pip:
   ```bash
   pip install requests beautifulsoup4 tqdm reportlab
   ```

3. Download or copy the `scrape_site.py` script to your desired directory.

## Usage

Run the tool from the command line:

```bash
python scrape_site.py <URL> [--depth DEPTH] [--max_pages MAX_PAGES] [--output OUTPUT_DIR]
```

### Arguments

- `URL`: The website URL to scrape (required). Can include http:// or https:// or just the domain (will default to https://)
- `--depth DEPTH`: Maximum crawl depth (default: 2)
- `--max_pages MAX_PAGES`: Maximum number of pages to scrape (default: 100)
- `--output OUTPUT_DIR`: Output directory and base filename (default: "output"). Creates "OUTPUT_DIR/OUTPUT_DIR.txt" and "OUTPUT_DIR/OUTPUT_DIR.pdf"

### Examples

- Basic usage:
  ```bash
  python scrape_site.py https://example.com
  ```
  This will scrape example.com with default settings (depth 2, max 100 pages) and save to "output/output.txt" and "output/output.pdf"

- Custom depth and output:
  ```bash
  python scrape_site.py https://website.com --depth 3 --output mysite
  ```
  This crawls website.com up to depth 3 and saves to "mysite/mysite.txt" and "mysite/mysite.pdf"

## Features

- Respects robots.txt when possible
- Extracts visible text, titles, headings, links, alt texts, and metadata
- Handles internal links and crawled pages within the same domain
- Skips scripts, styles, ads, forms, iframes
- Rate limiting (1 second between requests) to be polite to servers
- Progress indicator showing crawling progress
- Error handling for failed requests
- Generates clean PDF with structured content
- Generates plain TXT file with raw text
- Handles pagination through internal links (if pages link to each other)
- Adapts to different HTML structures by focusing on text extraction

## Output Files

- **TXT File**: Contains all raw text organized by page, with section headers for different types of content
- **PDF File**: clean, readable document with page titles as headings, body text, and organized sections for headings, metadata, links, and alt texts

## Limitations

- Does not extract images, videos, or other non-text assets
- May not handle JavaScript-rendered content (requires additional libraries like Selenium for that)
- Crawls only internal links within the same domain
- Rate limited to 1 request per second
- Respects robots.txt but cannot guarantee compliance if site changes
- PDF generation may not handle complex layouts perfectly

## Ethics and Legality

- Ensure you have permission to scrape websites
- Respect robots.txt files
- Follow website's Terms of Service
- Be considerate with crawling frequency
- This tool is for educational/research purposes only
