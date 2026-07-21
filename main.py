import asyncio
import logging
import os
import time
from typing import Dict, List

from aiogram import Bot, Dispatcher, F, types
from aiogram.filters import CommandStart, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import (
    KeyboardButton,
    Message,
    ReplyKeyboardMarkup,
    WebAppInfo,
)
import httpx
from dotenv import load_dotenv

# Загружаем переменные из .env файла
load_dotenv()

TOKEN = os.getenv("BOT_TOKEN")
ADMIN_ID = int(os.getenv("ADMIN_ID", 0))
SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_ANON_KEY")

COOLDOWN_SECONDS = 24 * 3600
MINI_APP_URL = "https://sqwerxx.github.io/rossonygazeta/"
CHANNEL_URL = "https://t.me/ronlinetest"

bot = Bot(token=TOKEN)
dp = Dispatcher(storage=MemoryStorage())

user_cooldowns: Dict[int, float] = {}
media_group_buffer: Dict[str, dict] = {}

class SuggestionState(StatesGroup):
    waiting_for_content = State()

async def upload_photo_to_supabase(photo: types.PhotoSize, file_name: str) -> str:
    file_info = await bot.get_file(photo.file_id)
    file_bytes = await bot.download_file(file_info.file_path)

    upload_url = f"{SUPABASE_URL}/storage/v1/object/news-images/{file_name}"
    headers = {
        "apikey": SUPABASE_KEY,
        "Authorization": f"Bearer {SUPABASE_KEY}",
        "Content-Type": "image/jpeg"
    }

    async with httpx.AsyncClient() as client:
        response = await client.post(upload_url, headers=headers, content=file_bytes.read())
        if response.status_code in (200, 201):
            return f"{SUPABASE_URL}/storage/v1/object/public/news-images/{file_name}"
        else:
            logging.error(f"Ошибка загрузки фото в Supabase Storage: {response.text}")
    return None

async def save_post_to_supabase(text_content: str, picture_urls: List[str]):
    url = f"{SUPABASE_URL}/rest/v1/posts"
    headers = {
        "apikey": SUPABASE_KEY,
        "Authorization": f"Bearer {SUPABASE_KEY}",
        "Content-Type": "application/json",
        "Prefer": "return=minimal"
    }
    
    default_reactions = { "🔥": 0, "👍": 0, "❤️": 0, "😂": 0, "😍": 0, "😢": 0, "😡": 0, "👎": 0 }
    
    payload = {
        "text": text_content,
        "pictures": picture_urls,
        "reactions": default_reactions
    }

    async with httpx.AsyncClient() as client:
        response = await client.post(url, headers=headers, json=payload)
        if response.status_code >= 400:
            logging.error(f"Ошибка вставки поста в Supabase REST: {response.text}")
            return False
    return True

async def process_saved_channel_post(
    post_id: int,
    text_content: str,
    photos: List[types.PhotoSize] = None
):
    saved_media_urls = []

    if photos:
        for idx, photo in enumerate(photos):
            filename = f"post_{post_id}_{idx}_{int(time.time())}.jpg"
            uploaded_url = await upload_photo_to_supabase(photo, filename)
            if uploaded_url:
                saved_media_urls.append(uploaded_url)

    success = await save_post_to_supabase(text_content, saved_media_urls)

    if success:
        try:
            await bot.send_message(
                chat_id=ADMIN_ID,
                text=(
                    f"⚡ **Новая публикация из канала успешно загружена в Supabase!**\n\n"
                    f"📝 **Текст:** {text_content[:100]}...\n"
                    f"🖼 **Загружено фото:** {len(saved_media_urls)} шт.\n\n"
                    f"🌐 Новость уже отображается на сайте!"
                ),
                parse_mode="Markdown"
            )
        except Exception as e:
            logging.error(f"Ошибка отправки отчета админу: {e}")

