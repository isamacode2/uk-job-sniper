import requests
import feedparser
import time
import os
from datetime import datetime
from dotenv import load_dotenv

load_dotenv()

BOT_TOKEN = os.getenv("JOBBOT_TOKEN")
CHAT_ID = os.getenv("JOBBOT_CHAT_ID")
CHECK_INTERVAL = int(os.getenv("CHECK_INTERVAL", 60))

SEARCH_TERMS = [
    "SOC Analyst",
    "Security Operations Analyst",
    "Blue Team",
    "Cyber Security Analyst",
    "IT Support Engineer",
    "2nd Line Support",
    "Service Desk Engineer"
]

seen_links = set()

def send_telegram(message):
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"

    payload = {
        "chat_id": CHAT_ID,
        "text": message,
        "parse_mode": "HTML",
        "disable_web_page_preview": False
    }

    r = requests.post(url, data=payload)

    if r.status_code == 200:
        print("✅ Sent")
    else:
        print("❌ Telegram error:", r.text)


def get_feeds(term):
    query = term.replace(" ", "+")
    return [
        f"https://www.indeed.co.uk/rss?q={query}&l=United+Kingdom&sort=date",
        f"https://www.reed.co.uk/jobs/rss?keywords={query}&location=United+Kingdom",
        f"https://www.cwjobs.co.uk/rss/jobs?q={query}&l=United+Kingdom",
        f"https://www.totaljobs.com/rss/jobs?q={query}&l=United+Kingdom"
    ]


def scan_term(term):
    feeds = get_feeds(term)

    for feed_url in feeds:
        try:
            feed = feedparser.parse(feed_url)

            for entry in feed.entries[:10]:
                title = entry.title
                link = entry.link

                if link not in seen_links:
                    seen_links.add(link)

                    message = (
                        f"🚨 <b>UK Job Alert</b>\n\n"
                        f"<b>{title}</b>\n"
                        f"🎯 Search: {term}\n"
                        f"📍 United Kingdom\n\n"
                        f"{link}"
                    )

                    send_telegram(message)

        except Exception as e:
            print("Feed error:", e)


def main():
    print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] 🇬🇧 UK Job Sniper LIVE")

    while True:
        for term in SEARCH_TERMS:
            print(f"Scanning: {term}")
            scan_term(term)

        print("Cycle complete\n")
        time.sleep(CHECK_INTERVAL)


if __name__ == "__main__":
    main()

