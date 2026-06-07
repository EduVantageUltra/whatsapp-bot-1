from flask import Flask, request, jsonify
import anthropic
import gspread
import requests
import json
import os
from google.oauth2.service_account import Credentials
from datetime import datetime

app = Flask(__name__)

ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY")
SHEET_ID = os.environ.get("SHEET_ID")
WHATSAPP_ACCESS_TOKEN = os.environ.get("WHATSAPP_ACCESS_TOKEN")
WHATSAPP_PHONE_NUMBER_ID = os.environ.get("WHATSAPP_PHONE_NUMBER_ID")
VERIFY_TOKEN = os.environ.get("VERIFY_TOKEN", "aero_webhook_2024")
GOOGLE_CREDENTIALS_FILE = os.environ.get("GOOGLE_CREDENTIALS_FILE", "credentials.json")

if not ANTHROPIC_API_KEY:
    try:
        from config import (
            ANTHROPIC_API_KEY,
            GOOGLE_CREDENTIALS_FILE,
            SHEET_ID,
            WHATSAPP_ACCESS_TOKEN,
            WHATSAPP_PHONE_NUMBER_ID,
            VERIFY_TOKEN
        )
    except:
        pass

conversations = {}
dnd_numbers = set()
client_names = {}  # Phone → Name mapping

WA_API_URL = f"https://graph.facebook.com/v18.0/{WHATSAPP_PHONE_NUMBER_ID}/messages"
HEADERS = {
    "Authorization": f"Bearer {WHATSAPP_ACCESS_TOKEN}",
    "Content-Type": "application/json"
}

SYSTEM_PROMPT = """Tu Aero AI Classes ka friendly representative hai — tera naam Aryan hai.

LANGUAGE RULE — SABSE IMPORTANT:
- Client jis language mein likhe — USI language mein reply kar
- Hindi mein likha → Pure Hindi mein reply
- English mein likha → Pure English mein reply
- Hinglish mein likha → Hinglish mein reply
- Automatically detect kar — kabhi mat poochh

TONE & RESPECT RULES:
- Har insaan ko respect de — chahe wo kuch bhi poochhe
- Bilkul human jaisi baat kar — chatbot jaisi nahi
- Short messages — 2-3 lines max
- Friendly aur warm reh, pushy bilkul mat ban
- Casual reh — formal mat ban
- Thode emojis use kar — zyada nahi
- Kabhi bhi kisi ko judge mat kar
- Sabke saath ek jaisa respectful behavior

HARMFUL CONTENT RULES:
- Koi bhi illegal ya harmful topic aaye → politely topic change kar
- Kisi ke baare mein bura mat bol
- Politics, religion pe seedha comment mat kar
- Agar koi galat cheez maange → "Yaar, main isme help nahi kar sakta, but AI Classes ke baare mein kuch poochna ho toh batao! 😊"
- Kabhi aggressive ya rude mat ho — chahe client ho bhi

NAME COLLECTION RULE — BAHUT IMPORTANT:
- Sabse PEHLE client ka naam poochho — hamesha
- Pehle message mein naam nahi hai → pehle naam poochho, kuch aur mat poochho
- Naam mil jaaye → uska naam use karke personal baat karo
- Ek baar naam mil jaaye → dobara mat poochho

Aero AI Classes ke baare mein:
- ChatGPT, AI Tools, Business Automation sikhate hain
- Free demo class available hai
- Online classes hain
- Price: Rs.2999/month, Rs.7999/3 months
- Batch timing: Morning 9am, Evening 7pm

Conversation flow:
1. Pehla message → Warmly greet + NAAM POOCHHO
2. Naam mila → "Nice to meet you [naam]! 😊" + kya jaanna chahte ho poochho
3. Interest show kare → Demo class offer karo
4. Price pooche → Batao aur value explain karo
5. Ready ho → Enrollment process batao
6. Not interested → Politely accept karo

Agar koi STOP/nahi/mat/band/no/not interested likhe → sirf ek word: DND"""