@dp.channel_post()
async def handle_channel_post(message: Message):
    post_id = message.message_id
    text = message.text or message.caption or ""

    if message.media_group_id:
        group_id = message.media_group_id
        if group_id not in media_group_buffer:
            media_group_buffer[group_id] = {
                "post_id": post_id,
                "text": text,
                "photos": [],
                "task": None
            }

        if message.photo:
            media_group_buffer[group_id]["photos"].append(message.photo[-1])
        if text and not media_group_buffer[group_id]["text"]:
            media_group_buffer[group_id]["text"] = text

        if media_group_buffer[group_id]["task"]:
            media_group_buffer[group_id]["task"].cancel()

        async def delayed_process(gid: str):
            await asyncio.sleep(2.0)
            data = media_group_buffer.pop(gid, None)
            if data:
                await process_saved_channel_post(
                    post_id=data["post_id"],
                    text_content=data["text"],
                    photos=data["photos"]
                )

        media_group_buffer[group_id]["task"] = asyncio.create_task(delayed_process(group_id))
    else:
        photos = [message.photo[-1]] if message.photo else []
        await process_saved_channel_post(
            post_id=post_id,
            text_content=text,
            photos=photos
        )

def get_main_keyboard() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="🌐 Открыть сайт газеты", web_app=WebAppInfo(url=MINI_APP_URL))],
            [KeyboardButton(text="🗞 Предложить новость"), KeyboardButton(text="📢 Арендовать рекламу")],
            [KeyboardButton(text="ℹ️ О боте и канале")]
        ],
        resize_keyboard=True,
        input_field_placeholder="Выберите действие ниже 👇"
    )

def get_cancel_keyboard() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[[KeyboardButton(text="❌ Отмена")]],
        resize_keyboard=True,
        input_field_placeholder="Отправьте материал или нажмите отмену..."
    )

@dp.message(CommandStart())
async def cmd_start(message: Message, state: FSMContext):
    await state.clear()
    first_name = message.from_user.first_name if message.from_user else "друг"
    welcome_text = (
        f"Здарова, **{first_name}**! 👋\n\n"
        f"Добро пожаловать в официальный цифровой бот канала **[Россоны Онлайн]({CHANNEL_URL})** "
        f"и газеты **«Голос Россонщины»**.\n\n"
        "Здесь ты можешь предложить свою новость, разместить объявление "
        "или почитать свежий выпуск прямо в Telegram!"
    )
    await message.answer(welcome_text, reply_markup=get_main_keyboard(), parse_mode="Markdown", disable_web_page_preview=True)

@dp.message(F.text == "ℹ️ О боте и канале")
async def cmd_about(message: Message):
    about_text = (
        "📌 **Информация о проекте «Голос Россонщины»**\n\n"
        f"📢 **Наш Telegram-канал:** [Россоны Онлайн]({CHANNEL_URL})\n"
        f"🌐 **Наш сайт / Mini App:** [Открыть приложение]({MINI_APP_URL})\n\n"
        "🤖 **Функционал бота:**\n"
        "• Быстрый доступ к сайту газеты\n"
        "• Отправка новостей и инсайдов редактору\n"
        "• Заказ рекламы и коммерческих объявлений\n\n"
        "Оставайся в курсе главных событий района! 📰🔥"
    )
    await message.answer(about_text, reply_markup=get_main_keyboard(), parse_mode="Markdown", disable_web_page_preview=True)

