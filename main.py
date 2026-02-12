import requests
import feedparser
import time
import os
from datetime import datetime

BOT_TOKEN = os.getenv("JOBBOT_TOKEN")
CHAT_ID = os.getenv("JOBBOT_CHAT_ID")
CHECK_INTERVAL = int(os.getenv("CHECK_INTERVAL", 300))

SEARCH_TERMS = [
    "SOC Analyst UK",
    "Blue Team UK",
    "Cyber Security Analyst UK",
    "IT Support Engineer UK",
    "2nd Line Support UK"
]

seen_links = set()


def send_telegram(message):
    try:
        url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
        payload = {
            "chat_id": CHAT_ID,
            "text": message,
            "parse_mode": "HTML"
        }
        requests.post(url, data=payload, timeout=10)
    except Exception as e:
        print("Telegram error:", e)


def get_feed(term):
    query = term.replace(" ", "+")
    return f"https://www.indeed.co.uk/rss?q={query}&l=United+Kingdom&sort=date"


def scan_term(term):
    feed_url = get_feed(term)

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
                    f"🎯 {term}\n\n"
                    f"{link}"
                )

                send_telegram(message)

    except Exception as e:
        print("Feed error:", e)


def main():
    print("🇬🇧 UK Job Sniper Running")

    while True:
        for term in SEARCH_TERMS:
            print("Scanning:", term)
            scan_term(term)

        print("Sleeping...\n")
        time.sleep(CHECK_INTERVAL)


if __name__ == "__main__":
    main()