def connect_sheet():
    scopes = [
        "https://www.googleapis.com/auth/spreadsheets",
        "https://www.googleapis.com/auth/drive"
    ]
    creds = Credentials.from_service_account_file(
        GOOGLE_CREDENTIALS_FILE, scopes=scopes
    )
    client = gspread.authorize(creds)
    return client.open_by_key(SHEET_ID).sheet1


def send_whatsapp_message(phone, message):
    payload = {
        "messaging_product": "whatsapp",
        "to": phone,
        "type": "text",
        "text": {"body": message}
    }
    r = requests.post(WA_API_URL, json=payload, headers=HEADERS)
    result = r.json()
    if "messages" in result:
        print(f"   ✅ Sent to {phone}")
    else:
        print(f"   ❌ Failed: {result.get('error', {}).get('message', 'Unknown')}")
    return result


def get_ai_reply(phone, user_message):
    if phone not in conversations:
        conversations[phone] = []

    conversations[phone].append({
        "role": "user",
        "content": user_message
    })

    if len(conversations[phone]) > 10:
        conversations[phone] = conversations[phone][-10:]

    client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
    response = client.messages.create(
        model="claude-opus-4-5",
        max_tokens=200,
        system=SYSTEM_PROMPT,
        messages=conversations[phone]
    )

    reply = response.content[0].text.strip()

    conversations[phone].append({
        "role": "assistant",
        "content": reply
    })

    return reply


def get_sentiment(message):
    msg = message.lower()
    positive_words = [
        "haan", "yes", "interested", "chahiye", "batao",
        "demo", "join", "enroll", "kab", "kaisa", "details",
        "price", "cost", "kitna", "bata", "okay", "ok", "sure"
    ]
    negative_words = [
        "nahi", "nhi", "no", "stop", "mat", "band",
        "not interested", "remove", "unsubscribe", "chhoddo"
    ]
    if any(w in msg for w in positive_words):
        return "Positive"
    elif any(w in msg for w in negative_words):
        return "Negative"
    return "Neutral"


def extract_name_from_message(phone, user_message):
    """
    Conversation history check karo — agar AI ne naam pucha tha
    aur client ne short reply diya toh wo naam hai
    """
    if phone not in conversations:
        return None

    history = conversations[phone]

    # Agar 2-4 messages hain — naam pucha gaya hoga
    if len(history) >= 2:
        last_bot_msg = None
        for msg in reversed(history[:-1]):  # Latest bot message dhundo
            if msg["role"] == "assistant":
                last_bot_msg = msg["content"].lower()
                break

        if last_bot_msg and any(w in last_bot_msg for w in
                                ["naam", "name", "aapka naam", "your name",
                                 "introduce", "bataiye", "kaun"]):
            # Client ka reply naam ho sakta hai
            words = user_message.strip().split()
            skip_words = [
                "haan", "nahi", "nhi", "yes", "no", "okay", "ok",
                "hello", "hi", "hey", "kya", "hai", "kaise", "demo",
                "price", "kitna", "bata", "classes", "join"
            ]
            if (len(words) <= 3 and
                    not any(w in user_message.lower() for w in skip_words)):
                return user_message.strip().title()

    return None


def log_to_sheet(phone, message, reply, sentiment, name=None):
    try:
        sheet = connect_sheet()
        now = datetime.now()
        all_data = sheet.get_all_values()

        phone_clean = str(phone).replace("+", "").replace(" ", "")[-10:]

        row_found = None
        for i, row in enumerate(all_data[1:], start=2):
            if len(row) > 3:
                existing = str(row[3]).replace("+", "").replace(" ", "")[-10:]
                if existing == phone_clean:
                    row_found = i
                    break

        if row_found:
            if name:
                sheet.update_cell(row_found, 3, name)
            sheet.update_cell(row_found, 11, f"Replied-{sentiment}")
            sheet.update_cell(row_found, 12, f"User: {message[:50]} | Bot: {reply[:50]}")
            sheet.update_cell(row_found, 13, now.strftime("%d-%m-%Y %H:%M"))
        else:
            sheet.append_row([
                now.strftime("%d-%m-%Y"),
                now.strftime("%H:%M:%S"),
                name or "Unknown",
                "+" + phone_clean,
                "", "", "", "", "", "",
                f"Replied-{sentiment}",
                f"User: {message[:50]}",
                now.strftime("%d-%m-%Y %H:%M")
            ])

        print(f"   📊 Sheet updated — {sentiment} | Name: {name or 'N/A'}")

    except Exception as e:
        print(f"   ⚠️ Sheet error: {e}")


