import os
import json
import logging
from typing import Dict, Optional
import aiohttp
from aiogram import Bot, Dispatcher, types
from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
import asyncio
from aiogram.enums import ParseMode
from aiogram.exceptions import TelegramAPIError
from apscheduler.schedulers.asyncio import AsyncIOScheduler
import openai
from aiogram.utils import markdown as md
from dotenv import load_dotenv

load_dotenv()

# Настройка логгера
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Загрузка конфигурации из переменных окружения
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
GOOGLE_API_KEY = os.getenv("GOOGLE_API_KEY")
GOOGLE_CX_ID = os.getenv("GOOGLE_CX_ID")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")

# Инициализация бота и диспетчера
bot = Bot(
    token=os.getenv("TELEGRAM_BOT_TOKEN"),
    default=DefaultBotProperties(
        parse_mode=ParseMode.MARKDOWN_V2,
        link_preview_is_disabled=True
    )
)
dp = Dispatcher()

# Загрузка стилей описаний
with open("styles.json", "r", encoding="utf-8") as f:
    STYLE_DESCRIPTIONS = json.load(f)

# Инициализация планировщика
scheduler = AsyncIOScheduler()
subscribers: Dict[int, bool] = {}

# Конфигурация OpenAI
openai.api_key = OPENAI_API_KEY
SYSTEM_PROMPT = (
    "Ты — профессиональный кинокритик. Анализируй фильмы, "
    "давай анализ сюжета, актерской игры и режиссуры. Сообщение примерно на 120 слов"
)


class APIError(Exception):
    """Базовое исключение для ошибок API"""


async def search_movie_gpt(description: str) -> Optional[str]:
    """Поиск фильма по описанию с помощью GPT-4"""
    try:
        response = await openai.ChatCompletion.acreate(
            model="gpt-4",
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": f"Определи фильм по описанию: {description}"}
            ]
        )
        return response["choices"][0]["message"]["content"].strip()
    except Exception as e:
        logger.error(f"OpenAI error: {str(e)}")
        raise APIError("Ошибка при обработке запроса к OpenAI")


async def generate_review_gpt(movie_title: str) -> str:
    """Генерация рецензии с помощью GPT-4"""
    try:
        response = await openai.ChatCompletion.acreate(
            model="gpt-4",
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": f"Напиши профессиональную рецензию на фильм: {movie_title}"}
            ]
        )
        return response["choices"][0]["message"]["content"].strip()
    except Exception as e:
        logger.error(f"OpenAI error: {str(e)}")
        raise APIError("Ошибка при генерации рецензии")


async def search_trailer_google(movie_title: str) -> Optional[str]:
    """Поиск трейлера через Google Custom Search"""
    url = "https://www.googleapis.com/customsearch/v1"
    params = {
        "key": GOOGLE_API_KEY,
        "cx": GOOGLE_CX_ID,
        "q": f"{movie_title} трейлер",
        "siteSearch": "youtube.com",
        "num": 1
    }

    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(url, params=params) as response:
                if response.status != 200:
                    return None

                data = await response.json()
                if "items" not in data or not data["items"]:
                    return None

                return data["items"][0].get("link")
    except Exception as e:
        logger.error(f"Google Search error: {str(e)}")
        return None

from aiogram.enums import ParseMode


def escape_md(text: str) -> str:
    """Ручное экранирование спецсимволов для MarkdownV2"""
    escape_chars = '_*[]()~`>#+-=|{}.!'
    return ''.join(f'\\{char}' if char in escape_chars else char for char in text)

@dp.message()
async def handle_movie_search(message: types.Message):
    """Обработчик запросов на поиск фильмов"""
    try:
        description = message.text.strip()
        if len(description) < 10:
            await message.reply("✍️ Пожалуйста, введите более подробное описание \(не менее 10 символов\)\.")
            return

        # Поиск фильма
        movie_title = await search_movie_gpt(description)
        if not movie_title:
            await message.reply(md.escape("🔍 Не удалось определить фильм. Попробуйте другое описание."))
            return

        # Генерация рецензии
        review = await generate_review_gpt(movie_title)

        # Поиск трейлера
        trailer_link = await search_trailer_google(movie_title)

        # Формирование ответа с экранированием
        safe_title = escape_md(movie_title)
        safe_review = escape_md(review)

        # Формирование ответа
        response = (
            f"🎬 *{safe_title}*\n\n"
            f"{safe_review}"
        )

        if trailer_link:

            safe_link = escape_md(trailer_link)
            print(safe_link)
            response += f"\n\n🎥 [Смотреть трейлер]({safe_link})"


        await message.reply(
            text=response,
            parse_mode=ParseMode.MARKDOWN_V2
        )

    except APIError as e:
        await message.reply(f"⚠️ {str(e)}")
    except TelegramAPIError as e:
        logger.error(f"Telegram API error: {str(e.message)}")

    except Exception as e:
        logger.error(f"Unexpected error: {str(e)}")
        await message.reply("⚠️ Произошла непредвиденная ошибка. Пожалуйста, попробуйте позже.")

async def main():
    try:
        # Запуск планировщика
        scheduler.start()

        # Запуск бота
        await dp.start_polling(bot)

    except KeyboardInterrupt:
        # Корректное завершение работы
        await shutdown()


async def shutdown():
    """Асинхронное завершение работы"""
    scheduler.shutdown()
    await bot.session.close()
    logger.info("Bot stopped gracefully")


if __name__ == "__main__":
    try:
        # Запускаем асинхронное приложение
        asyncio.run(main())

    except KeyboardInterrupt:
        # Резервное завершение на случай ошибок
        logger.info("Forced shutdown")