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

@app.route('/platega/webhook', methods=['POST'])
def platega_webhook():
    """Принимает уведомления от Platega"""
    data = request.json
    
    logging.info(f"Webhook received: {data}")
    
    # Platega отправляет transactionId и status
    transaction_id = data.get('transactionId')
    status = data.get('status')
    payload = data.get('payload')  # Там user_id
    
    if not transaction_id:
        return jsonify({"error": "No transactionId"}), 400
    
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    
    if status == 'CONFIRMED':
        # Находим пользователя по transaction_id
        c.execute('SELECT user_id, plan FROM customers WHERE platega_invoice_id = ?', (transaction_id,))
        customer = c.fetchone()
        
        if customer:
            user_id, plan = customer
            
            # Обновляем статус
            c.execute('UPDATE customers SET status = "activated" WHERE user_id = ?', (user_id,))
            
            # Добавляем в premium_users или basic_users
            if plan == 'premium':
                c.execute('INSERT OR REPLACE INTO premium_users (user_id, activated_at, plan) VALUES (?, ?, ?)',
                         (user_id, datetime.now().isoformat(), 'premium'))
                message = f"👑 <b>Premium активирован!</b>\n\n🔗 Трекер: {TRACKER_LINK}"
            else:
                c.execute('INSERT OR REPLACE INTO basic_users (user_id, activated_at, plan) VALUES (?, ?, ?)',
                         (user_id, datetime.now().isoformat(), 'basic'))
                message = f"🎫 <b>Базовый доступ активирован!</b>\n\n🔗 Трекер: {TRACKER_LINK}"
            
            conn.commit()
            conn.close()
            
            # Отправляем пользователю
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
