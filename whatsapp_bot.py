import anthropic
import gspread
import requests
import json
import time
from google.oauth2.service_account import Credentials
from datetime import datetime
from config import (
    ANTHROPIC_API_KEY,
    GOOGLE_CREDENTIALS_FILE,
    SHEET_ID,
    WHATSAPP_ACCESS_TOKEN,
    WHATSAPP_PHONE_NUMBER_ID
)

# ================================
# CONFIG
# ================================
TEMPLATE_NAME = "aero_ai_classes_intro"
WA_API_URL = f"https://graph.facebook.com/v18.0/{WHATSAPP_PHONE_NUMBER_ID}/messages"
HEADERS = {
    "Authorization": f"Bearer {WHATSAPP_ACCESS_TOKEN}",
    "Content-Type": "application/json"
}

# AI ka personality
SYSTEM_PROMPT = """Tu Aero AI Classes ka friendly sales representative hai — tera naam Aryan hai.

Rules:
- Bilkul human jaisi baat kar — chatbot jaisi nahi
- Short messages bhej — 2-3 lines max
- Hindi-English mix (Hinglish) mein baat kar
- Friendly aur warm reh, pushy mat ban
- Agar interested hai toh demo class ke baare mein bata
- Agar nahi chahiye toh politely accept kar

Aero AI Classes ke baare mein:
- ChatGPT, AI Tools, Business Automation sikhate hain
- Free demo class available hai
- Online classes hain
- Price: ₹2999/month, ₹7999/3 months
- Batch timing: Morning 9am, Evening 7pm

Agar koi STOP likhe ya nahi bolein toh DND note karo — reply mein sirf 'DND' likho."""

# ================================
# Google Sheet connect
# ================================
def connect_to_sheet():
    scopes = [
        "https://www.googleapis.com/auth/spreadsheets",
        "https://www.googleapis.com/auth/drive"
    ]
    creds = Credentials.from_service_account_file(GOOGLE_CREDENTIALS_FILE, scopes=scopes)
    client = gspread.authorize(creds)
    return client.open_by_key(SHEET_ID).sheet1

# ================================
# Template message bhejo
# ================================
def send_template_message(phone, name):
    # Phone clean karo
    phone = str(phone).strip().replace("+", "").replace(" ", "").replace("-", "")
    if phone.startswith("0"):
        phone = "91" + phone[1:]
    if len(phone) == 10:
        phone = "91" + phone

    payload = {
        "messaging_product": "whatsapp",
        "to": phone,
        "type": "template",
        "template": {
            "name": TEMPLATE_NAME,
            "language": {"code": "en"},
            "components": [
                {
                    "type": "body",
                    "parameters": [
                        {"type": "text", "text": name or "Aap"}
                    ]
                }
            ]
        }
    }

    r = requests.post(WA_API_URL, json=payload, headers=HEADERS)
    result = r.json()

    if "messages" in result:
        print(f"   ✅ Message bheja: {name} ({phone})")
        return True
    else:
        print(f"   ❌ Failed: {name} ({phone}) — {result.get('error', {}).get('message', 'Unknown error')}")
        return False

# ================================
# AI se reply generate karo
# ================================
def generate_ai_reply(conversation_history, user_message):
    client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)

    messages = conversation_history + [
        {"role": "user", "content": user_message}
    ]

    response = client.messages.create(
        model="claude-opus-4-5",
        max_tokens=200,
        system=SYSTEM_PROMPT,
        messages=messages
    )

    reply = response.content[0].text.strip()
    return reply

# ================================
# Free-form message bhejo (24hr window mein)
# ================================
def send_text_message(phone, message):
    phone = str(phone).strip().replace("+", "").replace(" ", "").replace("-", "")
    if len(phone) == 10:
        phone = "91" + phone

    payload = {
        "messaging_product": "whatsapp",
        "to": phone,
        "type": "text",
        "text": {"body": message}
    }

    r = requests.post(WA_API_URL, json=payload, headers=HEADERS)
    result = r.json()

    if "messages" in result:
        return True
    else:
        print(f"   ❌ Reply failed: {result.get('error', {}).get('message', '')}")
        return False

# ================================
# Sheet mein status update karo
# ================================
def update_status(sheet, row_num, status, notes=""):
    try:
        sheet.update_cell(row_num, 11, status)      # Column K = Status
        sheet.update_cell(row_num, 12, notes)        # Column L = Notes
        sheet.update_cell(row_num, 13, datetime.now().strftime("%d-%m-%Y %H:%M"))  # Column M = Last Contact
    except Exception as e:
        print(f"   ⚠️ Status update failed: {e}")

