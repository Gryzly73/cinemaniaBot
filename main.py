import os
import json
import logging
import asyncio
import aiohttp
import re
import requests
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
from functools import lru_cache

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
    review_ready = State()  # новое состояние после генерации рецензии

# Загрузка стилей рецензий
try:
    with open("styles.json", "r", encoding="utf-8") as f:
        STYLE_DESCRIPTIONS = json.load(f)
except Exception as e:
    logger.error(f"Error loading styles: {e}")
    STYLE_DESCRIPTIONS = {"humorous": "Стандартный стиль рецензии"}

# OpenAI функции
openai.api_key = OPENAI_API_KEY

MOVIE_PROMPT = """Сгенерируй описание фильма в жанре {genre} в формате:
Title: Название
Year: Год
IMDB-ID: ttXXXXXX \(действительный идентификатор с IMDB\)
Plot: Краткое описание на русском языке - без многоточий на конце предложений.
Избегай многоточий и повторяющихся знаков препинания
Избегай фильмов с этими ID: {avoid_ids}
Только действительные существующие фильмы\!"""

GENERAL_REVIEW_PROMPT = os.getenv("GENERAL_REVIEW_PROMPT", "Стандартные требования к рецензии")

# Утилиты
def escape_md(text: str) -> str:
    escape_chars = '_*[]()~`>#+-=|{}.!'
    return re.sub(f'([{"".join(re.escape(c) for c in escape_chars)}])', r'\\\1', str(text))

def time_to_cron(user_time: str) -> str:
    error_msg = (
        "Неправильный формат времени\!\n"
        "Используйте ЧЧ:ММ \(например 09:30\)\n"
        "Диапазон времени: 00:00 \- 23:59"
    )

    if not re.match(r"^([0-1]?[0-9]|2[0-3]):[0-5][0-9]$", user_time):
        raise ValueError(error_msg)  # Более подробное сообщение

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

# --- Универсальные обработчики ---
@dp.message(F.text.in_(["🔙 В меню", "/admin"]))
async def return_to_admin_menu(message: types.Message, state: FSMContext):
    """Обработчик возврата в админ-панель из любого места"""
    await state.clear()
    await admin_panel(message)

def admin_menu_keyboard() -> ReplyKeyboardMarkup: #
    builder = ReplyKeyboardBuilder()
    # Основные кнопки
    builder.row(KeyboardButton(text="🎭 Сменить жанр"))
    builder.row(KeyboardButton(text="🖋 Сменить стиль"))
    builder.row(KeyboardButton(text="⏰ Изменить время"))
    # Кнопка возврата внизу
    builder.row(KeyboardButton(text="🔙 В меню"))
    return builder.as_markup(resize_keyboard=True)

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

@lru_cache(maxsize=100)
async def get_cached_movie(genre: str, attempt: int):
    return await get_movie_data(genre, attempt)

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
            temperature=0.5,
            max_tokens=1000
        )
        return response.choices[0].message.content
    except Exception as e:
        logger.error(f"Ошибка генерации: {str(e)}")
        return "Рецензия временно недоступна"

# Обновлённая функция генерации фильмов
async def get_movie_data(genre: str, attempt: int = 0, used_ids: list = None):
    if attempt >= 3:
        return None
    if used_ids is None:
        used_ids = []

    try:
        # Экранируем и форматируем ID
        safe_ids = [id.replace('_', r'\_') for id in used_ids]
        avoid_ids = ", ".join(safe_ids[-50:]) if safe_ids else "нет запрещённых ID"

        full_prompt = MOVIE_PROMPT.format(
            genre=escape_md(genre),
            avoid_ids=avoid_ids
        )

        response = await openai.ChatCompletion.acreate(
            model="gpt-4",
            messages=[{"role": "user", "content": full_prompt}],
            temperature=0.7 + attempt * 0.1
        )

        raw_text = response.choices[0].message.content
        movie = parse_movie_response(raw_text)

        if not movie or movie["imdb_id"] in used_ids:
            return await get_movie_data(genre, attempt+1, used_ids)

        return movie

    except Exception as e:
        logger.error(f"Попытка {attempt + 1} неудачна: {str(e)}")
        return await get_movie_data(genre, attempt+1, used_ids)

