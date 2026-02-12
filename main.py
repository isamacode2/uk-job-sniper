import requests
import feedparser
import time
import os
from datetime import datetime
from dotenv import load_dotenv

load_dotenv()

BOT_TOKEN = os.getenv("JOBBOT_TOKEN")
CHAT_ID = os.getenv("JOBBOT_CHAT_ID")
CHECK_INTERVAL = int(os.getenv("CHECK_INTERVAL", 120))

SEARCH_TERMS = [
    "SOC Analyst UK",
    "Blue Team UK",
    "Cyber Security Analyst UK",
    "IT Support Engineer UK",
    "2nd Line Support UK"
]

seen_links = set()

HEADERS = {
    "User-Agent": "Mozilla/5.0"
}


def send_telegram(message):
    if not BOT_TOKEN or not CHAT_ID:
        print("Telegram credentials missing")
        return

    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"

    payload = {
        "chat_id": CHAT_ID,
        "text": message,
        "parse_mode": "HTML"
    }

    try:
        requests.post(url, data=payload, timeout=15)
        print("Sent")
    except Exception as e:
        print("Telegram error:", e)


def get_feeds(term):
    query = term.replace(" ", "+")
    return [
        f"https://www.indeed.co.uk/rss?q={query}&sort=date",
        f"https://www.reed.co.uk/jobs/rss?keywords={query}",
        f"https://remoteok.com/remote-security-jobs.rss"
    ]


def scan_term(term):
    feeds = get_feeds(term)

    for feed_url in feeds:
        try:
            response = requests.get(feed_url, headers=HEADERS, timeout=15)
            feed = feedparser.parse(response.content)

            for entry in feed.entries[:5]:
                title = entry.title
                link = entry.link

                if "United Kingdom" not in title and "UK" not in title:
                    continue

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
            print("Feed error:", feed_url, e)


def main():
    print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] 🇬🇧 UK Job Sniper LIVE")

    while True:
        for term in SEARCH_TERMS:
            print("Scanning:", term)
            scan_term(term)

        print("Cycle complete\n")
        time.sleep(CHECK_INTERVAL)


if __name__ == "__main__":
    main()

