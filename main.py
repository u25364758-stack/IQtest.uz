"""Telegram IQ test bot and authenticated Mini App API."""
import asyncio
import hashlib
import hmac
import json
import logging
import re
import time
from urllib.parse import parse_qsl
from urllib.parse import urlsplit

from aiogram import Bot, Dispatcher
from aiogram.filters import CommandStart
from aiogram.types import Message, WebAppInfo
from aiogram.utils.keyboard import InlineKeyboardBuilder
from aiohttp import web

import database
from config import BOT_TOKEN, HOST, INIT_DATA_MAX_AGE, PORT, QUESTIONS_DIR, WEBAPP_DIR, WEBAPP_URL

logging.basicConfig(level=logging.INFO)
log = logging.getLogger(__name__)
dp = Dispatcher()


def validate_init_data(init_data: str, bot_token: str):
    """Validate Telegram WebApp initData per Telegram's HMAC-SHA-256 scheme."""
    if not init_data or len(init_data) > 8192:
        raise web.HTTPUnauthorized(text="Telegram initData is required")
    try:
        pairs = parse_qsl(init_data, keep_blank_values=True, strict_parsing=True)
        values = dict(pairs)
        if len(values) != len(pairs) or "hash" not in values or "user" not in values:
            raise ValueError("Malformed initData")
        received_hash = values.pop("hash")
        auth_date = int(values["auth_date"])
        now = int(time.time())
        if auth_date > now + 60 or now - auth_date > INIT_DATA_MAX_AGE:
            raise ValueError("Expired initData")
        check_string = "\n".join(f"{k}={v}" for k, v in sorted(values.items()))
        secret_key = hmac.new(b"WebAppData", bot_token.encode(), hashlib.sha256).digest()
        expected = hmac.new(secret_key, check_string.encode(), hashlib.sha256).hexdigest()
        if not hmac.compare_digest(expected, received_hash):
            raise ValueError("Invalid initData signature")
        user = json.loads(values["user"])
        user_id = int(user["id"])
        if user_id <= 0:
            raise ValueError("Invalid user")
        return user_id
    except (ValueError, TypeError, KeyError, json.JSONDecodeError) as exc:
        raise web.HTTPUnauthorized(text="Invalid or expired Telegram session") from exc


@web.middleware
async def security_headers(request, handler):
    response = await handler(request)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "SAMEORIGIN"
    response.headers["Referrer-Policy"] = "no-referrer"
    return response


@web.middleware
async def api_errors(request, handler):
    try:
        return await handler(request)
    except web.HTTPException:
        raise
    except (json.JSONDecodeError, UnicodeDecodeError):
        raise web.HTTPBadRequest(text="Invalid JSON")
    except Exception:
        log.exception("Unhandled request error")
        raise web.HTTPInternalServerError(text="Internal server error")


def telegram_user_id(request):
    if not BOT_TOKEN:
        raise web.HTTPServiceUnavailable(text="Bot is not configured")
    return validate_init_data(request.headers.get("X-Telegram-Init-Data", ""), BOT_TOKEN)


def load_questions():
    path = QUESTIONS_DIR / "iq.json"
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise web.HTTPServiceUnavailable(text="Question bank is unavailable") from exc
    if not isinstance(data, list) or len(data) != 1000:
        raise web.HTTPServiceUnavailable(text="Invalid question bank")
    ids = set()
    for q in data:
        if (not isinstance(q, dict) or isinstance(q.get("id"), bool)
                or not isinstance(q.get("id"), int) or q["id"] < 1 or q["id"] in ids
                or not isinstance(q.get("question"), str) or not q["question"].strip()
                or not isinstance(q.get("options"), dict) or set(q["options"]) != {"A", "B", "C", "D"}
                or any(not isinstance(value, str) or not value.strip() for value in q["options"].values())
                or q.get("answer") not in {"A", "B", "C", "D"}
                or isinstance(q.get("difficulty"), bool) or not isinstance(q.get("difficulty"), int)
                or q["difficulty"] < 1):
            raise web.HTTPServiceUnavailable(text="Question bank contains invalid entries")
        ids.add(q["id"])
    return data


def parse_json_object(raw):
    if len(raw) > 32768:
        raise web.HTTPRequestEntityTooLarge(max_size=32768, actual_size=len(raw))
    data = json.loads(raw)
    if not isinstance(data, dict):
        raise web.HTTPBadRequest(text="Expected a JSON object")
    return data


async def api_profile(request):
    uid = telegram_user_id(request)
    data = parse_json_object(await request.text())
    full_name = data.get("full_name")
    age = data.get("age")
    gender = data.get("gender")
    if not isinstance(full_name, str) or not 2 <= len(full_name.strip()) <= 120:
        raise web.HTTPBadRequest(text="Enter a valid full name")
    if isinstance(age, bool) or not isinstance(age, int) or not 5 <= age <= 120:
        raise web.HTTPBadRequest(text="Age must be between 5 and 120")
    if not isinstance(gender, str) or gender not in {"male", "female"}:
        raise web.HTTPBadRequest(text="Choose a valid gender")
    database.save_profile(uid, full_name.strip(), age, gender)
    return web.json_response({"success": True})