# ================================
# Saare new leads ko message bhejo
# ================================
def send_to_all_new_leads():
    sheet = connect_to_sheet()
    all_data = sheet.get_all_values()

    if not all_data:
        print("Sheet khaali hai!")
        return

    headers = all_data[0]
    leads = all_data[1:]

    print(f"\n📋 Total leads: {len(leads)}")

    sent = 0
    skipped = 0
    failed = 0

    for i, row in enumerate(leads, start=2):
        if len(row) < 4:
            continue

        name = row[2] if len(row) > 2 else "Aap"
        phone = row[3] if len(row) > 3 else ""
        status = row[10] if len(row) > 10 else ""

        # Sirf new leads ko message karo
        if not phone:
            skipped += 1
            continue

        if status in ["Messaged", "DND", "Converted"]:
            print(f"   ⏭️ Skip ({status}): {name}")
            skipped += 1
            continue

        # Message bhejo
        success = send_template_message(phone, name)

        if success:
            update_status(sheet, i, "Messaged", "Template sent")
            sent += 1
        else:
            failed += 1

        # Rate limit — 1 second wait
        time.sleep(1)

    print(f"\n{'='*45}")
    print(f"✅ Sent: {sent} | ⏭️ Skipped: {skipped} | ❌ Failed: {failed}")
    print(f"{'='*45}")

# ================================
# Webhook se incoming message handle karo
# ================================
def handle_incoming_message(phone, user_message, conversation_history=[]):
    print(f"\n📩 Incoming: {phone} — {user_message}")

    sheet = connect_to_sheet()
    all_data = sheet.get_all_values()

    # AI se reply lo
    reply = generate_ai_reply(conversation_history, user_message)

    # DND check
    if reply.strip().upper() == "DND":
        # Sheet mein DND mark karo
        for i, row in enumerate(all_data[1:], start=2):
            if len(row) > 3 and str(row[3]).replace("+","").replace(" ","")[-10:] == str(phone)[-10:]:
                update_status(sheet, i, "DND", "User ne opt-out kiya")
                break
        print(f"   🚫 DND marked: {phone}")
        return

    # Reply bhejo
    send_text_message(phone, reply)

    # Sheet mein response note karo
    sentiment = "Positive" if any(w in user_message.lower() for w in ["haan", "yes", "interested", "chahiye", "batao", "demo"]) else \
                "Negative" if any(w in user_message.lower() for w in ["nahi", "no", "stop", "mat", "band"]) else "Neutral"

    for i, row in enumerate(all_data[1:], start=2):
        if len(row) > 3 and str(row[3]).replace("+","").replace(" ","")[-10:] == str(phone)[-10:]:
            update_status(sheet, i, f"Replied-{sentiment}", f"User: {user_message[:50]}")
            break

    print(f"   💬 Replied: {reply[:50]}...")
    print(f"   📊 Sentiment: {sentiment}")

# ================================
# Evening Report
# ================================
def generate_report():
    sheet = connect_to_sheet()
    all_data = sheet.get_all_values()

    if len(all_data) < 2:
        print("Koi data nahi hai!")
        return

    total = len(all_data) - 1
    messaged = sum(1 for r in all_data[1:] if len(r) > 10 and "Messaged" in r[10])
    positive = sum(1 for r in all_data[1:] if len(r) > 10 and "Positive" in r[10])
    negative = sum(1 for r in all_data[1:] if len(r) > 10 and "Negative" in r[10])
    neutral = sum(1 for r in all_data[1:] if len(r) > 10 and "Neutral" in r[10])
    dnd = sum(1 for r in all_data[1:] if len(r) > 10 and r[10] == "DND")
    converted = sum(1 for r in all_data[1:] if len(r) > 10 and r[10] == "Converted")

    print(f"\n{'='*45}")
    print(f"📊 EVENING REPORT — {datetime.now().strftime('%d %b %Y')}")
    print(f"{'='*45}")
    print(f"   Total Leads    : {total}")
    print(f"   Messages Sent  : {messaged}")
    print(f"   ✅ Positive    : {positive}")
    print(f"   😐 Neutral     : {neutral}")
    print(f"   ❌ Negative    : {negative}")
    print(f"   🚫 DND         : {dnd}")
    print(f"   💰 Converted   : {converted}")
    print(f"{'='*45}")

# ================================
# RUN
# ================================
if __name__ == "__main__":
    import sys

    if len(sys.argv) < 2:
        print("\n📱 WhatsApp Bot — Commands:")
        print("   python whatsapp_bot.py send     → Saare new leads ko message bhejo")
        print("   python whatsapp_bot.py report   → Evening report dekho")
        print("   python whatsapp_bot.py reply <phone> <message> → Manual reply test")
    
    elif sys.argv[1] == "send":
        print("\n🚀 Saare new leads ko message bhej raha hoon...")
        send_to_all_new_leads()
    
    elif sys.argv[1] == "report":
        generate_report()
    
    elif sys.argv[1] == "reply" and len(sys.argv) >= 4:
        phone = sys.argv[2]
        message = " ".join(sys.argv[3:])
        handle_incoming_message(phone, message)
    
    else:
        print("❌ Wrong command!")