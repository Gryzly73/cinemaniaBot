import os
import json
import logging
import asyncio
import aiohttp
from typing import Dict, Optional
from aiogram import Bot, Dispatcher, types, F
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.utils.keyboard import ReplyKeyboardBuilder, InlineKeyboardBuilder
from aiogram.types import ChatLocation, Message # ChatAction
from aiogram.exceptions import TelegramAPIError
from apscheduler.schedulers.asyncio import AsyncIOScheduler
import openai
from dotenv import load_dotenv

load_dotenv()

# Настройка логгера
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Загрузка конфигурации
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
GOOGLE_API_KEY = os.getenv("GOOGLE_API_KEY")
GOOGLE_CX_ID = os.getenv("GOOGLE_CX_ID")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
# print(OPENAI_API_KEY)

# Инициализация бота
bot = Bot(
    token=TELEGRAM_BOT_TOKEN,
    default=DefaultBotProperties(parse_mode=ParseMode.MARKDOWN_V2)
)
dp = Dispatcher()
scheduler = AsyncIOScheduler()


# Состояния
class Form(StatesGroup):
    waiting_description = State()
    choosing_style = State()


# Хранилище данных
user_styles: Dict[int, str] = {}
STYLE_DESCRIPTIONS = {}

# Загрузка стилей
try:
    with open("styles.json", "r", encoding="utf-8") as f:
        STYLE_DESCRIPTIONS = json.load(f)
except Exception as e:
    logger.error(f"Error loading styles: {e}")
    STYLE_DESCRIPTIONS = {"default": "Стандартный стиль рецензии"}


# Утилиты
def escape_md(text: str) -> str:
    """Экранирование спецсимволов MarkdownV2"""
    escape_chars = '_*[]()~`>#+-=|{}.!'
    return ''.join(f'\\{char}' if char in escape_chars else char for char in text)


async def show_typing(func, message: Message):
    """Декоратор для отображения индикатора набора"""
    await bot.send_chat_action(message.chat.id, ChatAction.TYPING)
    return await func(message)


# Меню
async def set_main_menu():
    menu_commands = [
        types.BotCommand(command="/start", description="Главное меню"),
        types.BotCommand(command="/help", description="Помощь"),
        types.BotCommand(command="/style", description="Стиль рецензии")
    ]
    await bot.set_my_commands(menu_commands)


# Обработчики
@dp.message(F.text == "/start")
# @dp.message(CommandStart())
async def cmd_start(message: Message, state: FSMContext):
    await state.clear()
    builder = ReplyKeyboardBuilder()
    builder.row(
        types.KeyboardButton(text="🎬 Найти фильм"),
        types.KeyboardButton(text="🎨 Стиль рецензии")
    )
    builder.row(types.KeyboardButton(text="ℹ️ Помощь"))

    await message.answer(
        escape_md("Добро пожаловать в *CinemaMania*! Выберите действие:"),
        reply_markup=builder.as_markup(resize_keyboard=True),
        parse_mode=ParseMode.MARKDOWN_V2
    )


@dp.message(F.text == "ℹ️ Помощь")
@dp.message(F.text == "/help")
async def cmd_help(message: Message):
    help_text = escape_md(
        "🎥 *Доступные команды:*\n\n"
        "🎬 Найти фильм - Поиск по описанию\n"
        "🎨 Стиль рецензии - Выбор стиля рецензии\n"
        "ℹ️ Помощь - Это сообщение\n\n"
        "Пример запроса: _Фильм про хакера в матрице_"
    )
    await message.answer(help_text, parse_mode=ParseMode.MARKDOWN_V2)


@dp.message(F.text == "🎨 Стиль рецензии")
@dp.message(F.text == "/style")
async def choose_style(message: Message, state: FSMContext):
    builder = InlineKeyboardBuilder()
    for style in STYLE_DESCRIPTIONS:
        builder.button(text=f"🎨 {style}", callback_data=f"style_{style}")
    builder.button(text="🔙 Назад", callback_data="back_main")
    builder.adjust(2)

    await message.answer(
        escape_md("Выберите стиль рецензии:"),
        reply_markup=builder.as_markup(),
        parse_mode=ParseMode.MARKDOWN_V2
    )
    await state.set_state(Form.choosing_style)


