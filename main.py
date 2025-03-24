import os
import json
import logging
import asyncio
import aiohttp
import uuid
import re
from typing import Dict, Optional
from aiogram import Bot, Dispatcher, types, F
from aiogram.utils.keyboard import InlineKeyboardBuilder, ReplyKeyboardBuilder
from aiogram.enums import ParseMode
from aiogram.client.default import DefaultBotProperties
from apscheduler.schedulers.asyncio import AsyncIOScheduler
import openai
from dotenv import load_dotenv
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.context import FSMContext
from aiogram.types import ReplyKeyboardMarkup, KeyboardButton
from datetime import datetime, time

# Загрузка переменных окружения
load_dotenv()

# Настройка логгера
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Конфигурация
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
ADMINS = list(map(int, os.getenv("ADMINS").split(','))) if os.getenv("ADMINS") else []
CHANNEL_ID = os.getenv("CHANNEL_ID")
MOVIES_HISTORY_FILE = "movies_history.json"

# Инициализация бота и диспетчера
bot = Bot(token=TELEGRAM_BOT_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.MARKDOWN_V2))
dp = Dispatcher()
scheduler = AsyncIOScheduler()

# База данных
DB = {
    "current_genre": "боевик",
    "current_style": "аналитический",
    "schedule": "0 9 * * *",
    "posted_imdb_ids": []
}


# Состояния FSM
class AdminStates(StatesGroup):
    setting_genre = State()
    setting_style = State()
    setting_schedule = State()


# Утилиты
def escape_md(text: str) -> str:
    escape_chars = '_*[]()~`>#+-=|{}.!'
    return ''.join(f'\\{char}' if char in escape_chars else char for char in str(text))


def time_to_cron(user_time: str) -> str:
    if not re.match(r"^([0-1]?[0-9]|2[0-3]):[0-5][0-9]$", user_time):
        raise ValueError("Неверный формат времени")
    hours, minutes = map(int, user_time.split(':'))
    return f"{minutes} {hours} * * *"


def parse_cron(cron_str: str) -> dict:
    if ":" in cron_str:
        cron_str = time_to_cron(cron_str)
    parts = cron_str.strip().split()
    return {
        "minute": parts[0],
        "hour": parts[1],
        "day": parts[2],
        "month": parts[3],
        "day_of_week": parts[4]
    }


# Работа с историей фильмов
def save_to_history(movie: dict):
    try:
        with open(MOVIES_HISTORY_FILE, "a", encoding="utf-8") as f:
            record = {
                "date": datetime.now().isoformat(),
                **movie
            }
            f.write(json.dumps(record, ensure_ascii=False) + "\n")
    except Exception as e:
        logger.error(f"Ошибка сохранения истории: {str(e)}")


def load_history() -> list:
    try:
        with open(MOVIES_HISTORY_FILE, "r", encoding="utf-8") as f:
            return [json.loads(line) for line in f.readlines()]
    except FileNotFoundError:
        return []


# OpenAI функции
openai.api_key = OPENAI_API_KEY

MOVIE_PROMPT = """Сгенерируй описание фильма в жанре {genre} в формате:
Title: Название
Year: Год
IMDB-ID: ttXXXXXX (действительный идентификатор с IMDB)
Plot: Краткое описание

Только действительные существующие фильмы!"""


async def get_movie_data(genre: str) -> Optional[dict]:
    attempt = 0
    while attempt < 3:
        try:
            response = await openai.ChatCompletion.acreate(
                model="gpt-4",
                messages=[{
                    "role": "user",
                    "content": MOVIE_PROMPT.format(genre=genre)
                }],
                temperature=0.7
            )

            raw_text = response.choices[0].message.content
            return parse_movie_response(raw_text)

        except Exception as e:
            logger.error(f"Попытка {attempt + 1} неудачна: {str(e)}")
            attempt += 1
    return None


def parse_movie_response(text: str) -> Optional[dict]:
    try:
        imdb_id = re.search(r'tt\d{7,8}', text).group(0)
        title = re.search(r'Title: (.+)', text).group(1)
        year = re.search(r'Year: (\d{4})', text).group(1)
        plot = re.search(r'Plot: (.+)', text, re.DOTALL).group(1)

        return {
            "imdb_id": imdb_id,
            "title": title.strip(),
            "year": int(year),
            "plot": plot.strip()
        }
    except Exception as e:
        logger.error(f"Ошибка парсинга: {str(e)}")
        return None


