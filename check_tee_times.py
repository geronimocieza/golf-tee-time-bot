#!/usr/bin/env python3
"""
Golf Tee Time Bot
==================
Checks the public tee-time calendars of a list of golf clubs (mostly running
on the MiClub booking platform, which is very common across Sydney/NSW golf
clubs) and emails a daily digest of what's currently available.

How the MiClub calendar page works
-----------------------------------
Each club's "ViewPublicCalendar.msp" page shows a rolling ~5-6 day window
starting today. For each day it shows one or more fee categories (e.g.
"MON - FRI (Before 1:00pm)", "WEEKEND", "SUNDOWNER") and, for each category
on each day, either a price (meaning tee times are available to book in that
category) or the text "Not Available".

This script fetches that page as plain text and reconstructs the grid by
scanning the text in order: it finds the row of date headers, then reads
each category name followed by one value per date column. This is more
resilient to small styling/markup differences between clubs (they all run
the same MiClub template) than trying to match specific HTML tags/classes.

If a club's page doesn't match the expected pattern, the script reports it
as an error for that club rather than crashing the whole run, and includes
that in the digest so you know to check it manually / tell Claude so the
parsing can be adjusted.
"""

import os
import re
import sys
import smtplib
import traceback
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from datetime import datetime

import requests
import yaml
from bs4 import BeautifulSoup

CONFIG_PATH = os.path.join(os.path.dirname(__file__), "clubs_config.yaml")
REQUEST_TIMEOUT = 25
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
)

PRICE_RE = re.compile(r"^\$\d+(\.\d{2})?$")
NOT_AVAILABLE_RE = re.compile(r"^not available$", re.IGNORECASE)
# Category labels on MiClub pages are things like "MON - FRI (Before 1:00pm)"
# or "WEEKEND (1:00pm - 2:00pm)" or "SUNDOWNER (after 2:00pm)". They always
# contain at least one letter and are not prices / "Not Available".
DATE_TOKEN_RE = re.compile(
    r"^(today|tomorrow|"
    r"(mon|tue|tues|wed|thu|thur|fri|sat|sun)[a-z]*)\b", re.IGNORECASE
)


MONTHS = {
    m.lower(): i
    for i, m in enumerate(
        [
            "Jan", "Feb", "Mar", "Apr", "May", "Jun",
            "Jul", "Aug", "Sep", "Oct", "Nov", "Dec",
        ],
        start=1,
    )
}


def load_config():
    with open(CONFIG_PATH, "r") as f:
        return yaml.safe_load(f)


def is_weekend_date_label(date_label, today=None):
    """
    MiClub date labels look like "Today", "Thu 02 July", "Sat 04 July", or
    sometimes just "04 Jun" (no weekday name). Returns True if the date
    falls on a Saturday or Sunday, False otherwise, and None if it can't be
    determined (caller should decide how to treat that — we treat unknown
    as "include it" so the filter never silently hides something).
    """
    today = today or datetime.now()
    label = date_label.strip()

    if label.lower() == "today":
        return today.weekday() >= 5  # Mon=0 ... Sat=5, Sun=6
    if label.lower() == "tomorrow":
        return (today.weekday() + 1) % 7 >= 5

    # If the label already starts with a recognisable weekday abbreviation,
    # trust that directly — it's the most reliable signal.
    m = re.match(
        r"^(mon|tue|tues|wed|thu|thur|fri|sat|sun)[a-z]*\b", label, re.IGNORECASE
    )
    if m:
        abbr = m.group(1)[:3].lower()
        weekday_by_abbr = {
            "mon": 0, "tue": 1, "wed": 2, "thu": 3,
            "fri": 4, "sat": 5, "sun": 6,
        }
        return weekday_by_abbr.get(abbr) in (5, 6)

    # Otherwise try to parse a "DD Month" style date (e.g. "04 Jun") and
    # work out the weekday ourselves, rolling into next year if the parsed
    # date would otherwise be far in the past (handles December->January
    # rollover near the end of the calendar's visible window).
    m = re.search(r"(\d{1,2})\s+([A-Za-z]+)", label)
    if m:
        day = int(m.group(1))
        month_name = m.group(2)[:3].lower()
        month = MONTHS.get(month_name)
        if month:
            year = today.year
            try:
                candidate = datetime(year, month, day)
            except ValueError:
                return None
            if (today - candidate).days > 300:
                candidate = datetime(year + 1, month, day)
            return candidate.weekday() >= 5

    return None


