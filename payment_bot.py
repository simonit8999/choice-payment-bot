#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import asyncio
import sqlite3
import logging
import requests
import uuid
from datetime import datetime, timedelta
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, BotCommand, ReplyKeyboardMarkup, KeyboardButton
from telegram.ext import (
    Application, CommandHandler, CallbackQueryHandler, MessageHandler, filters,
    ContextTypes
)

# ========== НАСТРОЙКИ ==========
BOT_TOKEN = "8630243895:AAGTaovmMcfBpnpmOqGfVO58mDVv5c4nlnk"
ADMIN_ID = 966054850

PRIVACY_POLICY = "https://telegra.ph/Politika-konfidencialnosti-04-01-26"
TERMS_OF_USE = "https://telegra.ph/Polzovatelskoe-soglashenie-04-01-19"
TRACKER_LINK = "https://t.me/Choice111Bot?startapp=choicetracker"
FREE_BOT_LINK = "https://t.me/free3daysbot"

# Platega
PLATEGA_MERCHANT_ID = "eacaca79-afbc-43d6-aa0d-474a495e75d3"
PLATEGA_SECRET_KEY = "3E8QrD5mn6VZkq9r16Xas3spTo0qgGUHAlfRstoaGdH93Nkee5kcuXqEcW6zegAovpo9TanEcH1QZGch68nup0EcBnaPpxo1VwX7"

logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO)

# ========== БАЗА ДАННЫХ ==========
DB_PATH = '/tmp/payments.db'

def init_db():
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute('''CREATE TABLE IF NOT EXISTS customers
                 (user_id INTEGER PRIMARY KEY, username TEXT, first_name TEXT,
                  purchased_at TEXT, plan TEXT, amount INTEGER, status TEXT,
                  platega_invoice_id TEXT)''')
    c.execute('''CREATE TABLE IF NOT EXISTS premium_users
                 (user_id INTEGER PRIMARY KEY, activated_at TEXT, plan TEXT)''')
    c.execute('''CREATE TABLE IF NOT EXISTS basic_users
                 (user_id INTEGER PRIMARY KEY, activated_at TEXT, plan TEXT)''')
    c.execute('''CREATE TABLE IF NOT EXISTS scheduled_broadcasts 
                 (id INTEGER PRIMARY KEY AUTOINCREMENT, 
                  broadcast_type TEXT, text TEXT, has_button INTEGER DEFAULT 0, 
                  button_text TEXT, button_url TEXT, send_time TEXT, sent INTEGER DEFAULT 0)''')
    conn.commit()
    conn.close()

def add_customer(user_id, username, first_name, plan, amount, status="pending", platega_invoice_id=None):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute('''INSERT OR REPLACE INTO customers (user_id, username, first_name, purchased_at, plan, amount, status, platega_invoice_id)
                 VALUES (?, ?, ?, ?, ?, ?, ?, ?)''',
              (user_id, username, first_name, datetime.now().isoformat(), plan, amount, status, platega_invoice_id))
    conn.commit()
    conn.close()

def get_all_customers():
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute('SELECT * FROM customers ORDER BY purchased_at DESC')
    rows = c.fetchall()
    conn.close()
    return rows

def get_all_premium_users():
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute('SELECT user_id FROM premium_users')
    rows = c.fetchall()
    conn.close()
    return [r[0] for r in rows]

def get_all_basic_users():
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute('SELECT user_id FROM basic_users')
    rows = c.fetchall()
    conn.close()
    return [r[0] for r in rows]

def get_customers_by_plan(plan):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute('SELECT user_id FROM customers WHERE plan = ?', (plan,))
    rows = c.fetchall()
    conn.close()
    return [r[0] for r in rows]

def has_seen_welcome(user_id):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute('SELECT status FROM customers WHERE user_id = ?', (user_id,))
    row = c.fetchone()
    conn.close()
    return row and row[0] == 'seen_welcome'

def add_scheduled_broadcast(broadcast_type, text, has_button, button_text, button_url, send_time):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute('''INSERT INTO scheduled_broadcasts 
                 (broadcast_type, text, has_button, button_text, button_url, send_time) 
                 VALUES (?, ?, ?, ?, ?, ?)''',
              (broadcast_type, text, has_button, button_text, button_url, send_time))
    conn.commit()
    conn.close()