async def generate_review(movie: dict) -> str:
    try:
        response = await openai.ChatCompletion.acreate(
            model="gpt-4",
            messages=[{
                "role": "system",
                "content": f"Напиши {DB['current_style']} рецензию. {STYLE_DESCRIPTIONS.get(DB['current_style'], '')}"
            }, {
                "role": "user",
                "content": f"Фильм: {movie['title']} ({movie['year']})\nСюжет: {movie['plot']}"
            }]
        )
        return response.choices[0].message.content
    except Exception as e:
        logger.error(f"Ошибка генерации рецензии: {str(e)}")
        return "Рецензия временно недоступна"


# Основная логика публикации
async def publish_scheduled_post():
    movie = await get_movie_data(DB["current_genre"])

    if not movie:
        await notify_admin("❌ Не удалось получить данные фильма!")
        return

    if movie["imdb_id"] in DB["posted_imdb_ids"]:
        await handle_duplicate(movie)
        return

    try:
        review = await generate_review(movie)
        post = (
            f"🎬 *{escape_md(movie['title'])}* \\({escape_md(str(movie['year']))}\\)\n\n"
            f"📖 Жанр: {escape_md(DB['current_genre'])}\n"
            f"📝 Рецензия \\({escape_md(DB['current_style'])}\\):\n{escape_md(review)}"
        )

        await bot.send_message(CHANNEL_ID, text=post, parse_mode=ParseMode.MARKDOWN_V2)
        DB["posted_imdb_ids"].append(movie["imdb_id"])
        save_to_history(movie)

    except Exception as e:
        logger.error(f"Ошибка публикации: {str(e)}")
        await notify_admin(f"🔥 Ошибка публикации: {str(e)}")


async def handle_duplicate(movie: dict):
    logger.warning(f"Дубликат IMDB ID: {movie['imdb_id']}")
    new_movie = await get_movie_data(DB["current_genre"])
    if new_movie:
        await publish_scheduled_post()


# Уведомления админа
async def notify_admin(message: str):
    for admin in ADMINS:
        await bot.send_message(admin, message)


# Обработчики команд
@dp.message(F.text == "/start")
async def cmd_start(message: types.Message):
    markup = ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="🎬 Найти фильм")],
            [KeyboardButton(text="ℹ️ Помощь")]
        ],
        resize_keyboard=True
    )

    if message.from_user.id in ADMINS:
        markup.keyboard.append([KeyboardButton(text="/admin")])

    await message.answer(
        "Добро пожаловать в CinemaBot\! 🍿\nВыберите действие:",
        reply_markup=markup
    )


@dp.message(F.text == "/admin")
async def admin_panel(message: types.Message):
    if message.from_user.id not in ADMINS:
        return

    try:
        cron_parts = DB["schedule"].split()
        current_time = f"{cron_parts[1]}:{cron_parts[0]}"
    except:
        current_time = "⏰ Не установлено"

    status_text = (
        f"⚙️ *Админ\-панель*\n\n"
        f"▫️ Жанр: {escape_md(DB['current_genre'])}\n"
        f"▫️ Стиль: {escape_md(DB['current_style'])}\n"
        f"▫️ Время: {escape_md(current_time)}\n\n"
        f"Опубликовано фильмов: {len(DB['posted_imdb_ids'])}"
    )

    builder = ReplyKeyboardBuilder()
    builder.row(KeyboardButton(text="🎭 Сменить жанр"), KeyboardButton(text="🖋 Сменить стиль"))
    builder.row(KeyboardButton(text="⏰ Изменить время"), KeyboardButton(text="🚀 Опубликовать сейчас"))
    builder.row(KeyboardButton(text="🔙 В меню"))

    await message.answer(
        status_text,
        reply_markup=builder.as_markup(resize_keyboard=True)
    )


# Остальные обработчики и запуск
async def main():
    # Загрузка истории при старте
    history = load_history()
    DB["posted_imdb_ids"] = [m["imdb_id"] for m in history[-500:]]

    scheduler.add_job(
        publish_scheduled_post,
        trigger='cron',
        **parse_cron(DB['schedule'])
    )
    scheduler.start()

    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())