#!/usr/bin/env python3
"""
RECEPTRA バックエンド - FastAPI
電話対応・リマインド機能実装
"""

from fastapi import FastAPI, HTTPException, Request, BackgroundTasks
from fastapi.responses import PlainTextResponse
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from datetime import datetime, timedelta
import json
import sqlite3
import uuid
import os
from typing import Optional, List, Dict
from apscheduler.schedulers.background import BackgroundScheduler
from twilio.rest import Client
from twilio.twiml.voice_response import VoiceResponse
import logging

# ========== 設定 ==========
API_HOST = os.getenv("API_HOST", "http://localhost:8000")
DATABASE_PATH = os.getenv("DATABASE_PATH", "receptra.db")
TWILIO_ACCOUNT_SID = os.getenv("TWILIO_ACCOUNT_SID", "")
TWILIO_AUTH_TOKEN = os.getenv("TWILIO_AUTH_TOKEN", "")
TWILIO_PHONE_NUMBER = os.getenv("TWILIO_PHONE_NUMBER", "+81312345678")

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = FastAPI(title="RECEPTRA API", version="1.0.0")

# CORS設定
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # 本番環境では制限すること
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Twilio クライアント
if TWILIO_ACCOUNT_SID:
    twilio_client = Client(TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN)
else:
    twilio_client = None

# ========== データモデル ==========

class Customer(BaseModel):
    phone_number: str
    name: Optional[str] = None
    email: Optional[str] = None

class Reservation(BaseModel):
    phone_number: str
    shop_id: str
    shop_name: str
    reservation_date: str  # YYYY-MM-DD
    reservation_time: str  # HH:MM
    party_size: int
    special_requests: Optional[str] = None
    source: str = "phone"  # 'web', 'phone', 'app'

class ReminderSetting(BaseModel):
    shop_id: str
    shop_name: str
    enabled: bool = True
    reminder_time: int = 24  # hours
    custom_questions: Optional[List[Dict]] = None
    notification_method: str = "both"  # 'phone', 'email', 'both'
    owner_phone: Optional[str] = None
    owner_email: Optional[str] = None

class ReminderResponse(BaseModel):
    reservation_id: str
    responses: Dict
    call_status: str

# ========== データベース初期化 ==========

def init_db():
    """データベーステーブル作成"""
    conn = sqlite3.connect(DATABASE_PATH)
    cursor = conn.cursor()

    # テーブル1: customers
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS customers (
            id TEXT PRIMARY KEY,
            phone_number TEXT UNIQUE NOT NULL,
            name TEXT,
            email TEXT,
            is_repeat BOOLEAN DEFAULT 0,
            first_contact_date DATETIME,
            last_contact_date DATETIME,
            notes TEXT,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP
        )
    """)

    # テーブル2: reservations
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS reservations (
            id TEXT PRIMARY KEY,
            customer_id TEXT,
            shop_id TEXT NOT NULL,
            shop_name TEXT,
            reservation_date DATE NOT NULL,
            reservation_time TIME,
            party_size INT,
            phone_number TEXT NOT NULL,
            special_requests TEXT,
            created_at DATETIME,
            source TEXT,
            status TEXT DEFAULT 'confirmed',
            FOREIGN KEY (customer_id) REFERENCES customers(id)
        )
    """)

    # テーブル3: reminder_settings
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS reminder_settings (
            id TEXT PRIMARY KEY,
            shop_id TEXT UNIQUE NOT NULL,
            shop_name TEXT,
            enabled BOOLEAN DEFAULT 1,
            reminder_time INT DEFAULT 24,
            custom_questions TEXT,
            notification_method TEXT DEFAULT 'both',
            owner_phone TEXT,
            owner_email TEXT,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP
        )
    """)

    # テーブル4: reminder_responses
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS reminder_responses (
            id TEXT PRIMARY KEY,
            reservation_id TEXT NOT NULL,
            reminder_call_date DATETIME,
            call_duration INT,
            responses TEXT,
            call_status TEXT,
            FOREIGN KEY (reservation_id) REFERENCES reservations(id)
        )
    """)

    # インデックス作成
    cursor.execute("""
        CREATE INDEX IF NOT EXISTS idx_reservations_date
        ON reservations(reservation_date, shop_id)
    """)
    cursor.execute("""
        CREATE INDEX IF NOT EXISTS idx_customers_phone
        ON customers(phone_number)
    """)

    conn.commit()
    conn.close()
    logger.info("Database initialized")

