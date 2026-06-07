#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import logging
import sqlite3
import asyncio
import os
from datetime import datetime, timedelta

import pandas as pd
import jdatetime  
import pytz       
import requests  # برای دریافت نتایج آنلاین

from telegram import Update, ReplyKeyboardMarkup, KeyboardButton, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    MessageHandler,
    filters,
    ConversationHandler,
    CallbackQueryHandler,
    ContextTypes,
)

# --------------------- پیکربندی ربات ---------------------
TOKEN = "8962298908:AAEMM-w_MT4EuWK6GU5h_YtIQN2JZ5iW9h0"                 
ADMIN_IDS = [259379104]                  
CARD_NUMBER = "6037-9982-7616-1402"      
REGISTRATION_FEE = 500                   
DB_PATH = "worldcup.db"
MATCH_LOCK_HOURS = 1                     

# 🔁 آدرس فایل JSON نتایج در گیت‌هاب شما (نسخه Raw)
# آدرس زیر یک نمونه است، بعداً آدرس گیت‌هاب خود را جایگزین کنید
RESULTS_API_URL = "https://raw.githubusercontent.com/ahmad2570/worldcup-results/main/results.json"

# --------------------- دیتابیس ---------------------
def get_db():
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    conn = get_db()
    c = conn.cursor()
    c.execute('''CREATE TABLE IF NOT EXISTS users (
        user_id INTEGER PRIMARY KEY,
        phone TEXT,
        full_name TEXT,
        is_active INTEGER DEFAULT 0,
        payment_photo_id TEXT,
        payment_status TEXT DEFAULT 'pending',
        registered_at TEXT DEFAULT (datetime('now','localtime'))
    )''')
    c.execute('''CREATE TABLE IF NOT EXISTS matches (
        match_id INTEGER PRIMARY KEY AUTOINCREMENT,
        match_number INTEGER UNIQUE,
        home_team TEXT,
        away_team TEXT,
        match_datetime TEXT,
        stage TEXT,
        group_name TEXT,
        home_score INTEGER,
        away_score INTEGER
    )''')
    c.execute('''CREATE TABLE IF NOT EXISTS predictions (
        pred_id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER,
        match_id INTEGER,
        pred_home INTEGER,
        pred_away INTEGER,
        points INTEGER DEFAULT 0,
        updated_at TEXT DEFAULT (datetime('now','localtime')),
        UNIQUE(user_id, match_id),
        FOREIGN KEY(user_id) REFERENCES users(user_id),
        FOREIGN KEY(match_id) REFERENCES matches(match_id)
    )''')
    conn.commit()
    conn.close()

# --------------------- سیستم امتیازدهی ---------------------
def calculate_points(stage: str, pred_home: int, pred_away: int, real_home: int, real_away: int) -> int:
    exact = (pred_home == real_home and pred_away == real_away)
    pred_winner = 1 if pred_home > pred_away else (2 if pred_away > pred_home else 0)
    real_winner = 1 if real_home > real_away else (2 if real_away > real_home else 0)
    winner_ok = (pred_winner == real_winner and real_winner != 0)
    draw_ok = (real_winner == 0 and pred_winner == 0)

    stage_lower = stage.strip().lower()
    if 'group' in stage_lower:
        if exact: return 5
        if winner_ok or (draw_ok and pred_home == pred_away): return 3
        if (real_home - real_away) == (pred_home - pred_away): return 2
        return 0
    elif 'round of 16' in stage_lower or 'quarter' in stage_lower:
        if exact and winner_ok: return 7
        if winner_ok: return 4
        if draw_ok and exact: return 3
        return 0
    else:
        if exact and winner_ok: return 15
        if winner_ok: return 10
        return 0

def update_points_for_match(match_id: int):
    conn = get_db()
    c = conn.cursor()
    c.execute("SELECT * FROM matches WHERE match_id=? AND home_score IS NOT NULL AND away_score IS NOT NULL", (match_id,))
    match = c.fetchone()
    if not match:
        conn.close()
        return
    c.execute("SELECT * FROM predictions WHERE match_id=?", (match_id,))
    for p in c.fetchall():
        pts = calculate_points(match['stage'], p['pred_home'], p['pred_away'], match['home_score'], match['away_score'])
        c.execute("UPDATE predictions SET points=? WHERE pred_id=?", (pts, p['pred_id']))
    conn.commit()
    conn.close()

