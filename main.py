import os
import time
import json
import hashlib
import requests
import feedparser
from datetime import datetime, timezone
from dotenv import load_dotenv
from bs4 import BeautifulSoup

load_dotenv()

# =========================
# ENV / CONFIG
# =========================
BOT_TOKEN = os.getenv("JOBBOT_TOKEN", "").strip()
CHAT_ID = os.getenv("JOBBOT_CHAT_ID", "").strip()

CHECK_INTERVAL = int(os.getenv("CHECK_INTERVAL", "60"))
FRESH_CYBER_MIN = int(os.getenv("FRESH_CYBER_MIN", "90"))
FRESH_IT_MIN = int(os.getenv("FRESH_IT_MIN", "360"))

MAX_CYBER_ALERTS = int(os.getenv("MAX_CYBER_ALERTS", "5"))
MAX_IT_ALERTS = int(os.getenv("MAX_IT_ALERTS", "3"))

ENABLE_LINKEDIN = os.getenv("ENABLE_LINKEDIN", "1") == "1"
ENABLE_RSS = os.getenv("ENABLE_RSS", "1") == "1"

# heartbeat (minutes)
HEARTBEAT_MIN = int(os.getenv("HEARTBEAT_MIN", "720"))

# Dedupe state (persists while container lives)
STATE_PATH = os.getenv("STATE_PATH", "/tmp/uk_job_sniper_state.json")

UA = os.getenv(
    "UA",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36"
)

SESSION = requests.Session()
SESSION.headers.update({
    "User-Agent": UA,
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-GB,en;q=0.9",
    "Connection": "keep-alive",
})

# =========================
# SEARCH TERMS (elite list)
# =========================
CYBER_TERMS = [
    "SOC Analyst",
    "Security Operations Analyst",
    "Cyber Security Analyst",
    "Incident Response",
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

# =========================
# SIGNAL FILTERING
# =========================
CYBER_POS = [
    "soc", "security operations", "blue team", "incident", "threat", "siem",
    "sentinel", "splunk", "defender", "edr", "incident response", "devsecops",
    "security analyst", "cyber"
]
CYBER_NEG = [
    "intern", "unpaid", "volunteer", "teacher", "lecturer",
    "sales", "recruiter", "commission only"
]

IT_POS = [
    "2nd line", "second line", "service desk", "it support", "desktop",
    "m365", "intune", "azure", "entra", "network", "firewall", "sonicwall",
    "fortigate", "jamf"
]
IT_NEG = [
    "intern", "unpaid", "volunteer", "sales", "recruiter"
]

# =========================
# TIME (FIXED)
# =========================
def now_utc() -> datetime:
    return datetime.now(timezone.utc)

def ensure_aware_utc(dt: datetime) -> datetime:
    """
    LinkedIn (and some feeds) sometimes give a datetime with no tzinfo.
    This normalizes EVERYTHING to UTC-aware so subtraction never crashes.
    """
    if dt is None:
        return now_utc()
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)

def minutes_ago(dt: datetime) -> int:
    dt = ensure_aware_utc(dt)
    return int((now_utc() - dt).total_seconds() / 60)

# =========================
# STATE / DEDUPE
# =========================
def safe_hash(s: str) -> str:
    return hashlib.sha256(s.encode("utf-8")).hexdigest()[:18]

