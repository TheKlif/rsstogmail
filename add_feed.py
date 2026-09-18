import os
import subprocess
import sys

import feedparser

FEEDS_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "feeds.txt")
RSS_SCRIPT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "rss_to_gmail.py")


def parse_feeds_file():
    if not os.path.exists(FEEDS_FILE):
        return []
    with open(FEEDS_FILE, "r", encoding="utf-8") as f:
        return f.readlines()


def fetch_title(url):
    try:
        feed = feedparser.parse(url)
        title = feed.feed.get("title", "").strip()
        return title if title else None
    except Exception:
        return None


def update_missing_titles():
    """Runs on startup: scans feeds.txt for any URL-only line (no
    '| Title' yet), fetches that feed's title, and rewrites the line
    with it filled in. Lets you paste a bare URL into feeds.txt by hand
    and have the title show up in list_feeds() next time this runs,
    without needing to go through add_feed()."""
    lines = parse_feeds_file()
    if not lines:
        return
    changed = False
    new_lines = []
    for line in lines:
        stripped = line.strip()
        if stripped and not stripped.startswith("#"):
            if "|" not in stripped:
                url = stripped
                print(f"  Fetching missing title for: {url}")
                title = fetch_title(url)
                if title:
                    new_lines.append(f"{url} | {title}\n")
                    changed = True
                else:
                    new_lines.append(line)
            else:
                new_lines.append(line)
        else:
            new_lines.append(line)
    if changed:
        with open(FEEDS_FILE, "w", encoding="utf-8") as f:
            f.writelines(new_lines)


def list_categories():
    categories = []
    for line in parse_feeds_file():
        line = line.strip()
        if line.startswith("#"):
            cat = line.lstrip("#").strip()
            if cat not in categories:
                categories.append(cat)
    return categories


def list_feeds():
    print("\n=== All Feeds ===\n")
    lines = parse_feeds_file()
    if not lines:
        print("  No feeds found.")
        return

    current_category = None
    count = 0
    for line in lines:
        stripped = line.strip()
        if stripped.startswith("#"):
            current_category = stripped.lstrip("#").strip()
            print(f"\n  [{current_category}]")
        elif stripped:
            parts = stripped.split("|", 1)
            title = parts[1].strip() if len(parts) > 1 else parts[0].strip()
            count += 1
            print(f"    {title}")

    print(f"\n  {count} feeds total.")


def run_r2g():
    print("\n=== Running r2g ===\n")
    subprocess.run([sys.executable, RSS_SCRIPT])


def add_feed():
    """Interactive add flow: prompts for a URL, checks it's not already
    in feeds.txt, auto-fetches the feed's title (or asks for one
    manually if that fails), then walks the file line-by-line to insert
    the new entry at the end of the chosen category's block - or creates
    that category's header at the end of the file if it doesn't exist
    yet."""
    print("\n=== Add Feed ===\n")
    url = input("Feed URL: ").strip()
    if not url:
        print("No URL entered.")
        return

    lines = parse_feeds_file()
    for line in lines:
        existing_url = line.strip().split("|")[0].strip()
        if existing_url == url:
            print("That URL is already in feeds.txt.")
            return

    print("  Fetching feed title...")
    title = fetch_title(url)
    if title:
        print(f"  Found: {title}")
    else:
        title = input(
            "  Could not fetch title. Enter one manually (or leave blank): "
        ).strip()

    entry = f"{url} | {title}" if title else url

    categories = list_categories()
    print("\nExisting categories:")
    for i, cat in enumerate(categories, 1):
        print(f"  {i}. {cat}")
    print("  N. New category")

    choice = input("\nChoose a number or N: ").strip()

    if choice.lower() == "n":
        category = input("New category name: ").strip()
    else:
        try:
            category = categories[int(choice) - 1]
        except (ValueError, IndexError):
            print("Invalid choice.")
            return

    if not category:
        print("No category entered.")
        return

    header = f"# {category}"
    inserted = False
    new_lines = []
    i = 0
    while i < len(lines):
        new_lines.append(lines[i])
        if not inserted and lines[i].strip() == header:
            i += 1
            while (
                i < len(lines)
                and not lines[i].strip().startswith("#")
                and lines[i].strip() != ""
            ):
                new_lines.append(lines[i])
                i += 1
            new_lines.append(entry + "\n")
            inserted = True
            continue
        i += 1

    if not inserted:
        if new_lines and new_lines[-1].strip() != "":
            new_lines.append("\n")
        new_lines.append(f"# {category}\n")
        new_lines.append(entry + "\n")

    with open(FEEDS_FILE, "w", encoding="utf-8") as f:
        f.writelines(new_lines)

    print(f"\n  Added to [{category}]: {title or url}")

    run_now = (
        input("\nRun r2g now to send backfill for this feed? (Y/N): ").strip().lower()
    )
    if run_now == "y":
        run_r2g()