# МЕДИА-ФУНКЦИИ
def get_movie_poster(movie_data: dict) -> Optional[str]:
    omdb_api_key = os.getenv("OMDB_API_KEY")

    # Пытаемся найти по IMDB ID для обычных фильмов
    if movie_data["imdb_id"].startswith("tt"):
        url = f"http://www.omdbapi.com/?i={movie_data['imdb_id']}&apikey={omdb_api_key}"
    else:  # Для кастомных рецензий ищем по названию и году
        url = (f"http://www.omdbapi.com/?t={movie_data['title']}"
               f"&y={movie_data['year']}&apikey={omdb_api_key}")

    try:
        response = requests.get(url)
        if response.ok:
            data = response.json()
            if data.get('Response') == 'True':
                return data.get("Poster") if data.get("Poster") != "N/A" else None
    except Exception as e:
        logger.error(f"Ошибка получения постера: {e}")
    return None

async def get_movie_media(imdb_id: str) -> dict:
    omdb_api_key = os.getenv("OMDB_API_KEY")
    url = f"http://www.omdbapi.com/?i={imdb_id}&apikey={omdb_api_key}"

    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(url) as response:
                data = await response.json()
                return {
                    "poster": data.get('Poster'),
                    "trailer": f"https://www.imdb.com/title/{imdb_id}/videogallery"
                }
    except Exception as e:
        logger.error(f"Ошибка получения медиа: {str(e)}")
        return {}

# Основная логика публикации
async def send_post_with_media(movie: dict, review: str):
    movie_data = {
        "imdb_id": movie["imdb_id"],
        "title": movie['title'],
        "year": movie['year']
    }

    poster_url = get_movie_poster(movie_data)

    # Экранируем ВСЕ динамические данные
    escaped_title = escape_md(movie['title'])
    escaped_year = escape_md(str(movie['year']))
    escaped_genre = escape_md(DB['current_genre'])
    escaped_style = escape_md(DB['current_style'])
    escaped_review = escape_md(review)

    # Формируем текст с правильным экранированием
    caption = (
        f"🎬 *{escaped_title}* \\({escaped_year}\\)\n\n"
        f"📖 Жанр: {escaped_genre}\n"
        f"📝 Рецензия \\({escaped_style}\\):\n{escaped_review}"
    )
    logger.info(f"Подпись: {caption} ")
    logger.info(f"Длина подписи: {len(caption)} символов")
    if poster_url:
        await bot.send_photo(
            chat_id=CHANNEL_ID,
            photo=poster_url,
            caption=caption,
            parse_mode=ParseMode.MARKDOWN_V2
        )
    else:
        await bot.send_message(
            CHANNEL_ID,
            text=caption,
            parse_mode=ParseMode.MARKDOWN_V2
        )

async def publish_scheduled_post_with_movie(movie: dict):
    try:
        review = await generate_review(movie)
        await send_post_with_media(movie, review)
        DB["posted_imdb_ids"].append(movie["imdb_id"])
        save_to_history(movie)
    except Exception as e:
        logger.error(f"Ошибка публикации: {str(e)}")
        await notify_admin(f"🔥 Ошибка публикации: {str(e)}")

# ОБРАБОТКА ДУБЛИКАТОВ
async def handle_duplicate(movie: dict):
    logger.warning(f"Дубликат IMDB ID: {movie['imdb_id']}")
    used_ids = DB["posted_imdb_ids"][-100:]  # Берем последние 100 ID
    new_movie = await get_movie_data(DB["current_genre"], used_ids=used_ids)

    if new_movie and new_movie["imdb_id"] not in DB["posted_imdb_ids"]:
        await publish_scheduled_post_with_movie(new_movie)
    else:
        await notify_admin(f"⚠️ Не удалось найти уникальный фильм после дубликата {movie['imdb_id']}")