init_db()

# ========== 顧客管理API ==========

@app.post("/api/customers")
def create_or_update_customer(customer: Customer):
    """顧客登録・更新（電話番号ベース）"""
    conn = sqlite3.connect(DATABASE_PATH)
    cursor = conn.cursor()

    customer_id = f"cust_{uuid.uuid4().hex[:8]}"
    now = datetime.now().isoformat()

    try:
        # 既存顧客確認
        cursor.execute("SELECT id FROM customers WHERE phone_number = ?", (customer.phone_number,))
        existing = cursor.fetchone()

        if existing:
            customer_id = existing[0]
            cursor.execute("""
                UPDATE customers
                SET name = ?, email = ?, last_contact_date = ?, is_repeat = 1
                WHERE id = ?
            """, (customer.name, customer.email, now, customer_id))
            logger.info(f"Updated customer: {customer_id}")
        else:
            cursor.execute("""
                INSERT INTO customers
                (id, phone_number, name, email, first_contact_date, last_contact_date)
                VALUES (?, ?, ?, ?, ?, ?)
            """, (customer_id, customer.phone_number, customer.name, customer.email, now, now))
            logger.info(f"Created customer: {customer_id}")

        conn.commit()
        return {"id": customer_id, "phone_number": customer.phone_number, "is_new": not existing}

    except sqlite3.IntegrityError:
        conn.rollback()
        raise HTTPException(status_code=400, detail="Phone number already registered")
    finally:
        conn.close()

@app.get("/api/customers/{phone_number}")
def get_customer(phone_number: str):
    """顧客情報取得"""
    conn = sqlite3.connect(DATABASE_PATH)
    cursor = conn.cursor()

    cursor.execute("""
        SELECT id, phone_number, name, email, is_repeat, last_contact_date
        FROM customers WHERE phone_number = ?
    """, (phone_number,))

    row = cursor.fetchone()
    conn.close()

    if not row:
        raise HTTPException(status_code=404, detail="Customer not found")

    return {
        "id": row[0],
        "phone_number": row[1],
        "name": row[2],
        "email": row[3],
        "is_repeat": bool(row[4]),
        "last_contact_date": row[5]
    }

# ========== 予約管理API ==========

