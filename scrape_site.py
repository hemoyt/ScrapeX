import argparse
import os
import time
import urllib.robotparser
from urllib.parse import urljoin, urlparse
from collections import deque
import requests
from bs4 import BeautifulSoup
from tqdm import tqdm
import reportlab.lib.pagesizes as pagesizes
from reportlab.pdfgen import canvas
from reportlab.lib.utils import simpleSplit
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer
from reportlab.lib.styles import getSampleStyleSheet
import validators  # Add this import for URL validation

def check_robots_txt(url):
    """Check if scraping is allowed by robots.txt"""
    rp = urllib.robotparser.RobotFileParser()
    parsed_url = urlparse(url)
    robots_url = f"{parsed_url.scheme}://{parsed_url.netloc}/robots.txt"
    rp.set_url(robots_url)
    try:
        rp.read()
        return rp.can_fetch('*', url)
    except:
        return True  # Allow if robots.txt is not accessible

def extract_text_and_metadata(url, session):
    """Extract visible text, titles, headings, links, alt text from a page"""
    try:
        response = session.get(url, timeout=10)
        response.raise_for_status()
        soup = BeautifulSoup(response.content, 'html.parser')

        # Remove scripts, styles, ads
        for script in soup(["script", "style"]):
            script.decompose()
        for tag in soup.find_all(['ins', 'iframe', 'form']):
            tag.decompose()  # Common ad and form elements

        # Extract title
        title = soup.title.string.strip() if soup.title else "No Title"

        # Extract headings
        headings = []
        for h in soup.find_all(['h1', 'h2', 'h3', 'h4', 'h5', 'h6']):
            headings.append(h.get_text().strip())

        # Extract visible text
        text = soup.get_text(separator='\n').strip()

        # Extract links
        links = []
        for a in soup.find_all('a', href=True):
            links.append({
                'text': a.get_text().strip(),
                'href': urljoin(url, a['href'])
            })

        # Extract alt text from images
        alts = [img['alt'] for img in soup.find_all('img', alt=True) if img['alt'].strip()]

        # Extract other metadata if present
        metadata = {}
        meta_tag = soup.find('meta', attrs={"name": "description"})
        if meta_tag:
            metadata['description'] = meta_tag.get('content', '')

        return {
            'title': title,
            'headings': headings,
            'text': text,
            'links': links,
            'alt_texts': alts,
            'metadata': metadata
        }
    except Exception as e:
        print(f"Error extracting from {url}: {e}")
        return None

def crawl_site(start_url, max_depth, max_pages, session):
    """Crawl the site with depth and page limits"""
    visited = set()
    queue = deque([(start_url, 0)])  # URL, depth
    scraped_pages = []

    pbar = tqdm(desc="Crawling pages", unit="page")

    while queue and len(scraped_pages) < max_pages:
        url, depth = queue.popleft()
        if url in visited or depth > max_depth:
            continue
        visited.add(url)

        data = extract_text_and_metadata(url, session)
        if data:
            scraped_pages.append(data)
            pbar.update(1)

            # Add internal links to queue
            if 'links' in data:
                for link in data['links']:
                    link_url = link['href']
                    parsed_link = urlparse(link_url)
                    parsed_start = urlparse(start_url)
                    if parsed_link.netloc == parsed_start.netloc:  # Internal link
                        if link_url not in visited:
                            queue.append((link_url, depth + 1))

        time.sleep(1)  # Rate limiting: 1 second per request

    pbar.close()
    return scraped_pages

def generate_txt(scraped_data, output_dir, site_name):
    """Generate plain TXT file"""
    txt_content = ""
    for page in scraped_data:
        txt_content += f"PAGE: {page['title']}\n\n"
        if page['headings']:
            txt_content += "HEADINGS:\n" + "\n".join(page['headings']) + "\n\n"
        if page['metadata']:
            for key, value in page['metadata'].items():
                txt_content += f"{key.upper()}: {value}\n"
        if page['alt_texts']:
            txt_content += "IMAGE ALTS:\n" + "\n".join(page['alt_texts']) + "\n\n"
        txt_content += "TEXT:\n" + page['text'] + "\n\n" + "="*50 + "\n\n"

    txt_file = os.path.join(output_dir, f"{site_name}.txt")
    with open(txt_file, 'w', encoding='utf-8') as f:
        f.write(txt_content)

