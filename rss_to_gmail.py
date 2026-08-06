import feedparser
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from dotenv import load_dotenv
import os
import traceback
import time
import msvcrt
import re
import requests
from urllib.parse import urlparse


# -------------------------------
# Debug toggle
# -------------------------------
DEBUG = False

# -------------------------------
# URL normalization
# -------------------------------
def normalize_url(url):
    parsed = urlparse(url)
    host = parsed.netloc
    if host.startswith("www."):
        host = host[4:]
    if host == "ext.penny-arcade.com":
        host = "penny-arcade.com"
    return parsed._replace(netloc=host).geturl()

# -------------------------------
# Image extraction (feed-provided only, no page fetch)
# -------------------------------
def get_entry_image(entry):
    if hasattr(entry, "media_thumbnail") and entry.media_thumbnail:
        url = entry.media_thumbnail[0].get("url")
        if url:
            return url
    if hasattr(entry, "media_content") and entry.media_content:
        for m in entry.media_content:
            if m.get("url") and (m.get("medium") == "image" or m.get("type", "").startswith("image")):
                return m.get("url")
    if hasattr(entry, "enclosures") and entry.enclosures:
        for enc in entry.enclosures:
            if enc.get("type", "").startswith("image"):
                return enc.get("href") or enc.get("url")

    # Fallback: first <img> embedded in content or summary HTML
    # (covers webcomic-style feeds like Penny Arcade that don't use
    # media:thumbnail/media:content/enclosure at all)
    html_blobs = []
    if hasattr(entry, "content") and entry.content:
        html_blobs.extend(item.value for item in entry.content)
    if hasattr(entry, "summary"):
        html_blobs.append(entry.summary)
    for blob in html_blobs:
        match = re.search(r'<img[^>]+src=["\']([^"\']+)["\']', blob)
        if match:
            return match.group(1)

    return None

# -------------------------------
# og:image fallback (page fetch, only when feed gives us nothing)
# -------------------------------
def fetch_og_image(url, timeout=5):
    try:
        resp = requests.get(
            url,
            timeout=timeout,
            headers={"User-Agent": "Mozilla/5.0 (compatible; r2g-bot/1.0)"},
        )
        resp.raise_for_status()
        match = re.search(
            r'<meta[^>]+property=["\']og:image["\'][^>]+content=["\']([^"\']+)["\']',
            resp.text,
            re.IGNORECASE,
        )
        if not match:
            # some sites put content before property
            match = re.search(
                r'<meta[^>]+content=["\']([^"\']+)["\'][^>]+property=["\']og:image["\']',
                resp.text,
                re.IGNORECASE,
            )
        if match:
            return match.group(1)
    except Exception:
        pass
    return None    


# -------------------------------
# File that remembers sent items
# -------------------------------
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
SENT_ITEMS_FILE = os.path.join(BASE_DIR, "sent_items.txt")

if os.path.exists(SENT_ITEMS_FILE):
    with open(SENT_ITEMS_FILE, "r", encoding="utf-8") as f:
        sent_items = set(normalize_url(line.strip()) for line in f if line.strip())
else:
    sent_items = set()

# -------------------------------
# Load Gmail credentials safely
# -------------------------------
load_dotenv()
GMAIL_ADDRESS = os.getenv("GMAIL_ADDRESS")
GMAIL_APP_PASSWORD = os.getenv("GMAIL_APP_PASSWORD")

# -------------------------------
# RSS feeds
# -------------------------------
FEEDS_FILE = os.path.join(BASE_DIR, "feeds.txt")

