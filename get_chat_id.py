"""
Run this after sending any message to your Telegram bot.
It will print your chat ID.
"""
import httpx

TOKEN = "8541179917:AAFJQADyDPUpTRDwdeQgMH-m6oNLTUxqyUg"

resp = httpx.get(f"https://api.telegram.org/bot{TOKEN}/getUpdates")
data = resp.json()

if not data.get("result"):
    print("No messages found.")
    print("Please send ANY message to your bot in Telegram, then run this script again.")
else:
    for update in data["result"]:
        msg = update.get("message") or update.get("channel_post")
        if msg:
            chat = msg["chat"]
            print(f"Chat ID: {chat['id']}")
            print(f"Chat type: {chat['type']}")
            print(f"Name: {chat.get('first_name', '')} {chat.get('last_name', '')} {chat.get('title', '')}")
            break
