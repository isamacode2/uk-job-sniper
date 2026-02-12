import os
import time
import json
import hashlib
from datetime import datetime, timezone

import requests
import feedparser
from bs4 import BeautifulSoup

# =========================
# ENV / CONFIG
# =========================
BOT_TOKEN = os.getenv("JOBBOT_TOKEN", "").strip()
CHAT_ID = os.getenv("JOBBOT_CHAT_ID", "").strip()

CHECK_INTERVAL = int(os.getenv("CHECK_INTERVAL", "60"))
FRESH_CYBER_MIN = int(os.getenv("FRESH_CYBER_MIN", "90"))
FRESH_IT_MIN = int(os.getenv("FRESH_IT_MIN", "360"))

MAX_CYBER_ALERTS = 5
MAX_IT_ALERTS = 4

STATE_PATH = "/tmp/uk_job_sniper_state.json"

UA = "Mozilla/5.0"
SESSION = requests.Session()
SESSION.headers.update({"User-Agent": UA})

# =========================
# SEARCH TERMS
# =========================
CYBER_TERMS = [
    "SOC Analyst",
    "Security Operations Analyst",
    "Cyber Security Analyst",
    "Threat Analyst",
    "DevSecOps",
]

IT_TERMS = [
    "2nd Line Support",
    "IT Support Engineer",
    "Service Desk Engineer",
    "IT Engineer",
    "IT Analyst",
]

CYBER_POS = ["soc", "security", "threat", "incident", "blue team", "siem", "cyber"]
CYBER_NEG = ["intern", "sales", "recruiter", "teacher"]

IT_POS = ["2nd line", "service desk", "it support", "azure", "intune", "network"]
IT_NEG = ["intern", "sales", "recruiter"]

# =========================
# TIME HELPERS (FIXED)
# =========================
def now_utc():
    return datetime.now(timezone.utc)

def to_utc(dt):
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)

def minutes_ago(dt):
    dt = to_utc(dt)
    return int((now_utc() - dt).total_seconds() / 60)

# =========================
# STATE
# =========================
def load_state():
    try:
        with open(STATE_PATH, "r") as f:
            return json.load(f)
    except:
        return {"seen": {}}

def save_state(state):
    with open(STATE_PATH, "w") as f:
        json.dump(state, f)

STATE = load_state()
SEEN = STATE.get("seen", {})

def is_seen(key):
    return key in SEEN

def mark_seen(key):
    SEEN[key] = int(time.time())

# =========================
# UTILS
# =========================
def score_text(text, pos, neg):
    t = text.lower()
    score = 0
    for p in pos:
        if p in t:
            score += 2
    for n in neg:
        if n in t:
            score -= 4
    return score

def send_telegram(message):
    if not BOT_TOKEN or not CHAT_ID:
        return False
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    data = {"chat_id": CHAT_ID, "text": message, "parse_mode": "HTML"}
    try:
        r = SESSION.post(url, data=data, timeout=20)
        return r.status_code == 200
    except:
        return False

def format_msg(source, title, link, bucket, age, score):
    return (
        f"🚨 <b>{bucket}</b>\n\n"
        f"<b>{title}</b>\n"
        f"🛰 {source}\n"
        f"🕒 {age} min old\n"
        f"📊 Score {score}\n\n"
        f"{link}"
    )

# =========================
# RSS
# =========================
def rss_feeds(term):
    q = term.replace(" ", "+")
    return [
        f"https://www.indeed.co.uk/rss?q={q}&l=United+Kingdom&sort=date",
        f"https://www.reed.co.uk/jobs/rss?keywords={q}&location=United+Kingdom",
    ]

def fetch_feed(url):
    try:
        resp = SESSION.get(url, timeout=20)
        feed = feedparser.parse(resp.content)
        return feed.entries or []
    except:
        return []

def parse_time(entry):
    if hasattr(entry, "published_parsed") and entry.published_parsed:
        t = entry.published_parsed
        return datetime(*t[:6], tzinfo=timezone.utc)
    return now_utc()

# =========================
# LINKEDIN
# =========================
def linkedin_search(term):
    url = (
        "https://www.linkedin.com/jobs-guest/jobs/api/seeMoreJobPostings/search/"
        f"?keywords={requests.utils.quote(term)}&location=United%20Kingdom&f_WT=2%2C3&start=0"
    )
    try:
        resp = SESSION.get(url, timeout=20)
        soup = BeautifulSoup(resp.text, "html.parser")
        results = []
        for li in soup.select("li")[:20]:
            a = li.select_one("a.base-card__full-link")
            if not a:
                continue
            title = a.get_text(strip=True)
            link = a.get("href")
            dt = now_utc()
            results.append({"title": title, "link": link, "dt": dt, "meta": li.get_text()})
        return results
    except:
        return []

# =========================
# CORE
# =========================
def scan(bucket, terms, fresh_min, pos, neg, max_alerts):
    sent = 0
    for term in terms:
        # RSS (strict)
        for url in rss_feeds(term):
            for e in fetch_feed(url)[:15]:
                title = getattr(e, "title", "")
                link = getattr(e, "link", "")
                dt = parse_time(e)
                age = minutes_ago(dt)
                score = score_text(title, pos, neg)

                if age > fresh_min:
                    continue
                if score < 3:
                    continue

                key = hashlib.sha256(link.encode()).hexdigest()
                if is_seen(key):
                    continue

                msg = format_msg("RSS", title, link, bucket, age, score)
                if send_telegram(msg):
                    mark_seen(key)
                    sent += 1

        # LinkedIn (balanced logic)
        results = linkedin_search(term)
        for r in results:
            title = r["title"]
            link = r["link"]
            dt = r["dt"]
            age = minutes_ago(dt)
            score = score_text(title + r["meta"], pos, neg)

            if age > fresh_min:
                continue

            if bucket == "CYBER":
                if score < 3:
                    continue
            else:
                if score < 2:
                    continue

            key = hashlib.sha256(link.encode()).hexdigest()
            if is_seen(key):
                continue

            msg = format_msg("LinkedIn", title, link, bucket, age, score)
            if send_telegram(msg):
                mark_seen(key)
                sent += 1

    return sent

def main():
    print("🚀 Job Sniper LIVE")
    while True:
        cyber = scan("CYBER", CYBER_TERMS, FRESH_CYBER_MIN, CYBER_POS, CYBER_NEG, MAX_CYBER_ALERTS)
        it = scan("IT", IT_TERMS, FRESH_IT_MIN, IT_POS, IT_NEG, MAX_IT_ALERTS)

        STATE["seen"] = SEEN
        save_state(STATE)

        print(f"Cycle done. CYBER={cyber} IT={it}")
        time.sleep(CHECK_INTERVAL)

if __name__ == "__main__":
    main()