def load_state():
    try:
        with open(STATE_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {"seen": {}, "last_heartbeat": 0}

def save_state(state):
    try:
        with open(STATE_PATH, "w", encoding="utf-8") as f:
            json.dump(state, f)
    except Exception:
        pass

STATE = load_state()
SEEN = STATE.get("seen", {})  # key -> epoch
LAST_HEARTBEAT = STATE.get("last_heartbeat", 0)

def is_seen(key: str) -> bool:
    return key in SEEN

def mark_seen(key: str):
    SEEN[key] = int(time.time())

# =========================
# SCORING / TELEGRAM
# =========================
def score_text(text: str, pos_list, neg_list) -> int:
    t = (text or "").lower()
    score = 0
    for p in pos_list:
        if p in t:
            score += 2
    for n in neg_list:
        if n in t:
            score -= 4
    return score

def send_telegram(message: str) -> bool:
    if not BOT_TOKEN or not CHAT_ID:
        print("❌ Telegram not configured (missing JOBBOT_TOKEN / JOBBOT_CHAT_ID)")
        return False

    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    payload = {
        "chat_id": CHAT_ID,
        "text": message,
        "parse_mode": "HTML",
        "disable_web_page_preview": False
    }

    try:
        r = SESSION.post(url, data=payload, timeout=20)
        ok = r.status_code == 200
        if ok:
            print("✅ Telegram sent")
        else:
            print("❌ Telegram error:", r.status_code, r.text[:250])
        return ok
    except Exception as e:
        print("❌ Telegram exception:", e)
        return False

def maybe_heartbeat():
    global LAST_HEARTBEAT
    now_ts = int(time.time())
    if LAST_HEARTBEAT == 0 or (now_ts - LAST_HEARTBEAT) >= HEARTBEAT_MIN * 60:
        ok = send_telegram(
            "✅ <b>Job Sniper LIVE</b>\n"
            f"Interval: {CHECK_INTERVAL}s\n"
            f"Cyber fresh: {FRESH_CYBER_MIN}m | IT fresh: {FRESH_IT_MIN}m"
        )
        if ok:
            LAST_HEARTBEAT = now_ts

def format_msg(source: str, title: str, link: str, bucket: str, age_min: int, score: int):
    return (
        f"🚨 <b>{bucket} Alert</b>\n\n"
        f"<b>{title}</b>\n"
        f"🛰 Source: {source}\n"
        f"🕒 Age: {age_min} min\n"
        f"📊 Score: {score}\n\n"
        f"{link}"
    )

# =========================
# SOURCES
# =========================
def rss_feeds_for_term(term: str):
    q = term.replace(" ", "+")
    return [
        f"https://www.indeed.co.uk/rss?q={q}&l=United+Kingdom&sort=date",
        f"https://www.reed.co.uk/jobs/rss?keywords={q}&location=United+Kingdom",
        f"https://www.totaljobs.com/rss/jobs?q={q}&l=United+Kingdom",
    ]

def rss_global_cyber():
    return [
        "https://remoteok.com/remote-security-jobs.rss",
        "https://remoteok.com/remote-devops-jobs.rss",
        "https://weworkremotely.com/categories/remote-devops-sysadmin-jobs.rss",
        "https://weworkremotely.com/categories/remote-programming-jobs.rss",
    ]

def fetch_feed_entries(url: str):
    try:
        resp = SESSION.get(url, timeout=25)
        feed = feedparser.parse(resp.content)
        return feed.entries or []
    except Exception as e:
        print(f"Feed error: {url} -> {e}")
        return []

def parse_entry_time(entry) -> datetime:
    for k in ("published_parsed", "updated_parsed"):
        if hasattr(entry, k) and getattr(entry, k):
            try:
                t = getattr(entry, k)
                return ensure_aware_utc(datetime(*t[:6], tzinfo=timezone.utc))
            except Exception:
                pass
    # fallback (not fresh if you keep strict freshness)
    return now_utc()

# =========================
# LINKEDIN (guest endpoint)
# =========================
def linkedin_guest_search(term: str, location: str = "United Kingdom", remote_and_hybrid_only: bool = True, limit: int = 25):
    term_q = requests.utils.quote(term)
    loc_q = requests.utils.quote(location)

    workplace_filters = ""
    if remote_and_hybrid_only:
        workplace_filters = "&f_WT=2%2C3"  # remote + hybrid

    url = (
        "https://www.linkedin.com/jobs-guest/jobs/api/seeMoreJobPostings/search/"
        f"?keywords={term_q}&location={loc_q}{workplace_filters}&start=0"
    )

    try:
        resp = SESSION.get(url, timeout=25, headers={
            "User-Agent": UA,
            "Accept": "text/html,*/*",
            "Referer": "https://www.linkedin.com/jobs/",
        })
        if resp.status_code != 200:
            print(f"LinkedIn non-200: {resp.status_code}")
            return []

        soup = BeautifulSoup(resp.text, "html.parser")
        cards = soup.select("li")

        results = []
        for li in cards[:limit]:
            a = li.select_one("a.base-card__full-link")
            if not a:
                continue

            title = (a.get_text(strip=True) or "").strip()
            link = (a.get("href", "") or "").strip()
            if link.startswith("/"):
                link = "https://www.linkedin.com" + link

            meta = li.get_text(" ", strip=True)

            dt = now_utc()
            time_tag = li.select_one("time")
            if time_tag and time_tag.has_attr("datetime"):
                raw = (time_tag.get("datetime") or "").strip()
                try:
                    # LinkedIn can return:
                    # - 2026-02-12T10:00:00Z
                    # - 2026-02-12T10:00:00+00:00
                    # - sometimes without TZ → naive
                    dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
                except Exception:
                    dt = now_utc()

            dt = ensure_aware_utc(dt)

            results.append({
                "title": title,
                "link": link,
                "meta": meta,
                "dt": dt,
                "source": "LinkedIn",
            })

        return results

    except Exception as e:
        print(f"LinkedIn error: {e}")
        return []

# =========================
# CORE SCAN
# =========================
def scan_bucket(bucket_name: str, terms, fresh_min: int, pos_list, neg_list,
                max_alerts: int, include_rss: bool, include_linkedin: bool,
                include_global: bool = False):
    sent = 0

    for term in terms:
        if sent >= max_alerts:
            break

        # --- Global remote cyber (CYBER only)
        if include_global and bucket_name == "CYBER":
            for url in rss_global_cyber():
                if sent >= max_alerts:
                    break

                entries = fetch_feed_entries(url)
                for e in entries[:20]:
                    if sent >= max_alerts:
                        break

                    title = getattr(e, "title", "") or ""
                    link = getattr(e, "link", "") or ""
                    dt = ensure_aware_utc(parse_entry_time(e))
                    age = minutes_ago(dt)

                    score = score_text(f"{title} {getattr(e, 'summary', '')}", pos_list, neg_list)

                    if age > fresh_min:
                        continue
                    if score < 2:
                        continue

                    key = safe_hash(f"{url}|{link}|{title}")
                    if is_seen(key):
                        continue

                    msg = format_msg("Global Remote", title, link, bucket_name, age, score)
                    if send_telegram(msg):
                        mark_seen(key)
                        sent += 1

        # --- RSS UK feeds
        if include_rss:
            for url in rss_feeds_for_term(term):
                if sent >= max_alerts:
                    break

                entries = fetch_feed_entries(url)
                print(f"{bucket_name} term='{term}' feed={url} entries={len(entries)}")

                for e in entries[:15]:
                    if sent >= max_alerts:
                        break

                    title = getattr(e, "title", "") or ""
                    link = getattr(e, "link", "") or ""
                    dt = ensure_aware_utc(parse_entry_time(e))
                    age = minutes_ago(dt)

                    score = score_text(f"{title} {getattr(e, 'summary', '')}", pos_list, neg_list)

                    if age > fresh_min:
                        continue
                    if score < 2:
                        continue

                    key = safe_hash(f"{url}|{link}|{title}")
                    if is_seen(key):
                        continue

                    msg = format_msg("UK RSS", title, link, bucket_name, age, score)
                    if send_telegram(msg):
                        mark_seen(key)
                        sent += 1

        # --- LinkedIn UK Remote/Hybrid
        if include_linkedin and ENABLE_LINKEDIN:
            time.sleep(2)  # gentle

            results = linkedin_guest_search(term, location="United Kingdom", remote_and_hybrid_only=True, limit=25)
            print(f"{bucket_name} LinkedIn term='{term}' results={len(results)}")

            for r in results:
                if sent >= max_alerts:
                    break

                title = r["title"]
                link = r["link"]
                meta = r["meta"]
                dt = ensure_aware_utc(r["dt"])
                age = minutes_ago(dt)

                score = score_text(f"{title} {meta}", pos_list, neg_list)

                if age > fresh_min:
                    continue
                if score < 3:  # stricter for LinkedIn
                    continue

                key = safe_hash(f"linkedin|{link}|{title}")
                if is_seen(key):
                    continue

                msg = format_msg("LinkedIn UK (Remote/Hybrid)", title, link, bucket_name, age, score)
                if send_telegram(msg):
                    mark_seen(key)
                    sent += 1

    return sent

def main():
    print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] 🇬🇧 Job Sniper starting. Interval={CHECK_INTERVAL}s")
    maybe_heartbeat()

    while True:
        try:
            cycle_start = datetime.now().strftime("%H:%M:%S")
            print(f"\n=== Cycle {cycle_start} ===")

            cyber_sent = scan_bucket(
                bucket_name="CYBER",
                terms=CYBER_TERMS,
                fresh_min=FRESH_CYBER_MIN,
                pos_list=CYBER_POS,
                neg_list=CYBER_NEG,
                max_alerts=MAX_CYBER_ALERTS,
                include_rss=ENABLE_RSS,
                include_linkedin=True,
                include_global=True,
            )

            it_sent = scan_bucket(
                bucket_name="IT",
                terms=IT_TERMS,
                fresh_min=FRESH_IT_MIN,
                pos_list=IT_POS,
                neg_list=IT_NEG,
                max_alerts=MAX_IT_ALERTS,
                include_rss=ENABLE_RSS,
                include_linkedin=True,
                include_global=False,
            )

            print(f"Cycle complete. Sent: CYBER={cyber_sent}, IT={it_sent}")

            STATE["seen"] = SEEN
            STATE["last_heartbeat"] = LAST_HEARTBEAT
            save_state(STATE)

        except Exception as e:
            print("Fatal cycle error:", e)

        time.sleep(CHECK_INTERVAL)

if __name__ == "__main__":
    main()