@app.post("/api/reservations")
async def create_reservation(reservation: Reservation, background_tasks: BackgroundTasks):
    """予約作成"""
    conn = sqlite3.connect(DATABASE_PATH)
    cursor = conn.cursor()

    reservation_id = f"res_{uuid.uuid4().hex[:8]}"
    now = datetime.now().isoformat()

    # 顧客登録
    try:
        cursor.execute("SELECT id FROM customers WHERE phone_number = ?",
                      (reservation.phone_number,))
        customer_row = cursor.fetchone()
        customer_id = customer_row[0] if customer_row else None
    except Exception as e:
        logger.error(f"Customer lookup failed: {e}")
        customer_id = None

    # 予約を保存
    try:
        cursor.execute("""
            INSERT INTO reservations
            (id, customer_id, shop_id, shop_name, reservation_date,
             reservation_time, party_size, phone_number, special_requests,
             created_at, source, status)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            reservation_id, customer_id, reservation.shop_id,
            reservation.shop_name,
            reservation.reservation_date, reservation.reservation_time,
            reservation.party_size, reservation.phone_number,
            reservation.special_requests, now, reservation.source, 'confirmed'
        ))
        conn.commit()

        # オーナー通知をバックグラウンドタスクで実行
        background_tasks.add_task(notify_owner_new_reservation,
                                 reservation_id, reservation.shop_name,
                                 reservation.party_size,
                                 reservation.reservation_date,
                                 reservation.reservation_time)

        logger.info(f"Created reservation: {reservation_id}")
        return {"reservation_id": reservation_id, "status": "confirmed"}

    except Exception as e:
        conn.rollback()
        logger.error(f"Reservation creation failed: {e}")
        raise HTTPException(status_code=400, detail="Failed to create reservation")
    finally:
        conn.close()

@app.get("/api/shops/{shop_id}/availability")
def get_shop_availability(shop_id: str, date: str):
    """空き状況確認（簡易版）"""
    # 実装例：固定タイムスロット
    available_times = ["17:00", "17:30", "18:00", "18:30", "19:00", "19:30", "20:00", "20:30"]

    return {
        "shop_id": shop_id,
        "date": date,
        "available_slots": [{"time": t, "capacity": 4} for t in available_times]
    }

@app.get("/api/reservations/{phone_number}")
def get_reservations_by_phone(phone_number: str):
    """電話番号による予約検索"""
    conn = sqlite3.connect(DATABASE_PATH)
    cursor = conn.cursor()

    cursor.execute("""
        SELECT id, shop_name, reservation_date, reservation_time, party_size, status
        FROM reservations WHERE phone_number = ? ORDER BY reservation_date DESC
    """, (phone_number,))

    rows = cursor.fetchall()
    conn.close()

    return {
        "phone_number": phone_number,
        "reservations": [
            {
                "id": row[0],
                "shop_name": row[1],
                "date": row[2],
                "time": row[3],
                "party_size": row[4],
                "status": row[5]
            }
            for row in rows
        ]
    }

# ========== リマインド設定API ==========

@app.post("/api/reminder-settings")
def create_reminder_setting(setting: ReminderSetting):
    """リマインド設定保存"""
    conn = sqlite3.connect(DATABASE_PATH)
    cursor = conn.cursor()

    setting_id = f"rem_{uuid.uuid4().hex[:8]}"

    try:
        cursor.execute("""
            INSERT INTO reminder_settings
            (id, shop_id, shop_name, enabled, reminder_time, custom_questions,
             notification_method, owner_phone, owner_email)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            setting_id, setting.shop_id, setting.shop_name, setting.enabled,
            setting.reminder_time, json.dumps(setting.custom_questions or []),
            setting.notification_method, setting.owner_phone, setting.owner_email
        ))
        conn.commit()
        logger.info(f"Created reminder setting: {setting_id}")
        return {"id": setting_id, "shop_id": setting.shop_id}
    except Exception as e:
        conn.rollback()
        logger.error(f"Failed to create reminder setting: {e}")
        raise HTTPException(status_code=400, detail="Failed to create reminder setting")
    finally:
        conn.close()

@app.get("/api/reminder-settings/{shop_id}")
def get_reminder_setting(shop_id: str):
    """リマインド設定取得"""
    conn = sqlite3.connect(DATABASE_PATH)
    cursor = conn.cursor()

    cursor.execute("""
        SELECT id, enabled, reminder_time, custom_questions, notification_method,
               owner_phone, owner_email
        FROM reminder_settings WHERE shop_id = ?
    """, (shop_id,))

    row = cursor.fetchone()
    conn.close()

    if not row:
        raise HTTPException(status_code=404, detail="Reminder setting not found")

    return {
        "id": row[0],
        "enabled": bool(row[1]),
        "reminder_time": row[2],
        "custom_questions": json.loads(row[3]),
        "notification_method": row[4],
        "owner_phone": row[5],
        "owner_email": row[6]
    }

# ========== 電話制御API ==========

@app.post("/api/calls/incoming")
async def handle_incoming_call(request: Request):
    """着信受付 - IVR ウェルカムメッセージ"""
    response = VoiceResponse()
    response.say("いらっしゃいませ。お電話ありがとうございます。", language="ja-JP")
    response.gather(
        num_digits=1,
        action=f"{API_HOST}/api/ivr/main_menu",
        method="POST"
    ).say("ご予約はメニューの1を、ご予約の確認は2をお押しください。", language="ja-JP")

    return PlainTextResponse(str(response), media_type="application/xml")

