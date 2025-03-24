import os
import json
import logging
import asyncio
import aiohttp
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
from aiogram.utils.keyboard import ReplyKeyboardBuilder
from datetime import datetime, time
import re


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
    """Парсит cron-строку или принимает время"""
    if ":" in cron_str:
        cron_str = time_to_cron(cron_str)

    parts = cron_str.strip().split()
    return {
        "minute": parts[0],
        "hour": parts[1],
        "day": parts[2],
        "month": parts[3],
        "day_of_week": parts[4],
        "second": "0"
    }

#  функция конвертации времени
def time_to_cron(user_time: str) -> str:
    """
    Конвертирует время в формате HH:MM в cron-формат
    Пример: "09:30" -> "30 9 * * *"
    """
    if not re.match(r"^([0-1]?[0-9]|2[0-3]):[0-5][0-9]$", user_time):
        raise ValueError("Неверный формат времени")

    hours, minutes = map(int, user_time.split(':'))
    return f"{minutes} {hours} * * *"

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
        await message.answer("🚫 Доступ запрещён!", reply_markup=types.ReplyKeyboardRemove())
        return

    try:
        cron_parts = DB["schedule"].split()
        current_time = datetime.strptime(
            f"{cron_parts[1]}:{cron_parts[0]}", "%H:%M"
        ).strftime("%H:%M")
    except Exception as e:
        logger.error(f"Error parsing cron time: {e}")
        current_time = "⏰ Не установлено"

    # Обновленный текст с инструкцией
    status_text = (
        f"⚙️ *Админ\-панель* \n\n"
        f"▫️ *Текущий жанр*: {escape_md(DB['current_genre'])}\n"
        f"▫️ *Стиль рецензий*: {escape_md(DB['current_style'])}\n"
        f"▫️ *Время публикации*: {escape_md(current_time)}\n\n"
        f"_Выберите действие из кнопок ниже\._"
        f"\n\n❌ Для отмены введите /cancel"  # Добавили сюда
    )

    builder = ReplyKeyboardBuilder()
    builder.row(
        KeyboardButton(text="🎭 Сменить жанр"),
        KeyboardButton(text="🖋 Сменить стиль")
    )
    builder.row(
        KeyboardButton(text="⏰ Изменить расписание"),
        KeyboardButton(text="🚀 Опубликовать сейчас")
    )
    builder.row(KeyboardButton(text="🔙 В главное меню"))

    # Отправляем ОДНО сообщение с клавиатурой
    await message.answer(
        status_text,
        parse_mode=ParseMode.MARKDOWN_V2,
        reply_markup=builder.as_markup(
            resize_keyboard=True,
            input_field_placeholder="Выберите действие\.\.\."
        )
    )

# Обработчики админ-меню
@dp.message(F.text == "⏰ Изменить расписание")
async def set_schedule_handler(message: types.Message, state: FSMContext):
    if message.from_user.id not in ADMINS:
        return

    # Создаем клавиатуру с примерами
    builder = ReplyKeyboardBuilder()
    for t in ["09:00", "12:00", "15:00", "18:00"]:
        builder.add(KeyboardButton(text=t))
    builder.adjust(2)

    await message.answer(
        "🕒 Введите время публикации в формате ЧЧ:ММ\n"
        "Пример: 09:30 или 14:00\n\n"
        "Или выберите из готовых вариантов:",
        reply_markup=builder.as_markup(resize_keyboard=True)
    )
    await state.set_state(AdminStates.setting_schedule)

@dp.message(F.text == "🎭 Сменить жанр")  # Добавьте этот хэндлер
async def set_genre_handler(message: types.Message, state: FSMContext):
    if message.from_user.id not in ADMINS:
        return

    markup = InlineKeyboardBuilder()
    for genre in ["боевик", "комедия", "драма", "фантастика"]:
        markup.button(text=genre, callback_data=f"genre_{genre}")
    markup.adjust(2)

    await message.answer(
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


# 3. Обновим обработчик состояния
@dp.message(AdminStates.setting_schedule)
async def process_schedule_input(message: types.Message, state: FSMContext):
    try:
        # Конвертируем время в cron
        cron_str = time_to_cron(message.text)

        # Обновляем расписание
        DB["schedule"] = cron_str

        # Проверяем существование задачи
        job = scheduler.get_job("daily_post")
        if job:
            scheduler.reschedule_job("daily_post", trigger='cron', **parse_cron(cron_str))
        else:
            scheduler.add_job(publish_scheduled_post, trigger='cron', id="daily_post", **parse_cron(cron_str))

        await admin_panel(message)

   #  except ValueError as e:


        # Форматируем красивое время для ответа
    #    time_obj = datetime.strptime(message.text, "%H:%M").time()
     #   formatted_time = time_obj.strftime("%H:%M")

    #    await message.answer(
     #       f"✅ Расписание обновлено!\n"
     #       f"Новое время публикации: {formatted_time}",
      #      reply_markup=types.ReplyKeyboardRemove()
     #   )

    except ValueError as e:
        await message.answer(
            f"❌ Неверный формат времени:\n"
            f"{escape_md(str(e))}\n"
            f"Попробуйте ещё раз в формате ЧЧ:ММ",
            parse_mode=ParseMode.MARKDOWN_V2
        )
    finally:
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
    id="daily_post",
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