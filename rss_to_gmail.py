import feedparser
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from dotenv import load_dotenv
import os
import traceback
import time
import msvcrt
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

                category_tag = category.lower().replace(" ", "")
                subject_line = f"[RSS] {feed_title}: {title} #r2g #{category_tag}"
                published = entry.get("published", "No date available")

                email_body = f"""
                <html>
                    <body>
                        <h2>{title}</h2>
                        <p><strong>Feed:</strong> {feed_title}</p>
                        <p><strong>Category:</strong> {category}</p>
                        <p><strong>Link:</strong> <a href="{link}">{link}</a></p>
                        <p><strong>Originally posted on:</strong> {published}</p>
                        <h3>Summary:</h3>
                        {summary}
                        <h3>Full Content:</h3>
                        {content}
                    </body>
                </html>
                """

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