# СУЩЕСТВУЮЩИЕ ФУНКЦИИ ПУБЛИКАЦИИ
async def publish_scheduled_post():
    used_ids = DB["posted_imdb_ids"][-100:]  # Последние 100 фильмов
    movie = await get_movie_data(DB["current_genre"], used_ids=used_ids)

    if not movie:
        await notify_admin("❌ Не удалось получить данные фильма\!")
        return
   # await publish_scheduled_post_with_movie(movie) #

    if movie["imdb_id"] in DB["posted_imdb_ids"]:
        await handle_duplicate(movie)
        return

    try:
        review = await generate_review(movie)

        # Получаем данные для медиа
        movie_data = {
            "imdb_id": movie["imdb_id"],
            "title": movie['title'],
            "year": movie['year']
        }

        # Используем ту же логику, что и в ручной публикации
        poster_url = get_movie_poster(movie_data)

        # Формируем текст поста
        escaped_title = escape_md(movie['title'])
        escaped_year = escape_md(str(movie['year']))
        escaped_genre = escape_md(DB['current_genre'])
        escaped_style = escape_md(DB['current_style'])
        escaped_review = escape_md(review)

        caption = (
            f"🎬 *{escaped_title}* \\({escaped_year}\\)\n\n"
            f"📖 Жанр: {escaped_genre}\n"
            f"📝 Рецензия \\({escaped_style}\\):\n{escaped_review}"
        )
        logger.info(f"Подпись: {caption} ")
        logger.info(f"Длина подписи: {len(caption)} символов")

        # Отправка с постером или без
        if poster_url:
            await bot.send_photo(
                chat_id=CHANNEL_ID,
                photo=poster_url,
                caption=caption,
                parse_mode=ParseMode.MARKDOWN_V2
            )
        else:
            await bot.send_message(
                CHANNEL_ID,
                text=caption,
                parse_mode=ParseMode.MARKDOWN_V2
            )

        DB["posted_imdb_ids"].append(movie["imdb_id"])
        save_to_history(movie)

    except Exception as e:
        logger.error(f"Ошибка публикации: {str(e)}")
        await notify_admin(f"🔥 Ошибка публикации: {str(e)}")

# ОБРАБОТЧИКИ СООБЩЕНИЙ

@dp.message(F.text == "/start")
async def cmd_start(message: types.Message):
    if message.from_user.id in ADMINS:
        # Прямой переход в админ-панель для администраторов
        await admin_panel(message)
    else:
        # Стандартное меню для обычных пользователей
        markup = ReplyKeyboardMarkup(
            keyboard=[
                [KeyboardButton(text="🎬 Найти фильм")],
                [KeyboardButton(text="ℹ️ Помощь")]
            ],
            resize_keyboard=True
        )
        await message.answer(
            "Добро пожаловать в CinemaBot\! 🍿\nВыберите действие:",
            reply_markup=markup
        )

# Модифицированный обработчик публикации
@dp.message(F.text == "🚀 Опубликовать сейчас")
async def publish_now_handler(message: types.Message, state: FSMContext):
    logger.warning("start")
    if message.from_user.id not in ADMINS:
        return
    logger.warning("admin ok!")

    data = await state.get_data()
    movie = data.get('movie')
    review = data.get('review')
    logger.info("movie")
    logger.info(movie)
    if movie and review:
        try:
            # Получаем данные для поиска постера
            movie_data = {
                "imdb_id": movie["imdb_id"],
                "title": movie['title'],
                "year": movie['year']
            }

            poster_url = get_movie_poster(movie_data)
            logger.warning("Poster url")
            logger.warning(poster_url)

            # Экранирование текста
            escaped_title = escape_md(movie['title'])
            escaped_year = escape_md(str(movie['year']))
            escaped_style = escape_md(DB['current_style'])
          #  escaped_plot = escape_md(movie['plot'])
            escaped_genre= "Выбор пользователя"
            escaped_review = escape_md(review)

            caption = (
                f"🎬 *{escaped_title}* \\({escaped_year}\\)\n\n"
                f"📖 Жанр: {escaped_genre}\n"
                f"📚 Сюжет: {escape_md(movie['plot'])[:200]}\n\n"
                f"📝 Рецензия \\({escaped_style}\\):\n{escaped_review}"
            )

            # Отправка поста с постером или без
            if poster_url:
                await bot.send_photo(
                    chat_id=CHANNEL_ID,
                    photo=poster_url,
                    caption=caption,
                    parse_mode=ParseMode.MARKDOWN_V2
                )
            else:
                await bot.send_message(
                    CHANNEL_ID,
                    text=caption,
                    parse_mode=ParseMode.MARKDOWN_V2
                )

            # Сохранение в историю
            save_to_history({
                "imdb_id": movie["imdb_id"],
                "title": movie['title'],
                "year": movie['year'],
                "plot": movie.get('plot', '')
            })

            await message.answer("✅ Рецензия опубликована\!")
        except Exception as e:
            logger.error(f"Ошибка публикации: {str(e)}")
            await message.answer(f"⚠️ Ошибка публикации: {str(e)}")
        finally:
            await state.clear()
    else:
        await message.answer("⚠️ Нет рецензии для публикации\!")

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