@app.post("/api/ivr/main_menu")
async def handle_main_menu(request: Request):
    """IVR メインメニュー処理"""
    form_data = await request.form()
    pressed_digit = form_data.get("Digits", "0")
    caller_number = form_data.get("Caller", "Unknown")

    response = VoiceResponse()

    if pressed_digit == "1":
        # 新規予約フロー
        response.redirect(f"{API_HOST}/api/ivr/new_reservation?caller={caller_number}")
    elif pressed_digit == "2":
        # 予約確認フロー
        response.redirect(f"{API_HOST}/api/ivr/check_reservation?caller={caller_number}")
    else:
        response.say("入力が認識されません。もう一度お試しください。", language="ja-JP")
        response.redirect(f"{API_HOST}/api/calls/incoming")

    return PlainTextResponse(str(response), media_type="application/xml")

@app.get("/api/ivr/new_reservation")
async def new_reservation_flow(caller: str):
    """新規予約フロー"""
    response = VoiceResponse()
    response.say("新規ご予約ですね。かしこまりました。", language="ja-JP")
    response.say("どの日付でのご来店希望ですか？", language="ja-JP")
    response.gather(
        num_digits=8,
        action=f"{API_HOST}/api/ivr/reservation_date",
        method="POST"
    ).say("年月日を8桁でお入力ください。例：20250215", language="ja-JP")

    return PlainTextResponse(str(response), media_type="application/xml")

@app.get("/api/ivr/check_reservation")
async def check_reservation_flow(caller: str):
    """予約確認フロー"""
    response = VoiceResponse()
    response.say("ご予約の確認ですね。", language="ja-JP")

    # データベースから予約を取得
    conn = sqlite3.connect(DATABASE_PATH)
    cursor = conn.cursor()
    cursor.execute("""
        SELECT shop_name, reservation_date, reservation_time, party_size
        FROM reservations
        WHERE phone_number = ? AND status = 'confirmed'
        ORDER BY reservation_date DESC LIMIT 1
    """, (caller,))

    row = cursor.fetchone()
    conn.close()

    if row:
        shop_name, date, time, party_size = row
        response.say(f"{shop_name}のご予約が確認されました。", language="ja-JP")
        response.say(f"{date}の{time}、{party_size}名様でのご予約ですね。", language="ja-JP")
        response.say("ご来店をお待ちしております。", language="ja-JP")
    else:
        response.say("予約が見つかりません。", language="ja-JP")

    response.hangup()
    return PlainTextResponse(str(response), media_type="application/xml")

@app.post("/api/calls/remind/{reservation_id}")
async def send_reminder_call(reservation_id: str):
    """リマインドコール発信"""
    conn = sqlite3.connect(DATABASE_PATH)
    cursor = conn.cursor()

    cursor.execute("""
        SELECT phone_number, shop_name, reservation_date, reservation_time, party_size
        FROM reservations WHERE id = ?
    """, (reservation_id,))

    row = cursor.fetchone()
    conn.close()

    if not row:
        raise HTTPException(status_code=404, detail="Reservation not found")

    phone_number, shop_name, res_date, res_time, party_size = row

    if not twilio_client:
        return {"status": "skipped", "reason": "Twilio not configured"}

    try:
        call = twilio_client.calls.create(
            to=phone_number,
            from_=TWILIO_PHONE_NUMBER,
            url=f"{API_HOST}/api/ivr/reminder?reservation_id={reservation_id}",
            method="GET"
        )
        logger.info(f"Reminder call initiated: {call.sid}")
        return {"status": "sent", "call_sid": call.sid}
    except Exception as e:
        logger.error(f"Failed to send reminder call: {e}")
        raise HTTPException(status_code=500, detail="Failed to send reminder call")

@app.get("/api/ivr/reminder")
async def reminder_ivr(reservation_id: str):
    """リマインド IVR"""
    conn = sqlite3.connect(DATABASE_PATH)
    cursor = conn.cursor()

    cursor.execute("""
        SELECT shop_name, reservation_date, reservation_time, party_size
        FROM reservations WHERE id = ?
    """, (reservation_id,))

    row = cursor.fetchone()
    conn.close()

    if not row:
        response = VoiceResponse()
        response.say("申し訳ございません。予約情報が見つかりません。", language="ja-JP")
        return PlainTextResponse(str(response), media_type="application/xml")

    shop_name, res_date, res_time, party_size = row

    response = VoiceResponse()
    response.say("いつもご利用ありがとうございます。", language="ja-JP")
    response.say(f"{shop_name}より、ご予約確認のお電話です。", language="ja-JP")
    response.say(f"{res_date}の{res_time}、{party_size}名様でのご予約ですね。", language="ja-JP")
    response.gather(
        num_digits=1,
        action=f"{API_HOST}/api/ivr/reminder_confirm?reservation_id={reservation_id}",
        method="POST"
    ).say("ご来店いただけますか？はいは1、いいえは2をお押しください。", language="ja-JP")

    return PlainTextResponse(str(response), media_type="application/xml")