def remove_feed():
    """Interactive remove flow: numbers every feed across all
    categories for selection, confirms before deleting, then rewrites
    feeds.txt with that entry's line dropped. A category header is
    dropped too if removing its last entry empties it out."""
    print("\n=== Remove Feed ===\n")
    lines = parse_feeds_file()
    if not lines:
        print("  feeds.txt not found or empty.")
        return

    current_category = None
    numbered_entries = []
    display_lines = []

    for line in lines:
        stripped = line.strip()
        if stripped.startswith("#"):
            current_category = stripped.lstrip("#").strip()
            display_lines.append(("header", current_category))
        elif stripped:
            parts = stripped.split("|", 1)
            url = parts[0].strip()
            title = parts[1].strip() if len(parts) > 1 else url
            numbered_entries.append((len(numbered_entries) + 1, url, title))
            display_lines.append(("entry", len(numbered_entries), title, url))

    for item in display_lines:
        if item[0] == "header":
            print(f"\n  [{item[1]}]")
        else:
            _, n, title, url = item
            print(f"    {n}. {title}")

    choice = input("\nEnter number to remove (or Q to quit): ").strip()
    if choice.lower() == "q":
        return

    try:
        n = int(choice)
        _, target_url, target_title = numbered_entries[n - 1]
    except (ValueError, IndexError):
        print("Invalid choice.")
        return

    # confirm
    confirm = input(f"\n  Remove '{target_title}'? (Y/N): ").strip().lower()
    if confirm != "y":
        print("  Cancelled.")
        return

    new_lines = []
    i = 0
    while i < len(lines):
        stripped = lines[i].strip()
        if stripped.startswith("#"):
            header_line = lines[i]
            i += 1
            section_entries = []
            while (
                i < len(lines)
                and not lines[i].strip().startswith("#")
                and lines[i].strip() != ""
            ):
                if lines[i].strip().split("|")[0].strip() != target_url:
                    section_entries.append(lines[i])
                i += 1
            if section_entries:
                new_lines.append(header_line)
                new_lines.extend(section_entries)
            continue
        new_lines.append(lines[i])
        i += 1

    with open(FEEDS_FILE, "w", encoding="utf-8") as f:
        f.writelines(new_lines)

    print(f"\n  Removed: {target_title}")


def main():
    print("=== RSS Feed Manager ===")
    print("Checking for missing feed titles...")
    update_missing_titles()

    while True:
        print("\n  1. Add feed")
        print("  2. Remove feed")
        print("  3. List all feeds")
        print("  4. Recategorize feed")
        print("  5. Run r2g now")
        print("  Q. Quit")

        choice = input("\nChoose: ").strip().lower()

        if choice == "1":
            add_feed()
        elif choice == "2":
            remove_feed()
        elif choice == "3":
            list_feeds()
        elif choice == "4":
            recategorize_feed()
        elif choice == "5":
            run_r2g()
        elif choice == "q":
            print("\nGoodbye.")
            break
        else:
            print("Invalid choice.")


