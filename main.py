import requests
import feedparser
import time
import os
from datetime import datetime

BOT_TOKEN = os.getenv("JOBBOT_TOKEN")
CHAT_ID = os.getenv("JOBBOT_CHAT_ID")
CHECK_INTERVAL = int(os.getenv("CHECK_INTERVAL", 60))

SEARCH_TERMS = [
    "SOC Analyst UK",
    "Blue Team UK",
    "Cyber Security Analyst UK",
    "IT Support Engineer UK",
    "2nd Line Support UK"
]

seen_links = set()


def send_telegram(message):
    if not BOT_TOKEN or not CHAT_ID:
        raise Exception("Telegram credentials missing")

    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    payload = {
        "chat_id": CHAT_ID,
        "text": message,
        "parse_mode": "HTML"
    }

    r = requests.post(url, data=payload)

    if r.status_code != 200:
        raise Exception(f"Telegram error: {r.text}")

    print("✅ Sent to Telegram")


def get_feeds(term):
    query = term.replace(" ", "+")
    return [
        f"https://www.indeed.co.uk/rss?q={query}&l=United+Kingdom&sort=date",
        f"https://www.reed.co.uk/jobs/rss?keywords={query}&location=United+Kingdom",
        f"https://www.totaljobs.com/rss/jobs?q={query}&l=United+Kingdom"
    ]


def scan_term(term):
    feeds = get_feeds(term)

    for feed_url in feeds:
        try:
            print(f"Checking feed: {feed_url}")
            feed = feedparser.parse(feed_url)
            print(f"Entries found: {len(feed.entries)}")

            for entry in feed.entries[:10]:
                title = entry.title
                link = entry.link

                if link not in seen_links:
                    seen_links.add(link)

                    message = (
                        f"🚨 <b>UK Job Alert</b>\n\n"
                        f"<b>{title}</b>\n"
                        f"🎯 Search: {term}\n\n"
                        f"{link}"
                    )

                    send_telegram(message)

        except Exception as e:
            print("Feed error:", e)


def main():
    print("🇬🇧 UK Job Sniper Running")
    send_telegram(f"🎯 UK Job Sniper ONLINE\nTime: {datetime.now()}")

    while True:
        for term in SEARCH_TERMS:
            print(f"Scanning: {term}")
            scan_term(term)

        print("Sleeping...\n")
        time.sleep(CHECK_INTERVAL)


if __name__ == "__main__":
    main()