# ========== КЛАВИАТУРЫ ==========
def get_reply_keyboard():
    keyboard = [
        [KeyboardButton("🏠 Главная")],
        [KeyboardButton("👑 Premium"), KeyboardButton("🎫 Базовый")],
        [KeyboardButton("🆓 Бесплатно"), KeyboardButton("❓ Помощь")]
    ]
    return ReplyKeyboardMarkup(keyboard, resize_keyboard=True)

# ========== PLATEGA ==========
async def create_platega_payment(user_id, amount, plan):
    order_id = f"{user_id}_{uuid.uuid4().hex[:8]}"
    
    payload = {
        "amount": amount,
        "currency": "RUB",
        "order_id": order_id,
        "description": f"CHOICE | {plan}",
        "url_callback": "https://choice-tracker-api.onrender.com/platega/webhook"
    }
    
    headers = {
        "X-MerchantId": PLATEGA_MERCHANT_ID,
        "X-Secret": PLATEGA_SECRET_KEY,
        "Content-Type": "application/json"
    }
    
    try:
        response = requests.post(
            "https://app.platega.io/api/v1/invoice/create",
            json=payload, headers=headers, timeout=30
        )
        
        if response.status_code == 200:
            data = response.json()
            payment_url = data.get("url") or data.get("payment_url")
            invoice_id = data.get("id") or data.get("invoice_id")
            
            if payment_url:
                add_customer(user_id, "", "", plan, amount, "pending", invoice_id)
                return payment_url
        
        logging.error(f"Platega error: {response.text}")
        return None
    except Exception as e:
        logging.error(f"Platega failed: {e}")
        return None

# ========== РАССЫЛКИ ==========
temp_data = {}

def clear_broadcast_state(user_id):
    if user_id in temp_data: del temp_data[user_id]

async def send_to_users(bot, user_ids, text, button_text=None, button_url=None):
    sent_count = 0
    keyboard = InlineKeyboardMarkup([[InlineKeyboardButton(button_text, url=button_url)]]) if button_url and button_text else None
    for user_id in user_ids:
        try:
            await bot.send_message(chat_id=user_id, text=text, reply_markup=keyboard)
            sent_count += 1
            await asyncio.sleep(0.05)
        except: pass
    return sent_count

async def start_broadcast(update, context, broadcast_type):
    if update.effective_user.id != ADMIN_ID: return
    clear_broadcast_state(update.effective_user.id)
    temp_data[update.effective_user.id] = {'type': broadcast_type, 'step': 'awaiting_text', 'text': None, 'button_text': None, 'button_url': None}
    names = {"all": "всем", "premium": "Premium", "basic": "Базовым"}
    await update.message.reply_text(f"📝 Рассылка {names.get(broadcast_type, '')}\nВведи текст:")

async def all_broadcast(update, context): await start_broadcast(update, context, "all")
async def premium_broadcast(update, context): await start_broadcast(update, context, "premium")
async def basic_broadcast(update, context): await start_broadcast(update, context, "basic")
async def cancel_broadcast(update, context): clear_broadcast_state(update.effective_user.id); await update.message.reply_text("❌ Отменено.")

async def handle_broadcast_text(update, context):
    user_id = update.effective_user.id
    if user_id not in temp_data or temp_data[user_id].get('step') != 'awaiting_text': return False
    temp_data[user_id]['text'] = update.message.text; temp_data[user_id]['step'] = 'choosing_action'
    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("📤 Отправить", callback_data="pbcast_send_now")],
        [InlineKeyboardButton("🔘 Кнопка", callback_data="pbcast_add_button")],
        [InlineKeyboardButton("📅 План", callback_data="pbcast_schedule")],
        [InlineKeyboardButton("❌ Отмена", callback_data="pbcast_cancel")]
    ])
    await update.message.reply_text("✅ Текст сохранён:", reply_markup=keyboard)
    return True