# --------------------- همگام‌سازی خودکار نتایج از اینترنت ---------------------
def auto_sync_results():
    try:
        response = requests.get(RESULTS_API_URL, timeout=10)
        if response.status_code == 200:
            results = response.json()
            conn = get_db()
            c = conn.cursor()
            for res in results:
                match_num = res.get('match_number')
                home_s = res.get('home_score')
                away_s = res.get('away_score')
                
                if match_num is not None and home_s is not None and away_s is not None:
                    c.execute("SELECT match_id, home_score, away_score FROM matches WHERE match_number=?", (match_num,))
                    match = c.fetchone()
                    if match and (match['home_score'] != home_s or match['away_score'] != away_s):
                        c.execute("UPDATE matches SET home_score=?, away_score=? WHERE match_number=?", (home_s, away_s, match_num))
                        conn.commit()
                        update_points_for_match(match['match_id'])
            conn.close()
            print("🔄 نتایج آنلاین با موفقیت همگام‌سازی شد.")
    except Exception as e:
        print(f"⚠️ خطا در دریافت نتایج آنلاین: {e}")

# --------------------- میان‌افزار وضعیت کاربر ---------------------
async def require_active(update: Update) -> bool:
    user_id = update.effective_user.id
    conn = get_db()
    c = conn.cursor()
    c.execute("SELECT is_active FROM users WHERE user_id=?", (user_id,))
    row = c.fetchone()
    conn.close()
    return row is not None and row['is_active'] == 1

# --------------------- دستورات کاربران ---------------------
PHONE, NAME = range(2)

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    user = update.effective_user
    conn = get_db()
    c = conn.cursor()
    c.execute("SELECT is_active FROM users WHERE user_id=?", (user.id,))
    row = c.fetchone()
    if row:
        if row['is_active']:
            await update.message.reply_text("✅ شما قبلاً ثبت‌نام شده‌اید. /games")
        else:
            await update.message.reply_text("⏳ ثبت‌نام شما ناقص است. لطفاً عکس رسید واریز را بفرستید.")
        conn.close()
        return ConversationHandler.END

    keyboard = [[KeyboardButton("📱 ارسال شماره", request_contact=True)]]
    markup = ReplyKeyboardMarkup(keyboard, one_time_keyboard=True, resize_keyboard=True)
    await update.message.reply_text("برای شروع ثبت‌نام، لطفاً شماره موبایل خود را ارسال کنید:", reply_markup=markup)
    return PHONE

async def get_phone(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    contact = update.message.contact
    if not contact or not contact.phone_number:
        await update.message.reply_text("لطفاً از دکمه ارسال شماره استفاده کنید.")
        return PHONE
    context.user_data['phone'] = contact.phone_number
    await update.message.reply_text("نام و نام خانوادگی خود را وارد کنید:")
    return NAME

async def get_name(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    full_name = update.message.text.strip()
    if len(full_name) < 3:
        await update.message.reply_text("نام حداقل ۳ کاراکتر باشد.")
        return NAME
    user_id = update.effective_user.id
    phone = context.user_data['phone']
    conn = get_db()
    c = conn.cursor()
    c.execute("INSERT OR REPLACE INTO users (user_id, phone, full_name) VALUES (?,?,?)", (user_id, phone, full_name))
    conn.commit()
    conn.close()
    await update.message.reply_text(
        f"📌 ثبت‌نام اولیه انجام شد.\n"
        f"برای تکمیل، مبلغ **{REGISTRATION_FEE:,} تومان** را به شماره کارت:\n"
        f"`{CARD_NUMBER}`\n"
        f"واریز کنید و سپس عکس رسید را همین‌جا بفرستید.",
        parse_mode='Markdown'
    )
    return ConversationHandler.END

async def handle_payment_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if not update.message.photo:
        await update.message.reply_text("لطفاً فقط عکس رسید را ارسال کنید.")
        return

    photo_file_id = update.message.photo[-1].file_id
    conn = get_db()
    c = conn.cursor()
    c.execute("UPDATE users SET payment_photo_id=?, payment_status='pending' WHERE user_id=?", (photo_file_id, user.id))
    conn.commit()
    conn.close()

    await update.message.reply_text("✅ رسید دریافت شد. منتظر تأیید ادمین باشید.")

    for admin_id in ADMIN_IDS:
        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton("✅ تأیید", callback_data=f"approve_{user.id}"),
             InlineKeyboardButton("❌ رد", callback_data=f"reject_{user.id}")]
        ])
        try:
            await context.bot.send_photo(admin_id, photo_file_id, caption=f"درخواست ثبت‌نام از {user.full_name}", reply_markup=kb)
        except:
            pass