RSS_FEEDS = []
current_category = "Uncategorized"
if os.path.exists(FEEDS_FILE):
    with open(FEEDS_FILE, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line.startswith("#"):
                current_category = line.lstrip("#").strip()
            elif line:
                url = line.split("|")[0].strip()
                RSS_FEEDS.append((url, current_category))
else:
    print("WARNING: feeds.txt not found. No feeds loaded.")

# -------------------------------
# Gmail SMTP settings
# -------------------------------
SMTP_SERVER = "smtp.gmail.com"
SMTP_PORT = 587

TITLE_STYLE = "font-size:1.2em;font-weight:bold;"
META_STYLE = "font-style:italic;color:#666;font-size:0.9em;"

# -------------------------------
# Send email
# -------------------------------
def send_email(subject, body_html):
    try:
        msg = MIMEMultipart()
        msg["From"] = GMAIL_ADDRESS
        msg["To"] = GMAIL_ADDRESS
        msg["Subject"] = subject
        msg.attach(MIMEText(body_html, "html"))
        with smtplib.SMTP(SMTP_SERVER, SMTP_PORT) as server:
            server.starttls()
            server.login(GMAIL_ADDRESS, GMAIL_APP_PASSWORD)
            server.send_message(msg)
        return True
    except Exception as e:
        print(f"  ERROR: Email send failed: {e}")
        return False

# -------------------------------
# Entry date sort key
# -------------------------------
def get_entry_date(entry):
    if hasattr(entry, "published_parsed") and entry.published_parsed:
        return entry.published_parsed
    if hasattr(entry, "updated_parsed") and entry.updated_parsed:
        return entry.updated_parsed
    return (0,) * 9

# -------------------------------
# Countdown exit
# -------------------------------
def countdown_exit(seconds=10):
    try:
        print(f"\nClosing in {seconds} seconds. Press any key to stay open.")
        for i in range(seconds, 0, -1):
            print(f"  {i}...", end="\r")
            for _ in range(10):
                if msvcrt.kbhit():
                    msvcrt.getch()
                    print("\nKeypress detected. Press Enter to exit.")
                    input()
                    return
                time.sleep(0.1)
        print("\nExiting.")
    except OSError:
        # No console attached (headless Task Scheduler run). Exit quietly.
        return
        
# -------------------------------
# Main
# -------------------------------
total_feeds = 0
total_new = 0
total_skipped = 0

for feed_url, category in RSS_FEEDS:
    total_feeds += 1
    try:
        feed = feedparser.parse(feed_url)
        feed_title = feed.feed.get("title", feed_url).strip()

        print(f"\nChecking feed: {feed_title}")

        if DEBUG:
            print(f"  [DEBUG] URL: {feed_url}")

        if not feed.entries:
            print("  No posts found.")
            continue

        sorted_entries = sorted(feed.entries, key=get_entry_date)

        feed_new = 0
        feed_skipped = 0

        for entry in sorted_entries:
            link = ""
            try:
                link = entry.get("link", "").strip()
                title = entry.get("title", "Untitled")

                if DEBUG:
                    print(f"  [DEBUG] RAW LINK: {repr(link)}")

                if not link:
                    continue

                normalized_link = normalize_url(link)

                if normalized_link in sent_items:
                    feed_skipped += 1
                    continue

                summary = entry.get("summary", "No summary available")
                content = ""
                if "content" in entry:
                    for item in entry.content:
                        content += item.value + "\n\n"
                content = content.strip()

                summary_clean = re.sub("<[^<]+?>", "", summary).strip()
                content_clean = re.sub("<[^<]+?>", "", content).strip()
                show_full_content = bool(content) and content_clean != summary_clean and len(content_clean) > len(summary_clean) + 20

                image_url = get_entry_image(entry)
                if not image_url:
                    image_url = fetch_og_image(link)

                category_tag = category.lower().replace(" ", "")
                subject_line = f"[RSS] {feed_title}: {title} #r2g #{category_tag}"
                published = entry.get("published", "No date available")

                html_parts = [f"<p style='{TITLE_STYLE}'><a href='{link}'>{title}</a></p>"]
                if image_url:
                    html_parts.append(f"<a href='{link}'><img src='{image_url}' style='max-width:400px;'></a>")
                html_parts.append(f"<p style='{META_STYLE}'>{feed_title} &middot; {category}</p>")
                html_parts.append(f"<p style='{META_STYLE}'>Originally posted on {published}</p>")
                html_parts.append(f"<h3>Summary:</h3>{summary}")
                if show_full_content:
                    html_parts.append(f"<h3>Full Content:</h3>{content}")
                html_parts.append(f"<p><a href='{link}'>{link}</a></p>")

                email_body = f"<html><body>{''.join(html_parts)}</body></html>"

                email_sent = send_email(subject=subject_line, body_html=email_body)

                if email_sent:
                    sent_items.add(normalized_link)
                    with open(SENT_ITEMS_FILE, "a", encoding="utf-8") as f:
                        f.write(normalized_link + "\n")

                    print(f"  Sent: {feed_title}: {title}")
                    feed_new += 1
                    total_new += 1
                else:
                    print(f"  SKIPPED (send failed, will retry next run): {feed_title}: {title}")

            except Exception as e:
                print(f"  ERROR: Failed on entry in {feed_title}: {e}")
                if DEBUG:
                    traceback.print_exc()
                with open(os.path.join(BASE_DIR, "rss_errors.log"), "a", encoding="utf-8") as f:
                    f.write(f"Error in entry {link} of feed {feed_url}:\n")
                    traceback.print_exc(file=f)
                    f.write("\n\n")

        total_skipped += feed_skipped

        if feed_new == 0 and feed_skipped == 0:
            print("  No posts found.")
        elif feed_new == 0:
            print(f"  {feed_skipped} already sent, nothing new.")
        else:
            print(f"  {feed_skipped} already sent, {feed_new} new.")

    except Exception:
        print(f"  ERROR: Could not load feed: {feed_url}")
        if DEBUG:
            traceback.print_exc()
        with open(os.path.join(BASE_DIR, "rss_errors.log"), "a", encoding="utf-8") as f:
            f.write(f"Error loading feed {feed_url}:\n")
            traceback.print_exc(file=f)
            f.write("\n\n")

print(f"\n--- Done. {total_feeds} feeds checked, {total_new} new emails sent, {total_skipped} already sent. ---")
countdown_exit(10)