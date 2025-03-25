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
    custom_review = State()

# Загрузка стилей рецензий
try:
    with open("styles.json", "r", encoding="utf-8") as f:
        STYLE_DESCRIPTIONS = json.load(f)
except Exception as e:
    logger.error(f"Error loading styles: {e}")
    STYLE_DESCRIPTIONS = {"default": "Стандартный стиль рецензии"}

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
Plot: Краткое описание на русском языке

Только действительные существующие фильмы!"""

GENERAL_REVIEW_PROMPT = os.getenv("GENERAL_REVIEW_PROMPT", "Стандартные требования к рецензии")

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

# Обновлённая функция генерации рецензии
async def generate_review(movie: dict) -> str:
    style_description = STYLE_DESCRIPTIONS.get(
        DB['current_style'],
        "Стандартный аналитический стиль"
    )

    system_prompt = (
        f"{GENERAL_REVIEW_PROMPT}\n\n"
        f"Стиль изложения: {DB['current_style']}\n"
        f"Характеристики стиля: {style_description}"
    )

    try:
        response = await openai.ChatCompletion.acreate(
            model="gpt-4",
            messages=[
                {
                    "role": "system",
                    "content": system_prompt
                },
                {
                    "role": "user",
                    "content": (
                        f"Фильм: {movie['title']} ({movie['year']})\n"
                        f"Сюжет: {movie['plot']}\n\n"
                        "Сгенерируй рецензию согласно указанным требованиям:"
                    )
                }
            ],
            temperature=0.7,
            max_tokens=1000
        )
        return response.choices[0].message.content
    except Exception as e:
        logger.error(f"Ошибка генерации: {str(e)}")
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

# Обработчик кнопки "🎭 Сменить жанр"
@dp.message(F.text == "🎭 Сменить жанр")
async def set_genre_handler(message: types.Message, state: FSMContext):
    if message.from_user.id not in ADMINS:
        return

    builder = InlineKeyboardBuilder()
    for genre in ["боевик", "комедия", "драма", "фантастика"]:
        builder.button(text=genre, callback_data=f"genre_{genre}")
    builder.adjust(2)

    await message.answer(
        "🎭 Выберите новый жанр:",
        reply_markup=builder.as_markup()
    )
    await state.set_state(AdminStates.setting_genre)

# Обработчик кнопки "🖋 Сменить стиль"
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

# Обработчик кнопки "⏰ Изменить время"
@dp.message(F.text == "⏰ Изменить время")
async def set_schedule_handler(message: types.Message, state: FSMContext):
    if message.from_user.id not in ADMINS:
        return

    builder = ReplyKeyboardBuilder()
    for t in ["09:00", "12:00", "15:00", "18:00"]:
        builder.add(KeyboardButton(text=t))
    builder.adjust(2)

    await message.answer(
        "🕒 Введите время в формате ЧЧ:ММ\nПример: 09:30",
        reply_markup=builder.as_markup(resize_keyboard=True)
    )
    await state.set_state(AdminStates.setting_schedule)

# Обработчик команды /cancel
@dp.message(F.text == "/cancel")
async def cancel_handler(message: types.Message, state: FSMContext):
    if message.from_user.id not in ADMINS:
        return

    await state.clear()
    await message.answer("❌ Операция отменена", reply_markup=types.ReplyKeyboardRemove())
    await admin_panel(message)

@dp.message(F.text == "❌ Отменить операцию")
async def cancel_button_handler(message: types.Message, state: FSMContext):
    await cancel_handler(message, state)

# Обработчик кнопки "📝 Еще рецензия"
@dp.message(F.text == "📝 Еще рецензия")
async def another_review_handler(message: types.Message, state: FSMContext):
    await custom_review_start(message, state)

# Изменения в функции generate_custom_review и добавление parse_custom_review
def parse_custom_review(text: str) -> Optional[dict]:
    try:
        title = re.search(r'Title: (.+)', text).group(1)
        year = re.search(r'Year: (\d{4})', text).group(1)
        review = re.search(r'Review: (.+)', text, re.DOTALL).group(1).strip()
        plot_match = re.search(r'Plot: (.+)', text, re.DOTALL)
        plot = plot_match.group(1).strip() if plot_match else "Описание отсутствует"
        return {
            "title": title.strip(),
            "year": int(year),
            "review": review,
            "plot": plot
        }
    except Exception as e:
        logger.error(f"Ошибка парсинга кастомной рецензии: {str(e)}")
        return None

async def generate_custom_review(query: str) -> Optional[dict]:
    system_prompt = (
        f"{GENERAL_REVIEW_PROMPT}\n"
        f"Стиль: {DB['current_style']}\n"
        "Учти: пользователь мог ввести название, концепцию или краткое описание!\n"
        "Формат ответа:\n"
        "Title: Название фильма\n"
        "Year: Год выпуска\n"
        "Plot: Краткое описание сюжета\n"
        "Review: Текст рецензии\n\n"
    )

    try:
        response = await openai.ChatCompletion.acreate(
            model="gpt-4",
            messages=[
                {
                    "role": "system",
                    "content": system_prompt
                },
                {
                    "role": "user",
                    "content": f"Запрос: {query}\n\nНапиши рецензию в указанном формате:"
                }
            ],
            temperature=0.8,
            max_tokens=1500
        )
        raw_text = response.choices[0].message.content
        return parse_custom_review(raw_text)
    except Exception as e:
        logger.error(f"Ошибка генерации кастомной рецензии: {str(e)}")
        return None

# Обновлённый обработчик process_custom_review
@dp.message(AdminStates.custom_review)
async def process_custom_review(message: types.Message, state: FSMContext):
    try:
        current_state = await state.get_state()

        await bot.send_chat_action(message.chat.id, "typing")
        review_data = await generate_custom_review(message.text)

        if not review_data:
            await message.answer("⚠️ Не удалось сгенерировать рецензию. Попробуйте другой запрос.")
            await state.clear()
            return

        # Сохраняем данные в состоянии
        await state.update_data(
            movie={
                "title": review_data["title"],
                "year": review_data["year"],
                "plot": review_data["plot"],
                "imdb_id": f"custom_{uuid.uuid4().hex}"
            },
            review=review_data["review"]
        )

        builder = ReplyKeyboardBuilder()
        builder.row(KeyboardButton(text="🚀 Опубликовать сейчас"))
        builder.row(
            KeyboardButton(text="📝 Еще рецензия"),
            KeyboardButton(text="🔙 В админку")
        )

        await message.answer(
            escape_md(f"📝 Рецензия ({DB['current_style']}):\n\n{review_data['review']}"),
            reply_markup=builder.as_markup(resize_keyboard=True),
            parse_mode=ParseMode.MARKDOWN_V2
        )

    except Exception as e:
        logger.error(f"Custom review error: {str(e)}")
        await message.answer("⚠️ Ошибка генерации. Попробуйте другой запрос.")
        await state.clear()

# Модифицированный обработчик публикации
@dp.message(F.text == "🚀 Опубликовать сейчас")
async def publish_now_handler(message: types.Message, state: FSMContext):
    if message.from_user.id not in ADMINS:
        return

    data = await state.get_data()
    movie = data.get('movie')
    review = data.get('review')

    if movie and review:
        try:
            post = (
                f"🎬 *{escape_md(movie['title'])}* \\({escape_md(str(movie['year']))}\\)\n\n"
                f"📖 Жанр: {escape_md(DB['current_genre'])}\n"
                f"📝 Рецензия \\({escape_md(DB['current_style'])}\\):\n{escape_md(review)}"
            )
            await bot.send_message(CHANNEL_ID, text=post, parse_mode=ParseMode.MARKDOWN_V2)

            # Сохранение истории
            save_to_history({
                "imdb_id": movie["imdb_id"],
                "title": movie["title"],
                "year": movie["year"],
                "plot": movie.get("plot", "")
            })

            await message.answer("✅ Рецензия опубликована\!")
        except Exception as e:
            await message.answer(f"⚠️ Ошибка публикации\: {e}")
        finally:
            await state.clear()  # Очищаем состояние после публикации
    else:
        await message.answer("⚠️ Нет рецензии для публикации\! Попробуйте создать новую\.")

# Обновление обработчика возврата в админку для очистки состояния
@dp.message(F.text == "🔙 В админку")
async def back_to_admin_handler(message: types.Message, state: FSMContext):
    await state.clear()
    await admin_panel(message)

# Обработчик выбора жанра
@dp.callback_query(F.data.startswith("genre_"), AdminStates.setting_genre)
async def genre_selected(callback: types.CallbackQuery, state: FSMContext):
    genre = callback.data.split("_")[1]
    DB["current_genre"] = genre
    await callback.message.edit_text(f"✅ Жанр установлен: {genre}")
    await state.clear()
    await admin_panel(callback.message)  # Возврат в админ-панель

# Обработчик выбора стиля
@dp.callback_query(F.data.startswith("style_"), AdminStates.setting_style)
async def style_selected(callback: types.CallbackQuery, state: FSMContext):
    style = callback.data.split("_")[1]
    DB["current_style"] = style
    await callback.message.edit_text(f"✅ Стиль установлен: {style}")
    await state.clear()
    await admin_panel(callback.message)  # Возврат в админ-панель

# Обработчик кнопки "🔙 В меню"
@dp.message(F.text == "🔙 В меню")
async def back_to_menu_handler(message: types.Message, state: FSMContext):
    await state.clear()
    await cmd_start(message)

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

    # Первый ряд
    builder.row(
        KeyboardButton(text="🎭 Сменить жанр"),
        KeyboardButton(text="🖋 Сменить стиль")
    )

    # Второй ряд
    builder.row(
        KeyboardButton(text="⏰ Изменить время"),
        KeyboardButton(text="🚀 Опубликовать сейчас")
    )

    # Третий ряд
    builder.row(
        KeyboardButton(text="📝 Создать рецензию"),
        KeyboardButton(text="🔙 В меню")
    )


    await message.answer(
        status_text,
        reply_markup=builder.as_markup(
            resize_keyboard=True,
            input_field_placeholder="Выберите действие..."
        )
    )

# Обработчик кнопки "📝 Создать рецензию"
@dp.message(F.text == "📝 Создать рецензию")
async def custom_review_start(message: types.Message, state: FSMContext):
    if message.from_user.id not in ADMINS:
        return

    await message.answer(
        "🎬 Введите название фильма или описание для генерации рецензии\:\n"
        "Примеры:\n"
        "\- Крестный отец, криминальная сага о мафии\n"
        "\- Фильм про роботов\-полицейских в будущем мегаполисе\n"
        "❌ Отмена \- \/cancel",
        reply_markup=types.ReplyKeyboardRemove()
    )
    await state.set_state(AdminStates.custom_review)

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