async def handle_broadcast_callback(update, context):
    query = update.callback_query; await query.answer()
    user_id = query.from_user.id
    if user_id not in temp_data: await query.edit_message_text("❌ Ошибка."); return
    data = temp_data[user_id]; action = query.data
    
    if action == "pbcast_cancel": clear_broadcast_state(user_id); await query.edit_message_text("❌ Отменено."); return
    
    if action == "pbcast_send_now":
        await query.edit_message_text("⏳..."); btype = data['type']
        if btype == "all": users = get_all_customers(); ids = [u[0] for u in users]
        elif btype == "premium": ids = get_all_premium_users()
        elif btype == "basic": ids = get_all_basic_users()
        cnt = await send_to_users(context.bot, ids, data['text'])
        clear_broadcast_state(user_id); await query.message.reply_text(f"✅ Отправлено: {cnt}")
        return
    
    if action == "pbcast_send_now_with_button":
        await query.edit_message_text("⏳..."); btype = data['type']
        if btype == "all": users = get_all_customers(); ids = [u[0] for u in users]
        elif btype == "premium": ids = get_all_premium_users()
        elif btype == "basic": ids = get_all_basic_users()
        cnt = await send_to_users(context.bot, ids, data['text'], data.get('button_text',''), data.get('button_url',''))
        clear_broadcast_state(user_id); await query.message.reply_text(f"✅ Отправлено: {cnt}")
        return
    
    if action == "pbcast_add_button": data['step'] = 'awaiting_button_text'; await query.edit_message_text("🔘 Текст кнопки:"); return
    if action in ["pbcast_schedule", "pbcast_schedule_with_button"]: data['step'] = 'awaiting_schedule_date'; await query.edit_message_text("📅 Дата: 2026-06-15 20:30"); return

async def handle_broadcast_input(update, context):
    user_id = update.effective_user.id
    if user_id not in temp_data: return False
    data = temp_data[user_id]; text = update.message.text.strip()
    if data.get('step') == 'awaiting_button_text':
        data['button_text'] = text; data['step'] = 'awaiting_button_url'
        await update.message.reply_text(f"✅ Кнопка: {text}\n🔗 Ссылка:"); return True
    if data.get('step') == 'awaiting_button_url':
        if not text.startswith("https://"): await update.message.reply_text("❌ https://"); return True
        data['button_url'] = text; data['step'] = 'choosing_action_with_button'
        keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton("📤 С кнопкой", callback_data="pbcast_send_now_with_button")],
            [InlineKeyboardButton("📅 План", callback_data="pbcast_schedule_with_button")],
            [InlineKeyboardButton("❌ Отмена", callback_data="pbcast_cancel")]
        ])
        await update.message.reply_text(f"✅ Готово!\n📝 {data['button_text']}\n🔗 {data['button_url']}", reply_markup=keyboard); return True
    if data.get('step') == 'awaiting_schedule_date':
        try:
            send_time = datetime.strptime(text, "%Y-%m-%d %H:%M")
            if send_time < datetime.now(): await update.message.reply_text("❌ Будущее!"); return True
        except: await update.message.reply_text("❌ Формат: 2026-06-15 20:30"); return True
        has = 1 if data.get('button_url') else 0
        add_scheduled_broadcast(data['type'], data['text'], has, data.get('button_text',''), data.get('button_url',''), text)
        await update.message.reply_text(f"✅ {text}"); clear_broadcast_state(user_id); return True
    return False

# ========== КОМАНДЫ ==========
async def start(update, context):
    user_id = update.effective_user.id
    user = update.effective_user
    
    if not has_seen_welcome(user_id):
        add_customer(user_id, user.username or '', user.first_name or '', 'visitor', 0, 'seen_welcome')
    
    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("👑 Premium — 1500₽", callback_data="buy_premium")],
        [InlineKeyboardButton("🎫 Базовый — 500₽", callback_data="buy_basic")],
        [InlineKeyboardButton("🆓 Бесплатно — 3 дня", callback_data="free_access")],
        [InlineKeyboardButton("❓ Помощь", callback_data="help")]
    ])
    
    await update.message.reply_text("💎 CHOICE | 30 DAYS\nАвторская программа.\nВыбери тариф:", reply_markup=keyboard)
    await update.message.reply_text("Выбери действие:", reply_markup=get_reply_keyboard())