def get_page_text_lines(url):
    """Fetch a page and return a flat list of its visible text tokens, in
    document order, with whitespace collapsed."""
    resp = requests.get(
        url, headers={"User-Agent": USER_AGENT}, timeout=REQUEST_TIMEOUT
    )
    resp.raise_for_status()
    soup = BeautifulSoup(resp.text, "html.parser")

    # Remove script/style content so it doesn't pollute the text stream
    for tag in soup(["script", "style", "noscript"]):
        tag.decompose()

    tokens = []
    for s in soup.stripped_strings:
        s = s.strip()
        if s:
            tokens.append(s)
    return tokens


def parse_miclub_calendar(tokens):
    """
    Reconstruct the (date, category, status) grid from the flat token list.

    Returns a list of dicts: {"date": ..., "category": ..., "status": ...}
    where status is either a price string like "$65.00" or "Not Available".

    Raises ValueError with a descriptive message if the expected pattern
    isn't found, so the caller can report a per-club error instead of
    silently returning nothing or crashing.
    """
    # 1. Find the date header row. It sits between a "Prev" token and a
    #    "Next" token (these are the calendar navigation arrows).
    try:
        prev_idx = next(i for i, t in enumerate(tokens) if t.strip().lower() == "prev")
    except StopIteration:
        raise ValueError("Could not find calendar 'Prev' navigation marker")

    try:
        next_idx = next(
            i for i in range(prev_idx + 1, len(tokens))
            if tokens[i].strip().lower() == "next"
        )
    except StopIteration:
        raise ValueError("Could not find calendar 'Next' navigation marker")

    date_tokens = tokens[prev_idx + 1 : next_idx]
    # Dates can be split across two tokens by BeautifulSoup, e.g.
    # ["Thu", "02 July"] or combined ["04 Jun"]. Group them: a token is a
    # new date if it looks like it starts a weekday/"Today", OR if it's a
    # standalone "DD Mon" pattern. To keep this simple and robust, merge
    # consecutive tokens until we hit something that looks like a full date
    # (contains a number) or "Today".
    dates = []
    buffer = ""
    for t in date_tokens:
        buffer = (buffer + " " + t).strip() if buffer else t
        if t.lower() == "today" or re.search(r"\d", t):
            dates.append(buffer)
            buffer = ""
    if buffer:
        dates.append(buffer)

    if not dates:
        raise ValueError("Found calendar navigation but no date headers between them")

    num_days = len(dates)

    # 2. After the "Next" marker, read category rows: each is one label
    #    token followed by num_days value tokens (price or "Not Available").
    results = []
    i = next_idx + 1
    n = len(tokens)
    while i < n:
        t = tokens[i]
        # Stop conditions: things that show up after the calendar grid ends
        if t.startswith("©") or t.lower() in ("confirm booking", "checkout", "login"):
            break
        if PRICE_RE.match(t) or NOT_AVAILABLE_RE.match(t):
            # Stray value with no preceding label we recognised — skip it
            i += 1
            continue

        # This token is treated as a category label. Collect it (it may be
        # split across a couple of tokens if it contains punctuation the
        # parser separated) then expect exactly num_days values after it.
        label = t
        j = i + 1
        # Absorb any following tokens that are neither prices nor
        # "Not Available" into the label (handles labels split oddly)
        while j < n and not (PRICE_RE.match(tokens[j]) or NOT_AVAILABLE_RE.match(tokens[j])):
            if tokens[j].startswith("©"):
                break
            label += " " + tokens[j]
            j += 1

        values = tokens[j : j + num_days]
        if len(values) < num_days or not all(
            PRICE_RE.match(v) or NOT_AVAILABLE_RE.match(v) for v in values
        ):
            # Doesn't fit the expected pattern — bail out of row parsing,
            # rather than emitting garbage. Whatever we found so far is
            # still returned.
            break

        for date_label, value in zip(dates, values):
            results.append({"date": date_label, "category": label, "status": value})

        i = j + num_days

    if not results:
        raise ValueError(
            "Found calendar structure but no category rows matched the expected pattern"
        )

    return results


