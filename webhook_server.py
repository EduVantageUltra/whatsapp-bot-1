from flask import Flask, request, jsonify
import anthropic
import gspread
import requests
import json
from google.oauth2.service_account import Credentials
from datetime import datetime
from config import (
    ANTHROPIC_API_KEY,
    GOOGLE_CREDENTIALS_FILE,
    SHEET_ID,
    WHATSAPP_ACCESS_TOKEN,
    WHATSAPP_PHONE_NUMBER_ID,
    VERIFY_TOKEN
)

app = Flask(__name__)

# Per number conversation history
conversations = {}

# DND numbers set
dnd_numbers = set()

WA_API_URL = f"https://graph.facebook.com/v18.0/{WHATSAPP_PHONE_NUMBER_ID}/messages"
HEADERS = {
    "Authorization": f"Bearer {WHATSAPP_ACCESS_TOKEN}",
    "Content-Type": "application/json"
}

# ================================
# AI SYSTEM PROMPT
# ================================
SYSTEM_PROMPT = """Tu Aero AI Classes ka friendly representative hai — tera naam Aryan hai.

LANGUAGE RULE — SABSE IMPORTANT:
- Client jis language mein likhe — USI language mein reply kar
- Hindi mein likha → Pure Hindi mein reply
- English mein likha → Pure English mein reply
- Hinglish mein likha → Hinglish mein reply
- Automatically detect kar — kabhi mat poochh ki kaun si language use karein

TONE RULES:
- Bilkul human jaisi baat kar — chatbot jaisi bilkul nahi
- Short messages — 2-3 lines max
- Friendly aur warm reh, pushy bilkul mat ban
- Casual reh — formal mat ban
- Thode emojis use kar — zyada nahi

Aero AI Classes ke baare mein:
- ChatGPT, AI Tools, Business Automation sikhate hain
- Free demo class available hai
- Online classes hain
- Price: Rs.2999/month, Rs.7999/3 months
- Batch timing: Morning 9am, Evening 7pm
- Contact: Aryan se directly baat kar sakte hain

Conversation flow:
1. Greeting → Warm response, poochho kya jaanna chahte hain
2. Interest show kare → Demo class offer karo
3. Price pooche → Batao aur value explain karo
4. Ready ho → Enrollment process batao
5. Not interested → Politely accept karo

Agar koi STOP/nahi/mat/band/no/not interested likhe → sirf ek word likho: DND"""

# ================================
# GOOGLE SHEET CONNECT
# ================================
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

# ================================
# WHATSAPP MESSAGE SEND
# ================================
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

# ================================
# AI REPLY GENERATE
# ================================
def get_ai_reply(phone, user_message):
    if phone not in conversations:
        conversations[phone] = []

    conversations[phone].append({
        "role": "user",
        "content": user_message
    })

    # Last 10 messages rakho only
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

# ================================
# SENTIMENT DETECT
# ================================
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

# ================================
# LOG TO GOOGLE SHEET
# ================================
def log_to_sheet(phone, message, reply, sentiment):
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
            sheet.update_cell(row_found, 11, f"Replied-{sentiment}")
            sheet.update_cell(row_found, 12, f"User: {message[:50]} | Bot: {reply[:50]}")
            sheet.update_cell(row_found, 13, now.strftime("%d-%m-%Y %H:%M"))
        else:
            sheet.append_row([
                now.strftime("%d-%m-%Y"),
                now.strftime("%H:%M:%S"),
                "Unknown",
                "+" + phone_clean,
                "", "", "", "", "", "",
                f"Replied-{sentiment}",
                f"User: {message[:50]}",
                now.strftime("%d-%m-%Y %H:%M")
            ])

        print(f"   📊 Sheet updated — {sentiment}")

    except Exception as e:
        print(f"   ⚠️ Sheet error: {e}")

# ================================
# DND MARK IN SHEET
# ================================
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

# ================================
# WEBHOOK ROUTES
# ================================

@app.route("/webhook", methods=["GET"])
def verify_webhook():
    """Meta webhook verification"""
    mode = request.args.get("hub.mode")
    token = request.args.get("hub.verify_token")
    challenge = request.args.get("hub.challenge")

    print(f"\n🔍 Verification attempt — mode: {mode}, token: {token}")

    if mode == "subscribe" and token == VERIFY_TOKEN:
        print("✅ Webhook verified successfully!")
        return challenge, 200
    else:
        print("❌ Verification failed!")
        return "Forbidden", 403

@app.route("/webhook", methods=["POST"])
def handle_message():
    """Incoming WhatsApp message handle karo"""
    data = request.json
    print(f"\n📨 Incoming webhook data received")

    try:
        entry = data.get("entry", [{}])[0]
        changes = entry.get("changes", [{}])[0]
        value = changes.get("value", {})

        # Sirf messages handle karo
        if "messages" not in value:
            return jsonify({"status": "ok"}), 200

        message = value["messages"][0]
        phone = message["from"]
        msg_type = message["type"]

        # Sirf text messages
        if msg_type != "text":
            print(f"   ⏭️ Non-text message ignored: {msg_type}")
            return jsonify({"status": "ok"}), 200

        user_message = message["text"]["body"]
        print(f"\n{'='*45}")
        print(f"📩 From: {phone}")
        print(f"💬 Message: {user_message}")
        print(f"{'='*45}")

        # DND check
        if phone in dnd_numbers:
            print(f"🚫 DND — ignoring {phone}")
            return jsonify({"status": "ok"}), 200

        # AI reply generate karo
        reply = get_ai_reply(phone, user_message)
        print(f"🤖 AI Reply: {reply}")

        # DND reply check
        if reply.strip().upper() == "DND":
            dnd_numbers.add(phone)
            mark_dnd_in_sheet(phone)
            print(f"🚫 DND marked: {phone}")
            return jsonify({"status": "ok"}), 200

        # Reply bhejo
        send_whatsapp_message(phone, reply)

        # Sheet mein log karo
        sentiment = get_sentiment(user_message)
        log_to_sheet(phone, user_message, reply, sentiment)

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
    <p>Webhook URL: /webhook</p>
    """, 200

# ================================
# START SERVER
# ================================
if __name__ == "__main__":
    print("\n" + "="*50)
    print("🚀 AERO WHATSAPP BOT STARTING...")
    print("="*50)
    print(f"📡 Local URL: http://localhost:5000")
    print(f"🔑 Verify Token: {VERIFY_TOKEN}")
    print(f"📱 Phone Number ID: {WHATSAPP_PHONE_NUMBER_ID}")
    print(f"💬 Webhook endpoint: /webhook")
    print("="*50)
    print("\n⏳ Ngrok URL chahiye Meta ke liye!")
    print("Doosri PowerShell mein chalao: ngrok http 5000\n")
    app.run(port=5000, debug=True)