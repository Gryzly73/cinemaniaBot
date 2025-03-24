import os
import json
import logging
import asyncio
import aiohttp
from typing import Dict, Optional
from aiogram import Bot, Dispatcher, types, F
from aiogram.utils.keyboard import InlineKeyboardBuilder
from aiogram.enums import ParseMode
from aiogram.client.default import DefaultBotProperties
from apscheduler.schedulers.asyncio import AsyncIOScheduler
import openai
from dotenv import load_dotenv
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.context import FSMContext
from aiogram.types import ReplyKeyboardMarkup, KeyboardButton


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

# Инициализация бота и диспетчера
bot = Bot(token=TELEGRAM_BOT_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.MARKDOWN_V2))
dp = Dispatcher()
scheduler = AsyncIOScheduler()

# База данных (в памяти)
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

# Утилиты

def escape_md(text: str) -> str:
    escape_chars = '_*[]()~`>#+-=|{}.!'
    return ''.join(f'\\{char}' if char in escape_chars else char for char in str(text))

def parse_cron(cron_str: str) -> dict:
    """Парсинг строки cron в параметры для APScheduler"""
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

# Инициализация OpenAI
openai.api_key = OPENAI_API_KEY

# Загрузка стилей рецензий
try:
    with open("styles.json", "r", encoding="utf-8") as f:
        STYLE_DESCRIPTIONS = json.load(f)
except Exception as e:
    logger.error(f"Error loading styles: {e}")
    STYLE_DESCRIPTIONS = {"default": "Стандартный стиль рецензии"}

@dp.message(F.text == "/start")
async def cmd_start(message: types.Message):
    # Создаем клавиатуру
    markup = ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="🎬 Найти фильм")],
            [KeyboardButton(text="ℹ️ Помощь")]
        ],
        resize_keyboard=True
    )

    # Если пользователь - администратор, добавляем кнопку админки
    if message.from_user.id in ADMINS:
        markup.keyboard.append([KeyboardButton(text=escape_md("/admin"))])

    await message.answer(
        "Добро пожаловать в CinemaBot\! 🍿\n"
        "Выберите действие:",
        reply_markup=markup
    )

# Обновим обработчик админ-панели
@dp.message(F.text == "/admin")
async def admin_panel(message: types.Message):
    if message.from_user.id not in ADMINS:
        await message.answer("Доступ запрещён ❌")
        return

    markup = ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="🎭 Сменить жанр"),
             KeyboardButton(text="🖋 Сменить стиль")],
            [KeyboardButton(text="⏰ Изменить расписание"),
             KeyboardButton(text="🚀 Опубликовать сейчас")],
            [KeyboardButton(text="🔙 В главное меню")]
        ],
        resize_keyboard=True
    )

    await message.answer(
        "⚙️ Админ\-панель:",
        reply_markup=markup
    )

# Обработчики админ-меню
@dp.callback_query(F.data == "set_genre")
async def set_genre_handler(callback: types.CallbackQuery, state: FSMContext):
    if callback.from_user.id not in ADMINS:
        return

    markup = InlineKeyboardBuilder()
    for genre in ["боевик", "комедия", "драма", "фантастика"]:
        markup.button(text=genre, callback_data=f"genre_{genre}")
    markup.adjust(2)

    await callback.message.answer(  # Используем callback.message
        "🎭 Выберите новый жанр:",
        reply_markup=markup.as_markup()
    )
    await state.set_state(AdminStates.setting_genre)

@dp.callback_query(F.data.startswith("genre_"))
async def genre_selected(callback: types.CallbackQuery, state: FSMContext):
    DB["current_genre"] = callback.data.split("_")[1]
    await callback.message.edit_text(f"✅ Жанр установлен: {DB['current_genre']}")
    await state.clear()

@dp.message(F.text == "🖋 Сменить стиль")
async def set_style_handler(message: types.Message, state: FSMContext):
    if message.from_user.id not in ADMINS:
        return

    markup = InlineKeyboardBuilder()
    for style in STYLE_DESCRIPTIONS:
        markup.button(text=style, callback_data=f"style_{style}")
    markup.adjust(2)

    await message.answer(
        "🖋 Выберите новый стиль:",
        reply_markup=markup.as_markup()
    )
    await state.set_state(AdminStates.setting_style)

@dp.callback_query(F.data.startswith("style_"))
async def style_selected(callback: types.CallbackQuery, state: FSMContext):
    DB["current_style"] = callback.data.split("_")[1]
    await callback.message.edit_text(f"✅ Стиль установлен: {DB['current_style']}")
    await state.clear()

@dp.callback_query(F.data == "set_schedule")
async def set_schedule_handler(callback: types.CallbackQuery, state: FSMContext):
    """Обработчик кнопки изменения расписания"""
    if message.from_user.id not in ADMINS:
        return

    await message.answer(
        "⏰ Введите новое расписание в формате cron:\n"
        "Пример: 0 9 * * * - ежедневно в 9:00\n"
        "Формат: [минуты] [часы] [дни] [месяцы] [дни недели]"
    )
    await state.set_state(AdminStates.setting_schedule)

@dp.message(AdminStates.setting_schedule)
async def schedule_entered(message: types.Message, state: FSMContext):
    try:
        parse_cron(message.text)
        DB["schedule"] = message.text
        await message.answer(f"✅ Расписание обновлено: {message.text}")
    except ValueError:
        await message.answer("❌ Неверный формат. Пример: '0 9 * * *'")
    await state.clear()