def check_miclub_club(name, url, weekend_only=True):
    """Returns (available_list, error_or_None)."""
    try:
        tokens = get_page_text_lines(url)
        rows = parse_miclub_calendar(tokens)
    except requests.RequestException as e:
        return [], f"Could not reach the site ({e.__class__.__name__})"
    except ValueError as e:
        return [], str(e)
    except Exception:
        return [], f"Unexpected error: {traceback.format_exc(limit=1)}"

    available = [r for r in rows if PRICE_RE.match(r["status"])]

    if weekend_only:
        # Keep a row if we can positively confirm it's Sat/Sun, OR if we
        # couldn't determine the weekday at all (fail open, not silently
        # hidden — better to show you one extra row than to hide a real
        # Saturday tee time because of a date-parsing hiccup).
        filtered = []
        for r in available:
            is_weekend = is_weekend_date_label(r["date"])
            if is_weekend is True or is_weekend is None:
                filtered.append(r)
        available = filtered

    return available, None


def build_email_body(config, all_results):
    """all_results: list of (club_name, url, platform, available, error, note)"""
    lines_html = []
    lines_html.append(
        f"<h2>⛳ Weekend Tee Time Digest — {datetime.now().strftime('%A %d %B %Y')}</h2>"
    )
    lines_html.append(
        "<p style='color:#555;'>Showing Saturday/Sunday availability only.</p>"
    )

    any_available = any(r[3] for r in all_results if r[3])
    if not any_available:
        lines_html.append("<p>No open weekend tee times found at any of your tracked clubs right now.</p>")

    for club_name, url, platform, available, error, note in all_results:
        lines_html.append(f"<h3>{club_name}</h3>")
        if platform == "manual":
            lines_html.append(
                f"<p><em>{note or 'This club uses a different booking system.'}</em> "
                f'<a href="{url}">Check availability here</a></p>'
            )
            continue
        if error:
            lines_html.append(
                f'<p style="color:#b00;">⚠️ Could not check this club: {error}. '
                f'<a href="{url}">View the calendar directly</a></p>'
            )
            continue
        if not available:
            lines_html.append(f'<p>No availability found. <a href="{url}">View calendar</a></p>')
            continue

        lines_html.append("<ul>")
        for row in available:
            lines_html.append(f"<li><b>{row['date']}</b> — {row['category']} — {row['status']}</li>")
        lines_html.append("</ul>")
        lines_html.append(f'<p><a href="{url}">Book at {club_name}</a></p>')

    lines_html.append(
        "<hr><p style='color:#888;font-size:0.85em;'>"
        "Sent automatically by your Golf Tee Time Bot. Showing weekend "
        "(Sat/Sun) availability only. Edit clubs_config.yaml to change "
        "which clubs are tracked or to turn off the weekend filter.<br>"
        "Note: club calendars only show whether a time category is "
        "bookable, not how many player spots remain in each slot — click "
        "through to see group size before booking."
        "</p>"
    )
    return "\n".join(lines_html)