async def premium_handler(update, context):
    query = update.callback_query; await query.answer()
    user_id = query.from_user.id
    await query.message.reply_text("⏳ Создаю платёж...")
    payment_url = await create_platega_payment(user_id, 1500, "premium")
    
    if payment_url:
        keyboard = InlineKeyboardMarkup([[InlineKeyboardButton("💳 Оплатить 1500₽", url=payment_url)]])
        await query.message.reply_text("👑 <b>Premium — всё включено</b>\n\nСтоимость: <b>1500₽</b>\n\nПосле оплаты доступ придёт автоматически.", parse_mode="HTML", reply_markup=keyboard)
    else:
        await query.message.reply_text("❌ Ошибка. Попробуй позже или напиши @Piholaa")

async def basic_handler(update, context):
    query = update.callback_query; await query.answer()
    user_id = query.from_user.id
    await query.message.reply_text("⏳ Создаю платёж...")
    payment_url = await create_platega_payment(user_id, 500, "basic")
    
    if payment_url:
        keyboard = InlineKeyboardMarkup([[InlineKeyboardButton("💳 Оплатить 500₽", url=payment_url)]])
        await query.message.reply_text("🎫 <b>Базовый — трекер + группа</b>\n\nСтоимость: <b>500₽</b>\n\nПосле оплаты доступ придёт автоматически.", parse_mode="HTML", reply_markup=keyboard)
    else:
        await query.message.reply_text("❌ Ошибка. Попробуй позже или напиши @Piholaa")

async def free_handler(update, context):
    query = update.callback_query; await query.answer()
    keyboard = InlineKeyboardMarkup([[InlineKeyboardButton("🆓 Перейти к боту", url=FREE_BOT_LINK)]])
    await query.message.reply_text("🆓 <b>Бесплатный доступ — 3 дня</b>\n\nПереходи по ссылке:", parse_mode="HTML", reply_markup=keyboard)

async def help_handler(update, context):
    query = update.callback_query; await query.answer()
    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("📞 Поддержка", url="https://t.me/Piholaa")],
        [InlineKeyboardButton("📋 Политика", url=PRIVACY_POLICY)],
        [InlineKeyboardButton("📄 Соглашение", url=TERMS_OF_USE)],
        [InlineKeyboardButton("◀ Назад", callback_data="back_to_start")]
    ])
    await query.message.reply_text("❓ <b>Помощь</b>\n\n📞 Поддержка: @Piholaa", parse_mode="HTML", reply_markup=keyboard)

async def back_to_start_handler(update, context):
    query = update.callback_query; await query.answer()
    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("👑 Premium — 1500₽", callback_data="buy_premium")],
        [InlineKeyboardButton("🎫 Базовый — 500₽", callback_data="buy_basic")],
        [InlineKeyboardButton("🆓 Бесплатно — 3 дня", callback_data="free_access")],
        [InlineKeyboardButton("❓ Помощь", callback_data="help")]
    ])
    await query.message.reply_text("💎 CHOICE | 30 DAYS\nВыбери тариф:", reply_markup=keyboard)

# ========== АДМИН-КОМАНДЫ ==========
async def stats_command(update, context):
    if update.effective_user.id != ADMIN_ID: return
    customers = get_all_customers()
    total = len(customers)
    premium = len(get_all_premium_users())
    basic = len(get_all_basic_users())
    pending = len([c for c in customers if c[6] == 'pending'])
    activated = len([c for c in customers if c[6] == 'activated'])
    revenue = sum(c[5] for c in customers if c[6] == 'activated')
    
    await update.message.reply_text(
        f"📊 <b>Статистика</b>\n\n"
        f"👥 Всего: {total}\n⏳ Ожидают: {pending}\n✅ Активировано: {activated}\n"
        f"👑 Premium: {premium}\n🎫 Базовый: {basic}\n💰 Выручка: {revenue}₽",
        parse_mode="HTML"
    )

async def customers_command(update, context):
    if update.effective_user.id != ADMIN_ID: return
    customers = get_all_customers()
    if not customers: await update.message.reply_text("Нет клиентов."); return
    text = "📊 <b>Клиенты:</b>\n\n"
    for c in customers[:30]:
        text += f"• <code>{c[0]}</code> | {c[4]} | {c[5]}₽ | {c[6]}\n"
    await update.message.reply_text(text, parse_mode="HTML")