async def api_questions(request):
    uid = telegram_user_id(request)
    all_questions = load_questions()
    if len(all_questions) < 25:
        raise web.HTTPServiceUnavailable(text="At least 25 valid questions are required")
    reservation = database.reserve_questions(uid, all_questions)
    if reservation is None:
        raise web.HTTPConflict(text="Question cycle is incomplete; please contact support")
    attempt_id, started_at, selected = reservation
    return web.json_response({
        "attempt_id": attempt_id,
        "started_at": started_at,
        "questions": [{"id": q["id"], "question": q["question"], "options": q["options"],
                       "difficulty": q["difficulty"]} for q in selected]
    })


def calculate_iq(question_ids, answers, questions_by_id, duration):
    # An entertainment score weighted by difficulty, not a standardized IQ result.
    weights = [1.0 if questions_by_id[qid]["difficulty"] == 1 else 1.35
               for qid in question_ids]
    earned = sum(weight for qid, answer, weight in zip(question_ids, answers, weights)
                 if answer == questions_by_id[qid]["answer"])
    weighted_accuracy = earned / sum(weights)
    speed = 1.0 if duration <= 300 else 0.95 if duration <= 600 else 0.90 if duration <= 900 else 0.85
    return max(70, min(140, round(70 + weighted_accuracy * 70 * speed)))


async def api_finish(request):
    uid = telegram_user_id(request)
    data = parse_json_object(await request.text())
    attempt_id = data.get("attempt_id")
    answers = data.get("answers")
    if isinstance(attempt_id, bool) or not isinstance(attempt_id, int) or attempt_id < 1:
        raise web.HTTPBadRequest(text="Invalid test session")
    attempt = database.get_attempt(attempt_id, uid)
    if attempt is None:
        raise web.HTTPNotFound(text="Test session not found")
    if attempt["finished_at"] is not None:
        raise web.HTTPConflict(text="Test session has already been completed")
    question_ids = json.loads(attempt["question_ids"])
    if (not isinstance(answers, list) or len(answers) != 25
            or any(not isinstance(a, str) or a not in {"A", "B", "C", "D"} for a in answers)):
        raise web.HTTPBadRequest(text="Answer all 25 questions")
    by_id = {q["id"]: q for q in load_questions()}
    if len(question_ids) != 25 or any(qid not in by_id for qid in question_ids):
        raise web.HTTPConflict(text="Question set is no longer available")
    correct = sum(answer == by_id[qid]["answer"] for qid, answer in zip(question_ids, answers))
    duration = max(0, int(time.time()) - attempt["started_at"])
    iq_score = calculate_iq(question_ids, answers, by_id, duration)
    saved_duration = database.finish_attempt(attempt_id, uid, answers, correct, iq_score)
    if saved_duration is None:
        raise web.HTTPConflict(text="Test session has already been completed")
    return web.json_response({"correct": correct, "total": 25, "duration": saved_duration, "iq": iq_score})


@dp.message(CommandStart())
async def start(message: Message):
    keyboard = InlineKeyboardBuilder()
    keyboard.button(text="IQ TESTNI BOSHLASH", web_app=WebAppInfo(url=WEBAPP_URL))
    await message.answer("IQ Test platformasiga xush kelibsiz.\n\nTestni boshlash uchun tugmani bosing.",
                         reply_markup=keyboard.as_markup())


async def start_server():
    app = web.Application(client_max_size=32768, middlewares=[api_errors, security_headers])
    async def health(_request):
        return web.json_response({"status": "ok"})

    async def index(_request):
        return web.FileResponse(WEBAPP_DIR / "index.html")

    app.router.add_get("/health", health)
    app.router.add_get("/", index)
    app.router.add_post("/api/profile", api_profile)
    app.router.add_post("/api/questions", api_questions)
    app.router.add_post("/api/finish", api_finish)
    app.router.add_static("/", WEBAPP_DIR, show_index=True)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, HOST, PORT)
    await site.start()
    log.info("Web server listening on %s:%s", HOST, PORT)
    return runner


async def main():
    if not BOT_TOKEN:
        raise RuntimeError("Set BOT_TOKEN in .env before starting the bot")
    parsed_url = urlsplit(WEBAPP_URL)
    if (parsed_url.scheme != "https" or not parsed_url.hostname
            or parsed_url.hostname.lower() in {"localhost", "127.0.0.1", "::1"}
            or parsed_url.username or parsed_url.password):
        raise RuntimeError("WEBAPP_URL must be a public HTTPS URL (not localhost)")
    database.init_db()
    bot = Bot(BOT_TOKEN)
    runner = await start_server()
    try:
        await dp.start_polling(bot)
    finally:
        await runner.cleanup()
        await bot.session.close()


if __name__ == "__main__":
    asyncio.run(main())