def recategorize_feed():
    """Interactive move flow: numbers every feed for selection, shows
    the destination category list with the current one marked, then
    removes the entry from its source category (dropping that header
    too if it's now empty) and inserts it into the destination category
    the same way add_feed() does."""
    print("\n=== Recategorize Feed ===\n")
    lines = parse_feeds_file()
    if not lines:
        print("  feeds.txt not found or empty.")
        return

    current_category = None
    numbered_entries = []
    display_lines = []

    for line in lines:
        stripped = line.strip()
        if stripped.startswith("#"):
            current_category = stripped.lstrip("#").strip()
            display_lines.append(("header", current_category))
        elif stripped:
            parts = stripped.split("|", 1)
            url = parts[0].strip()
            title = parts[1].strip() if len(parts) > 1 else url
            numbered_entries.append(
                (len(numbered_entries) + 1, url, title, current_category)
            )
            display_lines.append(("entry", len(numbered_entries), title, url))

    for item in display_lines:
        if item[0] == "header":
            print(f"\n  [{item[1]}]")
        else:
            _, n, title, url = item
            print(f"    {n}. {title}")

    choice = input("\nEnter number to move (or Q to quit): ").strip()
    if choice.lower() == "q":
        return

    try:
        n = int(choice)
        _, target_url, target_title, source_category = numbered_entries[n - 1]
    except (ValueError, IndexError):
        print("Invalid choice.")
        return

    categories = list_categories()
    print(f"\n  Moving: {target_title}")
    print(f"  Currently in: [{source_category}]\n")
    print("  Destination category:")
    for i, cat in enumerate(categories, 1):
        marker = " (current)" if cat == source_category else ""
        print(f"    {i}. {cat}{marker}")
    print("    N. New category")

    dest_choice = input("\nChoose a number or N: ").strip()

    if dest_choice.lower() == "n":
        new_category = input("New category name: ").strip()
    else:
        try:
            new_category = categories[int(dest_choice) - 1]
        except (ValueError, IndexError):
            print("Invalid choice.")
            return

    if not new_category:
        print("No category entered.")
        return

    if new_category == source_category:
        print("  Already in that category.")
        return

    # Build the entry string
    entry_line = f"{target_url} | {target_title}\n"

    # Remove from source, track if source category becomes empty
    new_lines = []
    i = 0
    while i < len(lines):
        stripped = lines[i].strip()
        if stripped == f"# {source_category}":
            # Collect all entries under this header
            header_line = lines[i]
            i += 1
            section_entries = []
            while (
                i < len(lines)
                and not lines[i].strip().startswith("#")
                and lines[i].strip() != ""
            ):
                if lines[i].strip().split("|")[0].strip() != target_url:
                    section_entries.append(lines[i])
                i += 1
            # Only keep the header if entries remain
            if section_entries:
                new_lines.append(header_line)
                new_lines.extend(section_entries)
            continue
        new_lines.append(lines[i])
        i += 1

    # Insert into destination
    dest_header = f"# {new_category}"
    inserted = False
    final_lines = []
    i = 0
    while i < len(new_lines):
        final_lines.append(new_lines[i])
        if not inserted and new_lines[i].strip() == dest_header:
            i += 1
            while (
                i < len(new_lines)
                and not new_lines[i].strip().startswith("#")
                and new_lines[i].strip() != ""
            ):
                final_lines.append(new_lines[i])
                i += 1
            final_lines.append(entry_line)
            inserted = True
            continue
        i += 1

    if not inserted:
        if final_lines and final_lines[-1].strip() != "":
            final_lines.append("\n")
        final_lines.append(f"# {new_category}\n")
        final_lines.append(entry_line)

    with open(FEEDS_FILE, "w", encoding="utf-8") as f:
        f.writelines(final_lines)

    print(f"\n  Moved '{target_title}' from [{source_category}] to [{new_category}].")


if __name__ == "__main__":
    main()
    input("\nPress Enter to exit...")