def generate_pdf(scraped_data, output_dir, site_name):
    """Generate clean PDF"""
    pdf_file = os.path.join(output_dir, f"{site_name}.pdf")
    doc = SimpleDocTemplate(pdf_file, pagesize=pagesizes.letter)
    styles = getSampleStyleSheet()
    story = []

    for page in scraped_data:
        # Page title as heading
        story.append(Paragraph(page['title'], styles['Heading1']))
        story.append(Spacer(1, 12))

        # Headings
        if page['headings']:
            story.append(Paragraph("Headings:", styles['Heading2']))
            for h in page['headings']:
                story.append(Paragraph(h, styles['Heading3']))
                story.append(Spacer(1, 6))

        # Metadata
        if page['metadata']:
            story.append(Paragraph("Metadata:", styles['Normal']))
            for key, value in page['metadata'].items():
                story.append(Paragraph(f"{key.title()}: {value}", styles['Normal']))

        # Alt texts
        if page['alt_texts']:
            story.append(Paragraph("Image Alts:", styles['Heading4']))
            for alt in page['alt_texts']:
                story.append(Paragraph(alt, styles['Normal']))
                story.append(Spacer(1, 3))

        # Links
        if page['links']:
            story.append(Paragraph("Links:", styles['Heading4']))
            for link in page['links']:
                story.append(Paragraph(f"{link['text']} -> {link['href']}", styles['Normal']))
                story.append(Spacer(1, 3))

        # Body text
        story.append(Paragraph("Content:", styles['Heading2']))
        story.append(Spacer(1, 6))
        text_paras = page['text'].split('\n\n')
        for para in text_paras:
            if para.strip():
                story.append(Paragraph(para, styles['Normal']))
                story.append(Spacer(1, 6))

        # Page separator
        story.append(Spacer(1, 12))
        story.append(Paragraph("="*50, styles['Normal']))

    doc.build(story)

def validate_url(url):
    """Validate the URL format"""
    if not validators.url(url):
        print(f"❌ Invalid URL: {url}")
        return False
    return True

def main():
    parser = argparse.ArgumentParser(description="Scrape a website and save to PDF and TXT")
    parser.add_argument("url", nargs="?", default="https://www.edumize.com/en/home", help="Website URL to scrape (default: https://www.edumize.com/en/home)")
    parser.add_argument("--depth", type=int, default=2, help="Max crawl depth (default: 2)")
    parser.add_argument("--max_pages", type=int, default=100, help="Max number of pages to scrape (default: 100)")
    parser.add_argument("--output", default="output", help="Output directory and base name (default: output)")
    args = parser.parse_args()

    start_url = args.url
    if not start_url.startswith(('http://', 'https://')):
        start_url = 'https://' + start_url

    # Validate the URL
    if not validate_url(start_url):
        return

    # Check robots.txt
    if not check_robots_txt(start_url):
        print("Scraping not allowed by robots.txt")
        return

    session = requests.Session()
    session.headers.update({'User-Agent': 'WebScraper/1.0 (Educational Purpose)'})

    # Crawl the site
    try:
        scraped_data = crawl_site(start_url, args.depth, args.max_pages, session)
    except Exception as e:
        print(f"❌ Error during crawling: {e}")
        return

    # Create output directory
    os.makedirs(args.output, exist_ok=True)

    # Generate outputs
    try:
        generate_txt(scraped_data, args.output, args.output)
        generate_pdf(scraped_data, args.output, args.output)
        print("✅ Scraping and file generation completed successfully.")
    except Exception as e:
        print(f"❌ Error generating output files: {e}")

if __name__ == "__main__":
    main()
