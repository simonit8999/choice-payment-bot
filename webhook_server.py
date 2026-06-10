#!/usr/bin/env python3
# -*- coding: utf-8 -*-

from flask import Flask, request, jsonify
import sqlite3
import logging
import requests
from datetime import datetime

app = Flask(__name__)
logging.basicConfig(level=logging.INFO)

DB_PATH = '/tmp/payments.db'
BOT_TOKEN = "8630243895:AAGTaovmMcfBpnpmOqGfVO58mDVv5c4nlnk"
TRACKER_LINK = "https://t.me/Choice111Bot?startapp=choicetracker"

# ========== ID ГРУПП ==========
PREMIUM_GROUP_ID = -1003503823617   # Premium-группа
BASIC_GROUP_ID = -1003695482567     # Basic-группа

def send_telegram_message(user_id, text):
    """Отправляет сообщение пользователю через бота"""
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    payload = {
        "chat_id": user_id,
        "text": text,
        "parse_mode": "HTML"
    }
    try:
        requests.post(url, json=payload, timeout=10)
    except Exception as e:
        logging.error(f"Telegram send error: {e}")

def create_one_time_invite(user_id, plan):
    """Создаёт одноразовую ссылку-приглашение в группу"""
    
    if plan == 'premium':
        group_id = PREMIUM_GROUP_ID
    else:
        group_id = BASIC_GROUP_ID
    
    try:
        url = f"https://api.telegram.org/bot{BOT_TOKEN}/createChatInviteLink"
        payload = {
            "chat_id": group_id,
            "member_limit": 1,
            "name": f"user_{user_id}"
        }
        response = requests.post(url, json=payload, timeout=10)
        
        if response.status_code == 200:
            data = response.json()
            if data.get('ok'):
                invite_link = data['result']['invite_link']
                logging.info(f"Created invite for user {user_id}: {invite_link}")
                return invite_link
            else:
                logging.error(f"Telegram API error: {data}")
                return None
        else:
            logging.error(f"Telegram API error: {response.status_code} - {response.text}")
            return None
    except Exception as e:
        logging.error(f"Create invite error: {e}")
        return None

@app.route('/platega/webhook', methods=['POST'])
def platega_webhook():
    """Принимает уведомления от Platega"""
    data = request.json
    logging.info(f"Webhook received: {data}")
    
    transaction_id = data.get('transactionId')
    status = data.get('status')
    
    if not transaction_id:
        return jsonify({"error": "No transactionId"}), 400
    
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    
    if status == 'CONFIRMED':
        c.execute('SELECT user_id, plan FROM customers WHERE platega_invoice_id = ?', (transaction_id,))
        customer = c.fetchone()
        
        if customer:
            user_id, plan = customer
            
            c.execute('UPDATE customers SET status = "activated" WHERE user_id = ?', (user_id,))
            
            # Создаём одноразовую ссылку в группу
            invite_link = create_one_time_invite(user_id, plan)
            
            if plan == 'premium':
                c.execute('INSERT OR REPLACE INTO premium_users (user_id, activated_at, plan) VALUES (?, ?, ?)',
                         (user_id, datetime.now().isoformat(), 'premium'))
                
                if invite_link:
                    message = (
                        f"👑 <b>Premium активирован!</b>\n\n"
                        f"🔗 Твоя ссылка в Premium-группу:\n{invite_link}\n"
                        f"⚠️ Ссылка одноразовая, не передавай никому\n\n"
                        f"📅 Трекер: {TRACKER_LINK}"
                    )
                else:
                    message = (
                        f"👑 <b>Premium активирован!</b>\n\n"
                        f"⚠️ Не удалось создать ссылку в группу. Напиши @Piholaa\n\n"
                        f"📅 Трекер: {TRACKER_LINK}"
                    )
            else:
                c.execute('INSERT OR REPLACE INTO basic_users (user_id, activated_at, plan) VALUES (?, ?, ?)',
                         (user_id, datetime.now().isoformat(), 'basic'))
                
                if invite_link:
                    message = (
                        f"🎫 <b>Базовый доступ активирован!</b>\n\n"
                        f"🔗 Твоя ссылка в группу:\n{invite_link}\n"
                        f"⚠️ Ссылка одноразовая, не передавай никому\n\n"
                        f"📅 Трекер: {TRACKER_LINK}"
                    )
                else:
                    message = (
                        f"🎫 <b>Базовый доступ активирован!</b>\n\n"
                        f"⚠️ Не удалось создать ссылку в группу. Напиши @Piholaa\n\n"
                        f"📅 Трекер: {TRACKER_LINK}"
                    )
            
            conn.commit()
            conn.close()
            
            send_telegram_message(user_id, message)
            
            logging.info(f"Payment confirmed: user={user_id}, plan={plan}")
            return jsonify({"status": "ok"}), 200
        else:
            conn.close()
            logging.error(f"Transaction not found: {transaction_id}")
            return jsonify({"error": "Transaction not found"}), 404
    
    elif status == 'CANCELED':
        c.execute('UPDATE customers SET status = "canceled" WHERE platega_invoice_id = ?', (transaction_id,))
        conn.commit()
        conn.close()
        logging.info(f"Payment canceled: {transaction_id}")
        return jsonify({"status": "ok"}), 200
    
    else:
        conn.close()
        logging.info(f"Unknown status: {status}")
        return jsonify({"status": "ignored"}), 200

@app.route('/')
def home():
    return "CHOICE Webhook Server is running"

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=8000)