@app.post("/api/ivr/reminder_confirm")
async def reminder_confirm(request: Request, reservation_id: str):
    """リマインド確認応答処理"""
    form_data = await request.form()
    pressed_digit = form_data.get("Digits", "0")

    conn = sqlite3.connect(DATABASE_PATH)
    cursor = conn.cursor()

    response = VoiceResponse()

    if pressed_digit == "1":
        response.say("かしこまりました。ご来店をお待ちしております。", language="ja-JP")
        # DB更新：リマインド確認済み
        cursor.execute("""
            INSERT OR REPLACE INTO reminder_responses
            (id, reservation_id, reminder_call_date, responses, call_status)
            VALUES (?, ?, ?, ?, ?)
        """, (f"remr_{uuid.uuid4().hex[:8]}", reservation_id,
              datetime.now().isoformat(), json.dumps({"confirmed": "yes"}), "completed"))
    else:
        response.say("キャンセルですね。承知いたしました。", language="ja-JP")
        # DB更新：ステータス変更
        cursor.execute("UPDATE reservations SET status = ? WHERE id = ?",
                      ("cancelled", reservation_id))

    conn.commit()
    conn.close()
    response.hangup()

    return PlainTextResponse(str(response), media_type="application/xml")

# ========== リマインダースケジューラー ==========

def schedule_reminders():
    """毎日チェック：リマインド対象の予約を発見して電話発信"""
    conn = sqlite3.connect(DATABASE_PATH)
    cursor = conn.cursor()

    # 24時間以内の予約を取得
    tomorrow = (datetime.now() + timedelta(hours=24)).date()

    cursor.execute("""
        SELECT id, phone_number, shop_id FROM reservations
        WHERE reservation_date = ? AND status = 'confirmed'
    """, (tomorrow,))

    reservations = cursor.fetchall()
    conn.close()

    if twilio_client:
        for res_id, phone, shop_id in reservations:
            try:
                call = twilio_client.calls.create(
                    to=phone,
                    from_=TWILIO_PHONE_NUMBER,
                    url=f"{API_HOST}/api/ivr/reminder?reservation_id={res_id}",
                    method="GET"
                )
                logger.info(f"Scheduled reminder call: {call.sid}")
            except Exception as e:
                logger.error(f"Failed to schedule reminder: {e}")

# スケジューラーセットアップ
scheduler = BackgroundScheduler()
scheduler.add_job(schedule_reminders, 'cron', hour=0, minute=0)
scheduler.start()

# ========== ヘルパー関数 ==========

def notify_owner_new_reservation(reservation_id: str, shop_name: str,
                                party_size: int, date: str, time: str):
    """オーナーに新規予約を通知"""
    message = f"""
新しい予約が入りました！

店舗: {shop_name}
日時: {date} {time}
人数: {party_size}名
予約ID: {reservation_id}

管理画面で詳細をご確認ください。
"""
    logger.info(f"Notification: {message}")
    # メール送信処理をここに実装可能

# ========== ヘルスチェック ==========

@app.get("/health")
def health_check():
    """ヘルスチェック"""
    return {
        "status": "ok",
        "timestamp": datetime.now().isoformat(),
        "twilio_configured": bool(twilio_client)
    }

@app.get("/api/status")
def api_status():
    """API ステータス"""
    return {
        "version": "1.0.0",
        "database": DATABASE_PATH,
        "twilio_account": TWILIO_ACCOUNT_SID[:20] + "..." if TWILIO_ACCOUNT_SID else "Not configured",
        "api_host": API_HOST
    }

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