def write_status_page(all_results, output_path):
    """Writes a small standalone HTML status page (for GitHub Pages) so you
    can check current weekend availability from your phone any time,
    instead of waiting for the daily email."""
    html = [
        "<!DOCTYPE html><html><head><meta charset='utf-8'>",
        "<meta name='viewport' content='width=device-width, initial-scale=1'>",
        "<title>Golf Tee Time Status</title>",
        "<style>",
        "body{font-family:-apple-system,Segoe UI,Roboto,sans-serif;max-width:700px;",
        "margin:2rem auto;padding:0 1rem;color:#222;}",
        "h1{font-size:1.4rem;} h2{font-size:1.1rem;margin-top:2rem;border-bottom:1px solid #ddd;padding-bottom:.3rem;}",
        "li{margin:.3rem 0;} .err{color:#b00;} .muted{color:#888;font-size:.85rem;}",
        "a{color:#0a6;} .updated{color:#888;font-size:.85rem;margin-bottom:1.5rem;}",
        "</style></head><body>",
        "<h1>⛳ Golf Tee Time Status (weekends only)</h1>",
        f"<p class='updated'>Last checked: {datetime.now().strftime('%A %d %B %Y, %I:%M %p')}</p>",
    ]

    for club_name, url, platform, available, error, note in all_results:
        html.append(f"<h2>{club_name}</h2>")
        if platform == "manual":
            html.append(f"<p><em>{note or 'Different booking system.'}</em> <a href='{url}'>Check here</a></p>")
            continue
        if error:
            html.append(f"<p class='err'>⚠️ Could not check: {error}. <a href='{url}'>View calendar</a></p>")
            continue
        if not available:
            html.append(f"<p class='muted'>No weekend availability found. <a href='{url}'>View calendar</a></p>")
            continue
        html.append("<ul>")
        for row in available:
            html.append(f"<li><b>{row['date']}</b> — {row['category']} — {row['status']}</li>")
        html.append("</ul>")
        html.append(f"<p><a href='{url}'>Book at {club_name}</a></p>")

    html.append(
        "<hr><p class='muted'>Regenerated automatically once a day. "
        "Only shows whether a time category is bookable, not remaining "
        "player spots per slot.</p>"
    )
    html.append("</body></html>")

    os.makedirs(os.path.dirname(output_path), exist_ok=True)
    with open(output_path, "w") as f:
        f.write("\n".join(html))


def send_email(subject, html_body, to_addr, from_name):
    gmail_user = os.environ.get("GMAIL_USER")
    gmail_app_password = os.environ.get("GMAIL_APP_PASSWORD")

    if not gmail_user or not gmail_app_password:
        print(
            "ERROR: GMAIL_USER and/or GMAIL_APP_PASSWORD environment variables "
            "are not set. See README.md for setup instructions.",
            file=sys.stderr,
        )
        sys.exit(1)

    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = f"{from_name} <{gmail_user}>"
    msg["To"] = to_addr
    msg.attach(MIMEText(html_body, "html"))

    with smtplib.SMTP_SSL("smtp.gmail.com", 465) as server:
        server.login(gmail_user, gmail_app_password)
        server.sendmail(gmail_user, [to_addr], msg.as_string())


def main():
    config = load_config()
    to_addr = config["email"]["to"]
    from_name = config["email"].get("from_name", "Golf Tee Time Bot")
    weekend_only = config.get("filters", {}).get("weekend_only", True)

    if to_addr.startswith("YOUR_EMAIL"):
        print(
            "ERROR: Please edit clubs_config.yaml and set email.to to your "
            "real email address before running this.",
            file=sys.stderr,
        )
        sys.exit(1)

    all_results = []
    for club in config["clubs"]:
        name = club["name"]
        url = club["url"]
        platform = club.get("platform", "miclub")
        note = club.get("note")

        if platform == "manual":
            all_results.append((name, url, platform, [], None, note))
            print(f"[SKIP-MANUAL] {name}")
            continue

        print(f"[CHECKING] {name} ...")
        available, error = check_miclub_club(name, url, weekend_only=weekend_only)
        all_results.append((name, url, platform, available, error, note))
        if error:
            print(f"  -> error: {error}")
        else:
            print(f"  -> {len(available)} available weekend slot(s) found")

    body = build_email_body(config, all_results)
    total_available = sum(len(r[3]) for r in all_results if r[3])
    subject = f"⛳ Weekend Tee Time Digest — {total_available} slot(s) available"

    send_email(subject, body, to_addr, from_name)
    print("Email sent.")

    status_page_path = os.path.join(os.path.dirname(__file__), "docs", "index.html")
    write_status_page(all_results, status_page_path)
    print(f"Status page written to {status_page_path}")


if __name__ == "__main__":
    main()