def mark_dnd_in_sheet(phone):
    try:
        sheet = connect_sheet()
        all_data = sheet.get_all_values()
        phone_clean = str(phone).replace("+", "").replace(" ", "")[-10:]

        for i, row in enumerate(all_data[1:], start=2):
            if len(row) > 3:
                existing = str(row[3]).replace("+", "").replace(" ", "")[-10:]
                if existing == phone_clean:
                    sheet.update_cell(i, 11, "DND")
                    sheet.update_cell(i, 12, "User ne opt-out kiya")
                    sheet.update_cell(i, 13, datetime.now().strftime("%d-%m-%Y %H:%M"))
                    break
    except Exception as e:
        print(f"   ⚠️ DND sheet error: {e}")


@app.route("/webhook", methods=["GET"])
def verify_webhook():
    mode = request.args.get("hub.mode")
    token = request.args.get("hub.verify_token")
    challenge = request.args.get("hub.challenge")

    print(f"\n🔍 Verification — mode: {mode}, token: {token}")

    if mode == "subscribe" and token == VERIFY_TOKEN:
        print("✅ Webhook verified!")
        return challenge, 200
    else:
        print("❌ Verification failed!")
        return "Forbidden", 403


@app.route("/webhook", methods=["POST"])
def handle_message():
    data = request.json
    print(f"\n📨 Webhook received")

    try:
        entry = data.get("entry", [{}])[0]
        changes = entry.get("changes", [{}])[0]
        value = changes.get("value", {})

        if "messages" not in value:
            return jsonify({"status": "ok"}), 200

        message = value["messages"][0]
        phone = message["from"]
        msg_type = message["type"]

        if msg_type != "text":
            print(f"   ⏭️ Non-text ignored: {msg_type}")
            return jsonify({"status": "ok"}), 200

        user_message = message["text"]["body"]
        print(f"\n{'='*45}")
        print(f"📩 From: {phone}")
        print(f"💬 Message: {user_message}")
        print(f"{'='*45}")

        if phone in dnd_numbers:
            print(f"🚫 DND — ignoring {phone}")
            return jsonify({"status": "ok"}), 200

        # Naam extract karo reply se pehle
        name = extract_name_from_message(phone, user_message)
        if name:
            client_names[phone] = name
            print(f"   👤 Name detected: {name}")

        reply = get_ai_reply(phone, user_message)
        print(f"🤖 Reply: {reply}")

        if reply.strip().upper() == "DND":
            dnd_numbers.add(phone)
            mark_dnd_in_sheet(phone)
            print(f"🚫 DND marked: {phone}")
            return jsonify({"status": "ok"}), 200

        send_whatsapp_message(phone, reply)

        sentiment = get_sentiment(user_message)
        saved_name = client_names.get(phone)
        log_to_sheet(phone, user_message, reply, sentiment, saved_name)

    except Exception as e:
        print(f"❌ Error: {e}")
        import traceback
        traceback.print_exc()

    return jsonify({"status": "ok"}), 200


@app.route("/", methods=["GET"])
def home():
    return """
    <h2>🤖 Aero WhatsApp Bot</h2>
    <p>Status: <b style='color:green'>Running</b></p>
    <p>Webhook: /webhook</p>
    """, 200


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    print("\n" + "="*50)
    print("🚀 AERO WHATSAPP BOT STARTING...")
    print("="*50)
    print(f"📡 Port: {port}")
    print(f"🔑 Verify Token: {VERIFY_TOKEN}")
    print(f"📱 Phone Number ID: {WHATSAPP_PHONE_NUMBER_ID}")
    print("="*50 + "\n")
    app.run(host="0.0.0.0", port=port, debug=False)