# Добавляем новый обработчик для состояния установки времени
@dp.message(AdminStates.setting_schedule)
async def process_schedule_time(message: types.Message, state: FSMContext):
    try:
        user_time = message.text.strip()
        cron_expression = time_to_cron(user_time)

        # Обновляем расписание
        DB["schedule"] = cron_expression

        # Перезапускаем задание планировщика
        scheduler.remove_job('publish_job')  # Удаляем старое задание
        scheduler.add_job(
            publish_scheduled_post,
            trigger='cron',
            **parse_cron(cron_expression),
            id='publish_job'
        )

        await message.answer(
            f"✅ Время публикации установлено: {user_time}",
            reply_markup=types.ReplyKeyboardRemove()
        )
        await state.clear()
        await admin_panel(message)  # Возвращаем в админ-панель с обновленными данными

    except ValueError as e:
        # await message.answer(f"❌ Ошибка: {e}\nПопробуйте еще раз в формате ЧЧ:ММ")
        await message.answer(
            f"❌ {str(e)}\nИспользуйте формат ЧЧ:ММ \(например 09:30\)",
            reply_markup=admin_menu_keyboard()  # Клавиатура с кнопкой "Назад"
        )
        await state.clear()
        await admin_panel(message)  # Возврат в админ-панель

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

# ... другие обработчики ...
# Уведомления админа
async def notify_admin(message: str):
    for admin in ADMINS:
        await bot.send_message(admin, message)

async def verify_imdb_id(imdb_id: str) -> bool:
    omdb_api_key = os.getenv("OMDB_API_KEY")
    url = f"http://www.omdbapi.com/?i={imdb_id}&apikey={omdb_api_key}"

    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(url) as response:
                data = await response.json()
                return data.get('Response') == 'True'
    except Exception as e:
        logger.error(f"Ошибка верификации IMDB ID: {str(e)}")
        return False

# Изменения в функции generate_custom_review и добавление parse_custom_review
def parse_custom_review(text: str) -> Optional[dict]:
    try:
        title_match = re.search(r'Title:\s*(.+)', text, re.IGNORECASE)
        year_match = re.search(r'Year:\s*(\d{4})', text, re.IGNORECASE)
        imdb_match = re.search(r'IMDB-ID:\s*(tt\d{7,8})', text, re.IGNORECASE)
        plot_match = re.search(r'Plot:\s*(.+)', text, re.IGNORECASE | re.DOTALL)
        review_match = re.search(r'Review:\s*(.+)$', text, re.IGNORECASE | re.DOTALL)

        title = title_match.group(1).strip() if title_match else "Неизвестный фильм"
        year = int(year_match.group(1)) if year_match else datetime.now().year
        plot = plot_match.group(1).strip() if plot_match else ""
        review = review_match.group(1).strip() if review_match else ""

        # Проверяем наличие корректного IMDB ID
        imdb_id = ""
        if imdb_match:
            extracted = imdb_match.group(1).strip()
            if re.match(r'^tt\d{7,8}$', extracted):
                imdb_id = extracted

        return {
            "title": title,
            "year": year,
            "plot": plot,
            "review": review,
            "imdb_id": imdb_id
        }
    except Exception as e:
        logger.error(f"Ошибка парсинга кастомной рецензии: {str(e)}")
        return None

