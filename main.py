import os
import time
import sqlite3
import hashlib
from datetime import datetime, timezone, timedelta
from email.utils import parsedate_to_datetime

import requests
import feedparser

# =======================
# ENV / CONFIG
# =======================
BOT_TOKEN = os.getenv("JOBBOT_TOKEN")
CHAT_ID = os.getenv("JOBBOT_CHAT_ID")
CHECK_INTERVAL = int(os.getenv("CHECK_INTERVAL", "60"))

# Freshness windows (minutes)
FRESH_CYBER_MIN = int(os.getenv("FRESH_CYBER_MIN", "90"))
FRESH_IT_MIN = int(os.getenv("FRESH_IT_MIN", "360"))

DB_PATH = os.getenv("SEEN_DB_PATH", "seen.db")

HEADERS = {"User-Agent": "Mozilla/5.0"}

# Two tracks
CYBER_TERMS = [
    "SOC Analyst",
    "Security Operations Analyst",
    "Security Analyst",
    "Blue Team",
    "Incident Response",
    "Threat Analyst",
    "Detection Engineer",
    "SIEM Analyst",
]
IT_TERMS = [
    "2nd Line Support",
    "Second Line Support",
    "Service Desk Engineer",
    "IT Support Engineer",
    "IT Engineer",
    "IT Analyst",
    "Desktop Support",
    "Support Engineer",
]

# Include/exclude (title-based)
CYBER_INCLUDE = [
    "soc", "security operations", "security analyst", "blue team",
    "incident response", "threat", "siem", "detection"
]
IT_INCLUDE = [
    "2nd line", "second line", "service desk", "it support",
    "desktop support", "support engineer", "it engineer", "it analyst"
]

EXCLUDE = [
    "sales", "marketing", "recruiter", "recruitment", "business development",
    "account manager", "commission", "door to door"
]

# Keep it UK-leaning. RSS feeds are UK, but we add a sanity check:
UK_HINTS = ["uk", "united kingdom", "england", "scotland", "wales", "london", "manchester", "birmingham", "bristol", "cardiff", "leeds", "glasgow", "edinburgh"]

# =======================
# FEEDS (UK)
# =======================
def feeds_for_term(term: str) -> list[str]:
    q = term.replace(" ", "+")
    # Note: boards can be flaky; we stick to the ones you’re already using successfully
    return [
        f"https://www.indeed.co.uk/rss?q={q}&l=United+Kingdom&sort=date",
        f"https://www.reed.co.uk/jobs/rss?keywords={q}&location=United+Kingdom",
        f"https://www.totaljobs.com/rss/jobs?q={q}&l=United+Kingdom",
    ]