async def check_and_start_suggestion(message: Message, state: FSMContext, category_tag: str, category_name: str):
    user_id = message.from_user.id
    current_time = time.time()
    
    if user_id in user_cooldowns:
        last_sent = user_cooldowns[user_id]
        time_passed = current_time - last_sent
        if time_passed < COOLDOWN_SECONDS:
            remaining = COOLDOWN_SECONDS - time_passed
            hours, minutes = int(remaining // 3600), int((remaining % 3600) // 60)
            await message.answer(
                f"⏳ **Защита от спама!**\n\nВы уже отправляли предложение недавно.\nСледующая отправка через **{hours} ч. {minutes} мин.**",
                reply_markup=get_main_keyboard(), parse_mode="Markdown"
            )
            return

    await state.update_data(category_tag=category_tag, category_name=category_name)
    await state.set_state(SuggestionState.waiting_for_content)
    
    prompt_text = (
        f"📝 **Вы выбрали:** {category_name}\n\n"
        f"Пришлите текст сообщения, подробное описание, фото или контактные данные.\n"
        f"Администратор получит ваше сообщение и свяжется с вами при необходимости.\n\n"
        f"_Если передумали — нажмите кнопку «❌ Отмена» ниже._"
    )
    await message.answer(prompt_text, reply_markup=get_cancel_keyboard(), parse_mode="Markdown")

@dp.message(F.text == "🗞 Предложить новость")
async def process_suggest_news(message: Message, state: FSMContext):
    await check_and_start_suggestion(message, state, "#НОВОСТЬ", "Предложить новость")

@dp.message(F.text == "📢 Арендовать рекламу")
async def process_suggest_ads(message: Message, state: FSMContext):
    await check_and_start_suggestion(message, state, "#РЕКЛАМА", "Аренда рекламы / Объявление")

@dp.message(StateFilter(SuggestionState.waiting_for_content), F.text == "❌ Отмена")
async def cancel_suggestion(message: Message, state: FSMContext):
    await state.clear()
    await message.answer("❌ Отправка отменена. Возвращаемся в главное меню.", reply_markup=get_main_keyboard())

@dp.message(StateFilter(SuggestionState.waiting_for_content))
async def receive_suggestion_content(message: Message, state: FSMContext):
    data = await state.get_data()
    category_tag = data.get("category_tag", "#ОБРАЩЕНИЕ")
    category_name = data.get("category_name", "Обращение")
    
    user = message.from_user
    user_id = user.id
    username_str = f"@{user.username}" if user.username else "Отсутствует"
    full_name = user.full_name or "Пользователь"
    
    admin_header = (
        f"📩 **НОВОЕ ПОСТУПЛЕНИЕ** [{category_tag}]\n"
        f"───────────────────\n"
        f"👤 **Отправитель:** {full_name}\n"
        f"🏷 **Юзернейм:** {username_str}\n"
        f"🆔 **User ID:** `{user_id}`\n"
        f"🔗 **Связь с автором:** [Написать в ЛС](tg://user?id={user_id})\n"
        f"───────────────────\n\n"
    )
    
    try:
        if message.text:
            await bot.send_message(ADMIN_ID, f"{admin_header}💬 **Текст обращения:**\n{message.text}", parse_mode="Markdown")
        elif message.photo:
            caption = message.caption or "_Без подписи_"
            await bot.send_photo(ADMIN_ID, photo=message.photo[-1].file_id, caption=f"{admin_header}📷 **Фото:**\n{caption}", parse_mode="Markdown")
        elif message.document:
            caption = message.caption or "_Без подписи_"
            await bot.send_document(ADMIN_ID, document=message.document.file_id, caption=f"{admin_header}📁 **Файл:**\n{caption}", parse_mode="Markdown")
        else:
            await bot.send_message(ADMIN_ID, admin_header, parse_mode="Markdown")
            await message.copy_to(ADMIN_ID)
            
        user_cooldowns[user_id] = time.time()
        
        success_text = (
            f"✅ **Ваше сообщение успешно отправлено редакции!**\n\n"
            f"Тип: **{category_name}**\nАдминистрация свяжется с вами при необходимости.\n\n"
            f"🛡 _Повторное предложение будет доступно через 24 часа._"
        )
        await message.answer(success_text, reply_markup=get_main_keyboard(), parse_mode="Markdown")

    except Exception as e:
        logging.error(f"Ошибка при отправке сообщения админу: {e}")
        await message.answer("⚠️ Произошла ошибка при отправке сообщения. Попробуйте позже.", reply_markup=get_main_keyboard())
    finally:
        await state.clear()

async def main():
    logging.basicConfig(level=logging.INFO)
    print("🚀 Автоматический бот запущен с переменными из .env!")
    
    await bot.delete_webhook(drop_pending_updates=True)
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())