async def generate_custom_review(query: str) -> Optional[dict]:
    system_prompt = (
        f"{GENERAL_REVIEW_PROMPT}\n"
        f"Стиль: {DB['current_style']}\n"
        "Учти: пользователь мог ввести название, концепцию или краткое описание\!\n"
        "Формат ответа:\n"
        "Title: Название фильма\n"
        "Year: Год выпуска\n"
        "IMDB-ID: ttXXXXXXX\n"
        "Plot: Описание сюжета на 20-30 слов - ни в коем случае не ставь несколько точек рядом, не ставь нигде многоточия, обязательно заканчивай описание одной точкой\n"
        "Review: Текст рецензии 100-120 слов - не ставь нигде многоточия\n\n"
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
            temperature=0.5,
            max_tokens=1500
        )
        raw_text = response.choices[0].message.content
        logger.warning(raw_text)
        return parse_custom_review(raw_text)
    except Exception as e:
        logger.error(f"Ошибка генерации кастомной рецензии: {str(e)}")
        return None

@dp.message(AdminStates.custom_review)
async def process_custom_review(message: types.Message, state: FSMContext):
    try:
        review_data = await generate_custom_review(message.text)

        if not review_data:
         #   Создаем  клавиатуру
            builder = ReplyKeyboardBuilder()
            builder.row(KeyboardButton(text="🔙 В меню"))
            await message.answer(
                "❌ Не удалось распознать фильм. Пожалуйста, проверьте название и попробуйте ввести его еще раз или вернитесь в меню.",
                reply_markup=builder.as_markup(resize_keyboard=True)
            )
            return  # Состояние не очищается, пользователь остается в custom_review
        # Верификация IMDB ID
        is_valid = await verify_imdb_id(review_data["imdb_id"])

        if not is_valid:
            await message.answer("⚠️ Недействительный IMDB ID\! Постер не будет сформирован\!\n")
         #   return!

        logger.warning(review_data["review"])

        # Сохраняем ВСЕ данные фильма включая IMDB ID
        await state.update_data(
            movie=review_data,  # содержит imdb_id
            review=review_data["review"],
            imdb_id=review_data["imdb_id"]  # явное сохранение ID
        )
        await state.set_state(AdminStates.review_ready)
        logger.warning("Ok!")

        # Показываем превью
        builder = ReplyKeyboardBuilder()
        builder.row(KeyboardButton(text="🚀 Опубликовать сейчас"))
        builder.row(KeyboardButton(text="📝 Еще рецензия"), KeyboardButton(text="🔙 В админку"))

        await message.answer(
            f"✅ Найден фильм:\n\n"
          #  f"🎬 {escape_md(review_data['title'])} \({review_data['year']}\)\n"
            f"🎬 {escape_md(review_data['title'])} \\({escape_md(str(review_data['year']))}\\)\n"
            f"📚 Сюжет: {escape_md(review_data['plot'])[:200]}\n\n"
            f"📝 Рецензия:\n{escape_md(review_data['review'])[:500]}",
            reply_markup=builder.as_markup()
        )

    except Exception as e:
        logger.error(f"Ошибка: {str(e)}")
        await message.answer("❌ Произошла ошибка, попробуйте снова")
        await state.clear()