async def activate_premium_command(update, context):
    if update.effective_user.id != ADMIN_ID: return
    try:
        user_id = int(context.args[0])
        conn = sqlite3.connect(DB_PATH); c = conn.cursor()
        c.execute('''INSERT OR REPLACE INTO premium_users (user_id, activated_at, plan) VALUES (?, ?, 'premium')''',
                  (user_id, datetime.now().isoformat()))
        c.execute('''UPDATE customers SET status = 'activated', plan = 'premium', amount = 1500 WHERE user_id = ?''', (user_id,))
        conn.commit(); conn.close()
        
        context.user_data['activating_user_id'] = user_id
        context.user_data['activating_plan'] = 'premium'
        
        await update.message.reply_text(
            f"✅ Пользователь {user_id} в базе Premium.\n"
            "Отправь ссылки на группы или 'пропустить'."
        )
    except: await update.message.reply_text("❌ /activate_premium <user_id>")

async def activate_basic_command(update, context):
    if update.effective_user.id != ADMIN_ID: return
    try:
        user_id = int(context.args[0])
        conn = sqlite3.connect(DB_PATH); c = conn.cursor()
        c.execute('''INSERT OR REPLACE INTO basic_users (user_id, activated_at, plan) VALUES (?, ?, 'basic')''',
                  (user_id, datetime.now().isoformat()))
        c.execute('''UPDATE customers SET status = 'activated', plan = 'basic', amount = 500 WHERE user_id = ?''', (user_id,))
        conn.commit(); conn.close()
        
        context.user_data['activating_user_id'] = user_id
        context.user_data['activating_plan'] = 'basic'
        
        await update.message.reply_text(
            f"✅ Пользователь {user_id} в базе Basic.\n"
            "Отправь ссылку на группу или 'пропустить'."
        )
    except: await update.message.reply_text("❌ /activate_basic <user_id>")

# ========== ОБРАБОТЧИК ТЕКСТА ==========
async def handle_text(update, context):
    user_id = update.effective_user.id; text = update.message.text.strip()
    if text.startswith('/'): return
    
    if user_id in temp_data:
        if await handle_broadcast_input(update, context): return
        if await handle_broadcast_text(update, context): return
    
    if 'activating_user_id' in context.user_data:
        target_user_id = context.user_data['activating_user_id']
        plan = context.user_data.get('activating_plan', 'premium')
        
        if text.lower() == 'пропустить':
            msg = f"👑 <b>Premium активирован!</b>\n\n🔗 Трекер: {TRACKER_LINK}" if plan == 'premium' else f"🎫 <b>Базовый активирован!</b>\n\n🔗 Трекер: {TRACKER_LINK}"
            try: await context.bot.send_message(chat_id=target_user_id, text=msg, parse_mode="HTML")
            except: pass
            await update.message.reply_text("✅ Отправлено")
            del context.user_data['activating_user_id']
            del context.user_data['activating_plan']
            return
        
        links = [l.strip() for l in text.split('\n') if l.strip().startswith('https://')]
        msg = f"👑 <b>Premium активирован!</b>\n\n" if plan == 'premium' else f"🎫 <b>Базовый активирован!</b>\n\n"
        if links: msg += f"🔗 Группа: {links[0]}\n"
        msg += f"\n🔗 Трекер: {TRACKER_LINK}"
        
        try:
            await context.bot.send_message(chat_id=target_user_id, text=msg, parse_mode="HTML")
            await update.message.reply_text(f"✅ Отправлено пользователю {target_user_id}")
        except Exception as e:
            await update.message.reply_text(f"❌ Ошибка: {e}")
        
        del context.user_data['activating_user_id']
        del context.user_data['activating_plan']
        return
    
    if text == "🏠 Главная":
        keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton("👑 Premium — 1500₽", callback_data="buy_premium")],
            [InlineKeyboardButton("🎫 Базовый — 500₽", callback_data="buy_basic")],
            [InlineKeyboardButton("🆓 Бесплатно — 3 дня", callback_data="free_access")],
            [InlineKeyboardButton("❓ Помощь", callback_data="help")]
        ])
        await update.message.reply_text("💎 CHOICE | 30 DAYS\nВыбери тариф:", reply_markup=keyboard)
    elif text == "👑 Premium":
        await update.message.reply_text("⏳ Создаю платёж...")
        payment_url = await create_platega_payment(user_id, 1500, "premium")
        if payment_url:
            keyboard = InlineKeyboardMarkup([[InlineKeyboardButton("💳 Оплатить 1500₽", url=payment_url)]])
            await update.message.reply_text("👑 <b>Premium — 1500₽</b>", parse_mode="HTML", reply_markup=keyboard)
        else:
            await update.message.reply_text("❌ Ошибка.")
    elif text == "🎫 Базовый":
        await update.message.reply_text("⏳ Создаю платёж...")
        payment_url = await create_platega_payment(user_id, 500, "basic")
        if payment_url:
            keyboard = InlineKeyboardMarkup([[InlineKeyboardButton("💳 Оплатить 500₽", url=payment_url)]])
            await update.message.reply_text("🎫 <b>Базовый — 500₽</b>", parse_mode="HTML", reply_markup=keyboard)
        else:
            await update.message.reply_text("❌ Ошибка.")
    elif text == "🆓 Бесплатно":
        keyboard = InlineKeyboardMarkup([[InlineKeyboardButton("🆓 Перейти", url=FREE_BOT_LINK)]])
        await update.message.reply_text("🆓 <b>Бесплатный доступ — 3 дня</b>", parse_mode="HTML", reply_markup=keyboard)
    elif text == "❓ Помощь":
        await update.message.reply_text(f"❓ <b>Помощь</b>\n\n📞 Поддержка: @Piholaa\n📋 Политика: {PRIVACY_POLICY}\n📄 Соглашение: {TERMS_OF_USE}", parse_mode="HTML", reply_markup=get_reply_keyboard())
    else:
        await update.message.reply_text("Используй кнопки", reply_markup=get_reply_keyboard())