@dp.callback_query(F.data.startswith("style_"))
async def style_selected(callback: types.CallbackQuery, state: FSMContext):
    style = callback.data.split("_")[1]
    user_styles[callback.from_user.id] = style
    await callback.message.edit_text(
        escape_md(f"✅ Выбран стиль: *{style}*\n{STYLE_DESCRIPTIONS[style]}"),
        parse_mode=ParseMode.MARKDOWN_V2
    )
    await callback.answer()


@dp.callback_query(F.data == "back_main")
async def back_button(callback: types.CallbackQuery, state: FSMContext):
    await state.clear()
    await callback.message.delete()
    await cmd_start(callback.message, state)


@dp.message(F.text == "🎬 Найти фильм")
async def start_search(message: Message, state: FSMContext):
    await state.set_state(Form.waiting_description)
    await message.answer(
        escape_md("🔍 Введите описание фильма:"),
        reply_markup=types.ReplyKeyboardRemove(),
        parse_mode=ParseMode.MARKDOWN_V2
    )


# Основная логика
@dp.message(Form.waiting_description)
# @show_typing
async def handle_movie_search(message: Message, state: FSMContext):
    try:
        progress_msg = await message.answer(escape_md("🔄 Поиск... 0%"))

        # Поиск фильма
        movie_title = await search_movie_gpt(message.text)
        if not movie_title:
            raise APIError("Фильм не найден")

        await progress_msg.edit_text(escape_md("🔄 Поиск... 50%"))

        # Генерация рецензии
        style = user_styles.get(message.from_user.id, "default")
        review = await generate_review_gpt(movie_title, style)

        # Поиск трейлера
        trailer_link = await search_trailer_google(movie_title)

        # Формирование ответа
        response = format_response(movie_title, review, trailer_link)

        await progress_msg.delete()
        await message.answer(response, parse_mode=ParseMode.MARKDOWN_V2)

    except Exception as e:
        await message.answer(escape_md(f"⚠️ Ошибка: {str(e)}"))
    finally:
        await state.clear()


def format_response(title: str, review: str, trailer: Optional[str]) -> str:
    """Форматирование ответа с экранированием"""
    safe_title = escape_md(title)
    safe_review = escape_md(review)

    response = f"🎬 *{safe_title}*\n\n{safe_review}"

    if trailer:
        safe_trailer = escape_md(trailer)
        response += f"\n\n🎥 [Смотреть трейлер]({safe_trailer})"

    return response


# Сервисные функции
async def search_movie_gpt(description: str) -> Optional[str]:
    """Поиск фильма через GPT-4"""
    try:
        response = await openai.ChatCompletion.acreate(
            api_key=OPENAI_API_KEY,
            model="gpt-4",
            messages=[{
                "role": "user",
                "content": f"Определи фильм по описанию: {description}"
            }]
        )
        return response.choices[0].message.content.strip()
    except Exception as e:
        logger.error(f"OpenAI Error: {e}")
        raise APIError("Ошибка поиска фильма")


async def generate_review_gpt(title: str, style: str) -> str:
    """Генерация рецензии через GPT-4"""
    try:
        style_prompt = STYLE_DESCRIPTIONS.get(style, "")
        response = await openai.ChatCompletion.acreate(
            api_key=OPENAI_API_KEY,
            model="gpt-4",
            messages=[{
                "role": "system",
                "content": f"Напиши рецензию в стиле: {style_prompt}"
            }, {
                "role": "user",
                "content": f"Фильм: {title}"
            }]
        )
        return response.choices[0].message.content.strip()
    except Exception as e:
        logger.error(f"OpenAI Error: {e}")
        raise APIError("Ошибка генерации рецензии")


async def search_trailer_google(title: str) -> Optional[str]:
    """Поиск трейлера через Google API"""
    try:
        async with aiohttp.ClientSession() as session:
            params = {
                "key": GOOGLE_API_KEY,
                "cx": GOOGLE_CX_ID,
                "q": f"{title} трейлер",
                "siteSearch": "youtube.com"
            }
            async with session.get(
                    "https://www.googleapis.com/customsearch/v1",
                    params=params
            ) as response:
                data = await response.json()
                return data["items"][0]["link"] if "items" in data else None
    except Exception as e:
        logger.error(f"Google Search Error: {e}")
        return None


# Запуск
async def main():
    await set_main_menu()
    scheduler.start()
    await dp.start_polling(bot)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("Bot stopped")