@dp.message(F.text.startswith("tt") and AdminStates.review_ready)
async def handle_manual_imdb_input(message: types.Message, state: FSMContext):
    # Проверка команды возврата
    if message.text.lower() in ["меню", "/admin", "🔙 в меню"]:
        await state.clear()
        await admin_panel(message)
        return

    data = await state.get_data()
    current_imdb = data.get('imdb_id', '')  # получаем текущий ID из состояния

   # imdb_id = message.text.strip()
    imdb_id = current_imdb

    logger.warning("imdb")
    logger.warning(message.text)
    logger.warning(message.text.strip())


    # Проверка формата
    if not re.match(r"^tt\d{7,8}$", imdb_id):
        await message.answer(
            "❌ Неверный формат IMDB ID. Пример: tt12345678\nПопробуйте снова:",
            reply_markup=return_kb.as_markup(resize_keyboard=True)
        )
        return

    # Проверка существования фильма
    is_valid = await verify_imdb_id(imdb_id)
    if not is_valid:
        await message.answer(
            "❌ Фильм с таким ID не найден\nПопробуйте другой ID:",
            reply_markup=return_kb.as_markup(resize_keyboard=True)
        )
        return

    # Обновляем данные
    new_data = data['movie'].copy()
    new_data['imdb_id'] = imdb_id
    await state.update_data(movie=new_data, imdb_id=imdb_id)

    # Клавиатура после успешного обновления
    success_kb = ReplyKeyboardBuilder()
    success_kb.row(KeyboardButton(text="🚀 Опубликовать сейчас"))
    success_kb.row(KeyboardButton(text="🔙 В меню"))

    await message.answer(
        f"✅ IMDB ID обновлен:\n"
        f"Новый ID: {imdb_id}\n\n"
        f"Выберите действие:",
        reply_markup=success_kb.as_markup(resize_keyboard=True)
    )

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

async def admin_panel(message: types.Message):
    if message.from_user.id not in ADMINS:
        print("Id admin:")
        print(message.from_user.id)
        print(ADMINS)
        await message.answer("⛔ Доступ запрещен\!")
        return

    try:
        cron_parts = DB["schedule"].split()
        current_time = f"{cron_parts[1]}:{cron_parts[0]}"
    except:
        current_time = "⏰ Не установлено"

    status_text = (
        f"⚙️ *{escape_md('Админ-панель')}*\n\n"  # Экранируем статический текст
        f"▫️ Жанр: {escape_md(DB['current_genre'])}\n"
        f"▫️ Стиль: {escape_md(DB['current_style'])}\n"
        f"▫️ Время: {escape_md(current_time)}\n\n"
        f"Опубликовано фильмов: {escape_md(str(len(DB['posted_imdb_ids'])))}"  # Число тоже экранируем
    )
    logger.debug(f"Raw text before sending: {status_text}")
    builder = ReplyKeyboardBuilder()
    builder.row(
        KeyboardButton(text="🎭 Сменить жанр"),
        KeyboardButton(text="🖋 Сменить стиль")
    )
    builder.row(
        KeyboardButton(text="⏰ Изменить время"),
        KeyboardButton(text="🚀 Опубликовать сейчас")
    )
    builder.row(
        KeyboardButton(text="📝 Создать рецензию"),
        KeyboardButton(text="🔙 В меню")
    )

    await message.answer(
        status_text,
        reply_markup=builder.as_markup(
            resize_keyboard=True,
            input_field_placeholder="Выберите действие\.\.\."
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

# Новый обработчик для некорректного ввода в админ-панели
@dp.message(F.from_user.id.in_(ADMINS))
async def handle_admin_invalid_input(message: types.Message, state: FSMContext):
    current_state = await state.get_state()
    allowed_commands = [
        "🎭 Сменить жанр", "🖋 Сменить стиль", "⏰ Изменить время",
        "🚀 Опубликовать сейчас", "📝 Создать рецензию", "🔙 В меню",
        "/start", "/admin"
    ]

    # Если пользователь не в состоянии и ввел неизвестную команду
    if current_state is None and message.text not in allowed_commands:
       # await message.answer("Пожалуйста, выберите вариант из списка ниже\.")
        await message.answer("ℹ️ Пожалуйста, выберите вариант из списка ниже\.")
        await admin_panel(message)  # <-- Добавляем вызов админ-панели

# Остальные обработчики и запуск
async def main():

    # Загрузка истории при старте
    history = load_history()
    DB["posted_imdb_ids"] = [m["imdb_id"] for m in history[-500:]]

    # Инициализация планировщика с ID задания
    scheduler.add_job(
        publish_scheduled_post,
        trigger='cron',
        **parse_cron(DB['schedule']),
        id='publish_job'  # Добавляем идентификатор задания
    )
    scheduler.start()

    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())