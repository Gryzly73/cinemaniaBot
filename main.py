import os
import json
import logging
from typing import Dict, Optional
from aiogram import Bot, Dispatcher, types, F
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.utils.keyboard import InlineKeyboardBuilder
from apscheduler.schedulers.asyncio import AsyncIOScheduler
import openai
from dotenv import load_dotenv
import asyncio

load_dotenv()

# Конфигурация
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
ADMINS = list(map(int, os.getenv("ADMINS").split(',')))
CHANNEL_ID = os.getenv("CHANNEL_ID")

bot = Bot(token=TELEGRAM_BOT_TOKEN)
dp = Dispatcher()
scheduler = AsyncIOScheduler()

# База данных
DB = {
    "current_genre": "боевик",
    "current_style": "аналитический",
    "schedule": "0 9 * * *",
    "posted_movies": []
}


# Состояния FSM
class AdminStates(StatesGroup):
    setting_genre = State()
    setting_style = State()
    setting_schedule = State()


# Инициализация OpenAI
openai.api_key = OPENAI_API_KEY


# Утилиты
def escape_md(text: str) -> str:
    escape_chars = '_*[]()~`>#+-=|{}.!'
    return ''.join(f'\\{char}' if char in escape_chars else char for char in text)


# Админ-панель
@dp.message(F.text == "/admin")
async def admin_panel(message: types.Message):
    if message.from_user.id not in ADMINS:
        return

    keyboard = InlineKeyboardBuilder()
    keyboard.button(text="🎭 Установить жанр", callback_data="set_genre")
    keyboard.button(text="🖋 Установить стиль", callback_data="set_style")
    keyboard.button(text="⏰ Установить расписание", callback_data="set_schedule")
    keyboard.button(text="🚀 Опубликовать сейчас", callback_data="publish_now")
    keyboard.adjust(2)

    status = (
        f"Текущие настройки:\n"
        f"Жанр: {DB['current_genre']}\n"
        f"Стиль: {DB['current_style']}\n"
        f"Расписание: {DB['schedule']}"
    )

    await message.answer(
        escape_md(status),
        reply_markup=keyboard.as_markup(),
        parse_mode=ParseMode.MARKDOWN_V2
    )


# Обработчики настроек
@dp.callback_query(F.data == "set_genre")
async def set_genre_handler(callback: types.CallbackQuery, state: FSMContext):
    keyboard = InlineKeyboardBuilder()
    for genre in ["боевик", "комедия", "драма", "фантастика"]:
        keyboard.button(text=genre, callback_data=f"genre_{genre}")
    await callback.message.edit_text(
        "Выберите жанр:",
        reply_markup=keyboard.as_markup()
    )


@dp.callback_query(F.data.startswith("genre_"))
async def genre_selected(callback: types.CallbackQuery):
    DB['current_genre'] = callback.data.split("_")[1]
    await callback.message.edit_text(f"✅ Жанр установлен: {DB['current_genre']}")


# Аналогично реализовать для стиля и расписания...

# Основная логика публикации
async def publish_scheduled_post():
    try:
        # Запрос к GPT для подбора фильма
        movie = await get_movie_by_genre(DB['current_genre'])
        review = await generate_review(movie, DB['current_style'])

        # Формирование поста
        post = format_post(movie, review)

        # Публикация
        await bot.send_message(
            chat_id=CHANNEL_ID,
            text=post,
            parse_mode=ParseMode.MARKDOWN_V2
        )

        # Обновление истории
        DB['posted_movies'].append(movie['title'])

    except Exception as e:
        logging.error(f"Publish error: {e}")


async def get_movie_by_genre(genre: str) -> Dict:
    prompt = f"Назови новый популярный фильм в жанре {genre}. Ответ в JSON: "
    response = await openai.ChatCompletion.acreate(
        model="gpt-4",
        messages=[{"role": "user", "content": prompt}]
    )
    return json.loads(response.choices[0].message.content)


async def generate_review(movie: Dict, style: str) -> str:
    prompt = (
        f"Напиши {style} рецензию на фильм {movie['title']}. "
        f"Основные параметры: год - {movie['year']}, жанр - {movie['genre']}. "
        "Используй Markdown форматирование."
    )
    response = await openai.ChatCompletion.acreate(
        model="gpt-4",
        messages=[{"role": "user", "content": prompt}]
    )
    return response.choices[0].message.content


def format_post(movie: Dict, review: str) -> str:
    return (
        f"🎬 *{escape_md(movie['title'])}* ({movie['year']})\n\n"
        f"📖 Жанр: {escape_md(movie['genre'])}\n\n"
        f"📝 Рецензия ({DB['current_style']}):\n{escape_md(review)}"
    )


def parse_cron(cron_str: str) -> dict:
    parts = cron_str.strip().split()
    if len(parts) != 5:
        raise ValueError("Неверный формат cron. Пример: '0 9 * * *'")

    return {
        "minute": parts[0],
        "hour": parts[1],
        "day": parts[2],
        "month": parts[3],
        "day_of_week": parts[4]
    }

# Инициализация расписания
try:
    scheduler.add_job(
        publish_scheduled_post,
        trigger='cron',
        **parse_cron(DB['schedule'])
    )
except ValueError as e:
    logger.error(f"Ошибка в расписании: {e}")


def parse_cron(schedule: str) -> Dict:
    # Конвертация строки "0 9 * * *" в параметры для APScheduler
    parts = schedule.split()
    return {
        "minute": parts[0],
        "hour": parts[1],
        "day": parts[2],
        "month": parts[3],
        "day_of_week": parts[4]
    }


# Запуск
async def main():
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())