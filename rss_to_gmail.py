import msvcrt
import os
import re
import smtplib
import time
import traceback
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from urllib.parse import urlparse

import feedparser
import requests
from dotenv import load_dotenv

# -------------------------------
# Debug toggle
# -------------------------------
DEBUG = False


# -------------------------------
# URL normalization
# -------------------------------
def normalize_url(url):
    """Normalizes a URL for sent-items comparison: strips a leading
    'www.', and collapses Penny Arcade's ext.penny-arcade.com subdomain
    down to penny-arcade.com, since that feed alternates between the two
    for the same articles and would otherwise cause duplicate sends."""
    parsed = urlparse(url)
    host = parsed.netloc
    if host.startswith("www."):
        host = host[4:]
    if host == "ext.penny-arcade.com":
        host = "penny-arcade.com"
    return parsed._replace(netloc=host).geturl()

def clean_header_text(text):
    """Collapses embedded newlines/tabs/runs of whitespace down to a
    single space. Some feeds (Sarah's Scribbles among them) have a
    literal line break inside a <title> element; passed straight into
    an email Subject header, Python's email library rejects it outright
    as a header-injection guard, which silently fails every entry from
    that feed on every run."""
    return re.sub(r"\s+", " ", text).strip()


# -------------------------------
# Image extraction (feed-provided only, no page fetch)
# -------------------------------
def get_entry_image(entry):
    """Pulls a preview image straight from the feed entry, trying each
    RSS/Atom convention in order: media:thumbnail, then media:content,
    then a plain enclosure, then finally the first <img> found in the
    entry's own HTML content/summary (covers feeds like Penny Arcade
    that don't use any of the standard image fields). Returns None if
    none of those produce anything - fetch_og_image() is the fallback
    for that case."""
    if hasattr(entry, "media_thumbnail") and entry.media_thumbnail:
        url = entry.media_thumbnail[0].get("url")
        if url:
            return url
    if hasattr(entry, "media_content") and entry.media_content:
        for m in entry.media_content:
            if m.get("url") and (
                m.get("medium") == "image" or m.get("type", "").startswith("image")
            ):
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
    """Last-resort image fallback: fetches the article page itself and
    regexes out its og:image meta tag. Only called when get_entry_image()
    finds nothing in the feed data directly, since this costs a real page
    request per entry. Returns None on any failure (timeout, no match,
    non-200 response) rather than raising - a missing preview image
    should never stop the entry from being sent."""
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
load_dotenv(os.path.join(BASE_DIR, ".env"))
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

BODY_STYLE = "font-family:'Atkinson Hyperlegible',-apple-system,Segoe UI,Helvetica,Arial,sans-serif;background:#123524;color:#F0EBE0;margin:0;padding:24px 16px;"
CARD_STYLE = "border:1px solid #2F5B3E;border-radius:8px;padding:12px 16px;margin:22px 0;background:#1A4A30;max-width:680px;color:#F0EBE0;"
TITLE_STYLE = "font-family:'Bricolage Grotesque',-apple-system,Segoe UI,Helvetica,Arial,sans-serif;font-size:1.2em;font-weight:bold;color:#FF8C42;"
META_STYLE = "font-style:italic;color:#B8AFA0;font-size:0.9em;"
LINK_STYLE = "color:#FF8C42;text-decoration:none;"
HEADER_STYLE = "font-family:'Bricolage Grotesque',-apple-system,Segoe UI,Helvetica,Arial,sans-serif;color:#F0EBE0;"


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


def format_entry_date(entry):
    """Human-readable date only, no time (e.g. 'Aug 7, 2026') - the byline
    format CLAUDE.md's Category A spec calls for. Returns None if the feed
    gives us no parseable date, so the byline can fall back to feed name
    only instead of showing a wrong/placeholder date.
    Avoids strftime's %-d/%#d day-of-month flags entirely since those are
    platform-specific (Linux/Mac vs Windows) and would break on one or
    the other."""
    parsed = get_entry_date(entry)
    if parsed == (0,) * 9:
        return None
    try:
        return f"{time.strftime('%b', parsed)} {parsed.tm_mday}, {parsed.tm_year}"
    except (ValueError, OverflowError):
        return None


# -------------------------------
# Countdown exit
# -------------------------------
def countdown_exit(seconds=10):
    """Waits `seconds` before exiting, polling for a keypress every
    100ms so the console window can be kept open for review instead of
    auto-closing. The OSError catch handles headless Task Scheduler
    runs, where there's no console attached for msvcrt to poll at all -
    in that case just exit immediately rather than erroring."""
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
        feed_title = clean_header_text(feed.feed.get("title", feed_url))

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
                title = clean_header_text(entry.get("title", "Untitled"))

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
                show_full_content = (
                    bool(content)
                    and content_clean != summary_clean
                    and len(content_clean) > len(summary_clean) + 20
                )

                image_url = get_entry_image(entry)
                if not image_url:
                    image_url = fetch_og_image(link)

                subject_line = f"[RSS] {feed_title}: {title} #r2g"
                date_str = format_entry_date(entry)
                byline = f"{feed_title} &middot; {date_str}" if date_str else feed_title

                html_parts = [
                    f"<p style='{TITLE_STYLE}'><a href='{link}' style='{LINK_STYLE}'>{title}</a></p>"
                ]
                html_parts.append(f"<p style='{META_STYLE}'>{byline}</p>")
                if image_url:
                    html_parts.append(
                        f"<a href='{link}'><img src='{image_url}' style='max-width:400px;border-radius:6px;'></a>"
                    )
                html_parts.append(f"<h3 style='{HEADER_STYLE}'>Summary:</h3>{summary}")
                if show_full_content:
                    html_parts.append(
                        f"<h3 style='{HEADER_STYLE}'>Full Content:</h3>{content}"
                    )
                html_parts.append(
                    f"<p><a href='{link}' style='{LINK_STYLE}'>{link}</a></p>"
                )

                email_body = (
                    f"<html><head><meta charset='utf-8'>"
                    f"<meta name='color-scheme' content='dark'>"
                    f"<meta name='supported-color-schemes' content='dark'></head>"
                    f"<body style='{BODY_STYLE}'><div style='{CARD_STYLE}'>"
                    f"{''.join(html_parts)}</div></body></html>"
                )

                email_sent = send_email(subject=subject_line, body_html=email_body)

                if email_sent:
                    sent_items.add(normalized_link)
                    with open(SENT_ITEMS_FILE, "a", encoding="utf-8") as f:
                        f.write(normalized_link + "\n")

                    print(f"  Sent: {feed_title}: {title}")
                    feed_new += 1
                    total_new += 1
                else:
                    print(
                        f"  SKIPPED (send failed, will retry next run): {feed_title}: {title}"
                    )

            except Exception as e:
                print(f"  ERROR: Failed on entry in {feed_title}: {e}")
                if DEBUG:
                    traceback.print_exc()
                with open(
                    os.path.join(BASE_DIR, "rss_errors.log"), "a", encoding="utf-8"
                ) as f:
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

print(
    f"\n--- Done. {total_feeds} feeds checked, {total_new} new emails sent, {total_skipped} already sent. ---"
)
countdown_exit(10)