# ========== ЗАПУСК ==========
def main():
    init_db()
    app = Application.builder().token(BOT_TOKEN).build()
    
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("stats", stats_command))
    app.add_handler(CommandHandler("customers", customers_command))
    app.add_handler(CommandHandler("activate_premium", activate_premium_command))
    app.add_handler(CommandHandler("activate_basic", activate_basic_command))
    app.add_handler(CommandHandler("broadcast_all", all_broadcast))
    app.add_handler(CommandHandler("broadcast_premium", premium_broadcast))
    app.add_handler(CommandHandler("broadcast_basic", basic_broadcast))
    app.add_handler(CommandHandler("cancel", cancel_broadcast))
    
    app.add_handler(CallbackQueryHandler(premium_handler, pattern="^buy_premium$"))
    app.add_handler(CallbackQueryHandler(basic_handler, pattern="^buy_basic$"))
    app.add_handler(CallbackQueryHandler(free_handler, pattern="^free_access$"))
    app.add_handler(CallbackQueryHandler(help_handler, pattern="^help$"))
    app.add_handler(CallbackQueryHandler(back_to_start_handler, pattern="^back_to_start$"))
    app.add_handler(CallbackQueryHandler(handle_broadcast_callback, pattern="^pbcast_"))
    
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))
    
    async def setup():
        commands = [BotCommand("start", "🏠 Главное меню")]
        await app.bot.set_my_commands(commands)
        
        from telegram import BotCommandScopeChat
        admin_commands = commands + [
            BotCommand("stats", "📊 Статистика"),
            BotCommand("customers", "👥 Клиенты"),
            BotCommand("activate_premium", "👑 Активировать Premium"),
            BotCommand("activate_basic", "🎫 Активировать Базовый"),
            BotCommand("broadcast_all", "📨 Рассылка всем"),
            BotCommand("broadcast_premium", "👑 Рассылка Premium"),
            BotCommand("broadcast_basic", "🎫 Рассылка Базовым"),
            BotCommand("cancel", "❌ Отменить рассылку"),
        ]
        await app.bot.set_my_commands(admin_commands, scope=BotCommandScopeChat(chat_id=ADMIN_ID))
    
    loop = asyncio.new_event_loop()
    loop.run_until_complete(setup())
    loop.close()
    
    logging.info("Бот CHOICE запущен на Render")
    app.run_polling()

if __name__ == "__main__":
    main()