@dp.message(F.text == "🚀 Опубликовать сейчас")
async def publish_now_handler(message: types.Message):
    if message.from_user.id not in ADMINS:
        return

    await message.answer("🔄 Запускаю публикацию\.\.\.")
    try:
        await publish_scheduled_post()
        await message.answer("✅ Рецензия успешно опубликована!")
    except Exception as e:
        await message.answer(f"❌ Ошибка: {escape_md(str(e))}")

@dp.message(F.text == "🔙 В главное меню")
async def back_to_main_handler(message: types.Message, state: FSMContext):
    """Обработчик возврата в главное меню"""
    await state.clear()
    await cmd_start(message)

@dp.callback_query(F.data.startswith("genre_"), AdminStates.setting_genre)
async def genre_selected(callback: types.CallbackQuery, state: FSMContext):
    genre = callback.data.split("_")[1]
    DB["current_genre"] = genre
    await callback.message.edit_text(f"✅ Жанр изменен на: {genre}")
    await state.clear()

@dp.callback_query(F.data.startswith("style_"), AdminStates.setting_style)
async def style_selected(callback: types.CallbackQuery, state: FSMContext):
    style = callback.data.split("_")[1]
    DB["current_style"] = style
    await callback.message.edit_text(f"✅ Стиль изменен на: {style}")
    await state.clear()

@dp.message(AdminStates.setting_schedule)
async def schedule_entered(message: types.Message, state: FSMContext):
    try:
        parse_cron(message.text)
        DB["schedule"] = message.text
        scheduler.reschedule_job(
            "daily_post",
            **parse_cron(DB['schedule'])
        )
        await message.answer(f"✅ Расписание обновлено: {message.text}")
    except ValueError as e:
        await message.answer(f"❌ Ошибка: {escape_md(str(e))}")
    await state.clear()

# Основная логика публикации
async def publish_scheduled_post():
    try:
        # Запрос к GPT для получения фильма
        response = await openai.ChatCompletion.acreate(
            model="gpt-4",
            messages=[{
                "role": "user",
                "content": (
                    f"Назови популярный фильм в жанре {DB['current_genre']}. "
                    "Ответ строго в формате JSON: {\"title\": \"Название\", \"year\": год, "
                    "\"genre\": \"Жанр\", \"description\": \"Описание\"}. "
                    "Только JSON, без комментариев."
                )
            }],
            temperature=0.3  # Для более структурированного ответа
        )

        # Логируем и чистим ответ
        raw_response = response.choices[0].message.content
        logger.debug(f"Raw response: {raw_response}")

        # Заменяем одинарные кавычки и лишние символы
        json_str = (
            raw_response
            .replace("'", '"')
            .replace("```json", "")
            .replace("```", "")
            .strip()
        )

        # Добавляем обертку, если нужно
        if not json_str.startswith("{"):
            json_str = "{" + json_str.split("{", 1)[-1]
        if not json_str.endswith("}"):
            json_str = json_str.split("}", 1)[0] + "}"

        movie = json.loads(json_str)

        # Генерация рецензии
        review_response = await openai.ChatCompletion.acreate(
            model="gpt-4",
            messages=[{
                "role": "system",
                "content": f"Напиши {DB['current_style']} рецензию. {STYLE_DESCRIPTIONS.get(DB['current_style'], '')}"
            }, {
                "role": "user",
                "content": f"Фильм: {movie['title']} ({movie['year']})"
            }]
        )
        review = review_response.choices[0].message.content

        # Экранируем ВСЕ динамические данные
        safe_title = escape_md(movie['title'])
        safe_year = escape_md(str(movie['year']))
        safe_genre = escape_md(movie['genre'])
        safe_style = escape_md(DB['current_style'])
        safe_review = escape_md(review)

        # Формирование поста с экранированием всех частей
        post = (
            f"🎬 *{safe_title}* \\({safe_year}\\)\n\n"
            f"📖 Жанр: {safe_genre}\n"
            f"📝 Рецензия \\({safe_style}\\):\n{safe_review}"
        )

        await bot.send_message(
            chat_id=CHANNEL_ID,
            text=post,
            parse_mode=ParseMode.MARKDOWN_V2
        )

    except json.JSONDecodeError as e:
        logger.error(f"JSON decode error: {e}\nResponse: {raw_response}")
        await bot.send_message(
            ADMINS[0],
            f"❌ Ошибка формата JSON:\n{raw_response}"
        )
    except Exception as e:
        logger.error(f"Ошибка публикации: {str(e)}")
        await bot.send_message(
            ADMINS[0],
            f"🔥 Критическая ошибка:\n{str(e)}"
        )

# Инициализация расписания
scheduler.add_job(
    publish_scheduled_post,
    trigger='cron',
    **parse_cron(DB['schedule'])
)

async def main():
    # Запускаем планировщик внутри event loop
    scheduler.start()

    # Запускаем бота
    await dp.start_polling(bot)

if __name__ == "__main__":
    try:
        # Создаем новый event loop и запускаем основную корутину
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("Bot stopped")
    finally:
        # Останавливаем планировщик при выходе
        scheduler.shutdown()