# =======================
# DB (persistent dedupe)
# =======================
def db():
    conn = sqlite3.connect(DB_PATH)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS seen (
            id TEXT PRIMARY KEY,
            link TEXT NOT NULL,
            title TEXT NOT NULL,
            track TEXT NOT NULL,
            first_seen_utc TEXT NOT NULL
        )
    """)
    return conn

def seen_id(link: str) -> str:
    return hashlib.sha256(link.encode("utf-8")).hexdigest()

def already_seen(conn: sqlite3.Connection, link: str) -> bool:
    sid = seen_id(link)
    cur = conn.execute("SELECT 1 FROM seen WHERE id = ?", (sid,))
    return cur.fetchone() is not None

def mark_seen(conn: sqlite3.Connection, link: str, title: str, track: str):
    sid = seen_id(link)
    conn.execute(
        "INSERT OR IGNORE INTO seen (id, link, title, track, first_seen_utc) VALUES (?, ?, ?, ?, ?)",
        (sid, link, title, track, datetime.now(timezone.utc).isoformat()),
    )
    conn.commit()

def purge_old(conn: sqlite3.Connection, days: int = 14):
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    conn.execute("DELETE FROM seen WHERE first_seen_utc < ?", (cutoff.isoformat(),))
    conn.commit()

# =======================
# UTIL
# =======================
def now_utc():
    return datetime.now(timezone.utc)

def parse_published(entry) -> datetime | None:
    # RSS entries may have published / updated / etc.
    for key in ("published", "updated"):
        if hasattr(entry, key):
            try:
                dt = parsedate_to_datetime(getattr(entry, key))
                if dt.tzinfo is None:
                    dt = dt.replace(tzinfo=timezone.utc)
                return dt.astimezone(timezone.utc)
            except Exception:
                pass
    return None

def minutes_old(dt: datetime | None) -> int | None:
    if not dt:
        return None
    return int((now_utc() - dt).total_seconds() / 60)

def title_ok(title: str, include: list[str]) -> bool:
    t = title.lower()
    if any(x in t for x in EXCLUDE):
        return False
    return any(x in t for x in include)

def looks_uk(title: str) -> bool:
    t = title.lower()
    return any(h in t for h in UK_HINTS) or True  # feeds are UK-based; keep permissive to avoid false negatives

def score_title(track: str, title: str) -> int:
    t = title.lower()
    score = 0
    if track == "CYBER":
        if "soc" in t: score += 3
        if "security operations" in t: score += 3
        if "incident response" in t: score += 2
        if "siem" in t: score += 2
        if "detection" in t: score += 2
        if "senior" in t or "lead" in t: score -= 2
    else:
        if "2nd line" in t or "second line" in t: score += 3
        if "service desk" in t: score += 2
        if "it support" in t: score += 2
        if "support engineer" in t: score += 2
        if "senior" in t or "lead" in t: score -= 1
    return score

# =======================
# TELEGRAM
# =======================
def send_telegram(text: str):
    if not BOT_TOKEN or not CHAT_ID:
        raise RuntimeError("Missing JOBBOT_TOKEN / JOBBOT_CHAT_ID in environment variables")

    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    payload = {"chat_id": CHAT_ID, "text": text, "parse_mode": "HTML", "disable_web_page_preview": False}
    r = requests.post(url, data=payload, timeout=15)
    if r.status_code != 200:
        raise RuntimeError(f"Telegram send failed: {r.status_code} {r.text}")

# =======================
# SCAN
# =======================
def scan_track(conn: sqlite3.Connection, track: str, term: str, fresh_min: int, include_keywords: list[str]) -> int:
    sent = 0
    feed_urls = feeds_for_term(term)

    for feed_url in feed_urls:
        try:
            resp = requests.get(feed_url, headers=HEADERS, timeout=15)
            feed = feedparser.parse(resp.content)
            entries = getattr(feed, "entries", []) or []
            print(f"[{datetime.now()}] {track} term='{term}' feed={feed_url} entries={len(entries)}")

            for entry in entries[:20]:
                title = getattr(entry, "title", "").strip()
                link = getattr(entry, "link", "").strip()
                if not title or not link:
                    continue

                if not looks_uk(title):
                    continue

                if not title_ok(title, include_keywords):
                    continue

                pub = parse_published(entry)
                age = minutes_old(pub)

                # If feed provides timestamps, enforce freshness.
                # If not, allow but score lower by skipping if too many false positives happen later.
                if age is not None and age > fresh_min:
                    continue

                if already_seen(conn, link):
                    continue

                score = score_title(track, title)
                mark_seen(conn, link, title, track)

                age_txt = f"{age}m" if age is not None else "unknown age"
                msg = (
                    f"🎯 <b>{track} SNIPER</b>\n"
                    f"<b>{title}</b>\n"
                    f"Score: {score} | Age: {age_txt}\n\n"
                    f"{link}"
                )
                send_telegram(msg)
                sent += 1

        except Exception as e:
            print(f"[{datetime.now()}] Feed error: {feed_url} -> {e}")

    return sent

def main():
    print(f"[{datetime.now()}] ✅ Job Sniper starting. Interval={CHECK_INTERVAL}s")
    conn = db()
    purge_old(conn, days=14)

    # Startup heartbeat
    try:
        send_telegram(f"✅ Job Sniper LIVE\nInterval: {CHECK_INTERVAL}s\nCyber fresh: {FRESH_CYBER_MIN}m | IT fresh: {FRESH_IT_MIN}m")
    except Exception as e:
        print(f"[{datetime.now()}] Telegram startup failed: {e}")

    while True:
        cycle_sent = 0

        # CYBER
        for term in CYBER_TERMS:
            cycle_sent += scan_track(conn, "CYBER", term, FRESH_CYBER_MIN, CYBER_INCLUDE)

        # IT
        for term in IT_TERMS:
            cycle_sent += scan_track(conn, "IT", term, FRESH_IT_MIN, IT_INCLUDE)

        print(f"[{datetime.now()}] Cycle complete. Sent={cycle_sent}. Sleeping...\n")
        time.sleep(CHECK_INTERVAL)

if __name__ == "__main__":
    main()