async def payment_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data.split('_')
    action = data[0]
    user_id = int(data[1])

    conn = get_db()
    c = conn.cursor()
    if action == 'approve':
        c.execute("UPDATE users SET is_active=1, payment_status='approved' WHERE user_id=?", (user_id,))
        try:
            await context.bot.send_message(user_id, "🎉 ثبت‌نام شما تأیید شد! اکنون می‌توانید پیش‌بینی کنید. /games")
        except:
            pass
        await query.edit_message_caption(caption=query.message.caption + "\n\n✅ تأیید شد.")
    else:
        c.execute("UPDATE users SET payment_status='rejected' WHERE user_id=?", (user_id,))
        try:
            await context.bot.send_message(user_id, "⛔️ رسید شما رد شد. لطفاً دوباره تلاش کنید.")
        except:
            pass
        await query.edit_message_caption(caption=query.message.caption + "\n\n❌ رد شد.")
    conn.commit()
    conn.close()

async def list_games(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await require_active(update):
        await update.message.reply_text("⛔️ شما هنوز ثبت‌نام خود را تکمیل نکرده‌اید. /start")
        return

    auto_sync_results() # 🔄 همگام‌سازی خودکار قبل از نمایش بازی‌ها
    conn = get_db()
    c = conn.cursor()
    c.execute("SELECT * FROM matches WHERE match_datetime > datetime('now','localtime') ORDER BY match_datetime")
    upcoming = c.fetchall()
    if not upcoming:
        await update.message.reply_text("بازی جدیدی برای پیش‌بینی موجود نیست.")
        conn.close()
        return

    by_date = {}
    for m in upcoming:
        dt = datetime.strptime(m['match_datetime'], '%Y-%m-%d %H:%M')
        day = dt.strftime('%Y-%m-%d')
        by_date.setdefault(day, []).append(m)

    text = "📋 بازی‌های پیش رو:\n"
    keyboard = []
    for day, matches in by_date.items():
        text += f"\n📅 {day}\n"
        for m in matches:
            lock_time = datetime.strptime(m['match_datetime'], '%Y-%m-%d %H:%M') - timedelta(hours=MATCH_LOCK_HOURS)
            locked = datetime.now() > lock_time
            status = "🔒 قفل" if locked else "🔓 باز"
            text += f"#{m['match_number']} {m['home_team']} 🆚 {m['away_team']} | {m['match_datetime'][-5:]} {status}\n"
            if not locked:
                keyboard.append([InlineKeyboardButton(f"⚽ {m['home_team']} vs {m['away_team']}", callback_data=f"pred_{m['match_id']}")])

    reply_markup = InlineKeyboardMarkup(keyboard) if keyboard else None
    await update.message.reply_text(text, reply_markup=reply_markup)
    conn.close()

ASK_HOME, ASK_AWAY = range(2)

async def start_predict(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    if not await require_active(update):
        await query.edit_message_text("⛔️ فقط کاربران تأییدشده مجاز به پیش‌بینی هستند.")
        return ConversationHandler.END

    match_id = int(query.data.split('_')[1])
    context.user_data['predict_match_id'] = match_id
    await query.message.reply_text("گل تیم میزبان را وارد کنید:")
    return ASK_HOME

async def ask_home(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    try:
        val = int(update.message.text)
        if val < 0: raise ValueError
    except:
        await update.message.reply_text("لطفاً یک عدد صحیح وارد کنید.")
        return ASK_HOME
    context.user_data['pred_home'] = val
    await update.message.reply_text("گل تیم مهمان را وارد کنید:")
    return ASK_AWAY

async def ask_away(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    try:
        val = int(update.message.text)
        if val < 0: raise ValueError
    except:
        await update.message.reply_text("لطفاً یک عدد صحیح وارد کنید.")
        return ASK_AWAY

    user_id = update.effective_user.id
    match_id = context.user_data['predict_match_id']
    home = context.user_data['pred_home']
    away = val

    conn = get_db()
    c = conn.cursor()
    c.execute("SELECT match_datetime FROM matches WHERE match_id=?", (match_id,))
    m = c.fetchone()
    if not m or datetime.now() > datetime.strptime(m['match_datetime'], '%Y-%m-%d %H:%M') - timedelta(hours=MATCH_LOCK_HOURS):
        await update.message.reply_text("⌛️ مهلت پیش‌بینی این بازی به پایان رسیده است.")
        conn.close()
        return ConversationHandler.END

    c.execute("""INSERT INTO predictions (user_id, match_id, pred_home, pred_away) VALUES (?,?,?,?)
                 ON CONFLICT(user_id, match_id) DO UPDATE SET pred_home=?, pred_away=?""", (user_id, match_id, home, away, home, away))
    conn.commit()
    conn.close()
    await update.message.reply_text("✅ پیش‌بینی شما ثبت شد.")
    return ConversationHandler.END

async def profile(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await require_active(update):
        await update.message.reply_text("⛔️ ابتدا ثبت‌نام کنید. /start")
        return
    user_id = update.effective_user.id
    conn = get_db()
    c = conn.cursor()
    c.execute("""
        SELECT u.full_name, COALESCE(SUM(p.points),0) as total_points, COUNT(p.pred_id) as total_preds,
               SUM(CASE WHEN p.points > 0 THEN 1 ELSE 0 END) as correct
        FROM users u LEFT JOIN predictions p ON u.user_id = p.user_id WHERE u.user_id=? GROUP BY u.user_id
    """, (user_id,))
    row = c.fetchone()
    await update.message.reply_text(f"👤 کاربر: {row['full_name']}\n🏆 کل امتیازات: {row['total_points']}\n⚽ تعداد پیش‌بینی‌ها: {row['total_preds']}")
    conn.close()

async def leaderboard(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await require_active(update):
        await update.message.reply_text("⛔️ دسترسی محدود.")
        return
    
    auto_sync_results() # 🔄 همگام‌سازی خودکار قبل از محاسبه رتبه‌ها
    conn = get_db()
    c = conn.cursor()
    c.execute("""
        SELECT u.full_name, SUM(p.points) as total_points FROM users u
        LEFT JOIN predictions p ON u.user_id = p.user_id WHERE u.is_active=1
        GROUP BY u.user_id ORDER BY total_points DESC LIMIT 20
    """)
    rows = c.fetchall()
    text = "🏅 **جدول برترین‌ها**\n\n"
    for i, r in enumerate(rows, 1):
        text += f"{i}. {r['full_name']} - {r['total_points'] or 0} امتیاز\n"
    await update.message.reply_text(text, parse_mode='Markdown')
    conn.close()

# --------------------- بخش مدیریت ---------------------
async def admin_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id not in ADMIN_IDS: return
    await update.message.reply_text("🔧 مدیریت:\n/import - بارگذاری اکسل بازی‌ها\n/sync - همگام‌سازی دستی نتایج آنلاین")

async def force_sync(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id not in ADMIN_IDS: return
    auto_sync_results()
    await update.message.reply_text("🔄 درخواست همگام‌سازی نتایج ارسال شد.")

async def import_excel_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id not in ADMIN_IDS: return
    if not update.message.document:
        await update.message.reply_text("لطفاً فایل اکسل ارسال کنید.")
        return
    doc = update.message.document
    file = await context.bot.get_file(doc.file_id)
    file_path = "temp_import.xlsx"
    await file.download_to_drive(file_path)

    try:
        df = pd.read_excel(file_path, engine='openpyxl')
        conn = get_db()
        c = conn.cursor()
        for _, row in df.iterrows():
            c.execute("INSERT OR REPLACE INTO matches (match_number, home_team, away_team, match_datetime, stage, group_name) VALUES (?,?,?,?,?,?)",
                      (int(row['match_number']), row['home_team'], row['away_team'], str(row['match_datetime']), row['stage'], row.get('group_name', '')))
        conn.commit()
        conn.close()
        await update.message.reply_text("✅ بازی‌ها با موفقیت از فایل اکسل وارد شدند.")
    except Exception as e:
        await update.message.reply_text(f"❌ خطا: {e}")

# --------------------- اجرای برنامه ---------------------
def main():
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    init_db()

    # استفاده از ساختار استاندارد نسخه جدید تلگرام 🟢
    app = ApplicationBuilder().token(TOKEN).build()

    reg_conv = ConversationHandler(
        entry_points=[CommandHandler('start', start)],
        states={
            PHONE: [MessageHandler(filters.CONTACT, get_phone)],
            NAME: [MessageHandler(filters.TEXT & ~filters.COMMAND, get_name)]
        },
        fallbacks=[]
    )
    app.add_handler(reg_conv)

    pred_conv = ConversationHandler(
        entry_points=[CallbackQueryHandler(start_predict, pattern='^pred_')],
        states={
            ASK_HOME: [MessageHandler(filters.TEXT & ~filters.COMMAND, ask_home)],
            ASK_AWAY: [MessageHandler(filters.TEXT & ~filters.COMMAND, ask_away)]
        },
        fallbacks=[]
    )
    app.add_handler(pred_conv)

    app.add_handler(CommandHandler('games', list_games))
    app.add_handler(CommandHandler('profile', profile))
    app.add_handler(CommandHandler('leaderboard', leaderboard))
    app.add_handler(CommandHandler('admin', admin_menu))
    app.add_handler(CommandHandler('sync', force_sync))
    app.add_handler(CommandHandler('import', import_excel_handler))
    app.add_handler(MessageHandler(filters.PHOTO, handle_payment_photo))
    app.add_handler(CallbackQueryHandler(payment_callback, pattern='^(approve|reject)_'))

    print("✅ ربات بدون هیچ خطایی روی پایتون ۳.۱۳ با موفقیت اجرا شد...")
    app.run_polling()

if __name__ == '__main__':
    main()
