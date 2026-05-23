import asyncio
import logging
import os
import sqlite3
from datetime import datetime, timedelta
from aiogram import Bot, Dispatcher, types
from aiogram.filters import Command
from aiogram.types import Message
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from dotenv import load_dotenv

load_dotenv()

# Настройка логирования
logging.basicConfig(level=logging.INFO)

TOKEN = os.getenv("BOT_TOKEN")
PASSWORD = os.getenv("BOT_PASSWORD")

if not TOKEN or not PASSWORD:
    raise ValueError("BOT_TOKEN и BOT_PASSWORD должны быть в .env")

bot = Bot(token=TOKEN)
storage = MemoryStorage()
dp = Dispatcher(storage=storage)

# Состояние авторизации
class AuthState(StatesGroup):
    waiting_for_password = State()

# База данных
conn = sqlite3.connect('archiver.db', check_same_thread=False)
cursor = conn.cursor()
cursor.execute('''
    CREATE TABLE IF NOT EXISTS messages (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        chat_id INTEGER,
        message_id INTEGER,
        user_id INTEGER,
        username TEXT,
        text TEXT,
        timestamp DATETIME,
        is_edited BOOLEAN DEFAULT 0,
        is_deleted BOOLEAN DEFAULT 0
    )
''')
conn.commit()

# Функции работы с БД
def add_message(msg: Message):
    cursor.execute('''
        INSERT INTO messages (chat_id, message_id, user_id, username, text, timestamp)
        VALUES (?, ?, ?, ?, ?, ?)
    ''', (
        msg.chat.id,
        msg.message_id,
        msg.from_user.id,
        msg.from_user.username or str(msg.from_user.id),
        msg.text or msg.caption or "[non-text]",
        datetime.now()
    ))
    conn.commit()

def get_message(chat_id, message_id):
    cursor.execute('''
        SELECT text, user_id, username FROM messages
        WHERE chat_id=? AND message_id=?
    ''', (chat_id, message_id))
    return cursor.fetchone()

def mark_edited(chat_id, message_id, new_text):
    cursor.execute('''
        UPDATE messages SET text=?, is_edited=1 WHERE chat_id=? AND message_id=?
    ''', (new_text, chat_id, message_id))
    conn.commit()

def mark_deleted(chat_id, message_id):
    cursor.execute('''
        UPDATE messages SET is_deleted=1 WHERE chat_id=? AND message_id=?
    ''', (chat_id, message_id))
    conn.commit()

def delete_old_messages():
    cutoff = datetime.now() - timedelta(hours=12)
    cursor.execute('DELETE FROM messages WHERE timestamp < ?', (cutoff,))
    conn.commit()
    logging.info("Старые сообщения удалены из БД")

# Команда /start — запрос пароля
@dp.message(Command("start"))
async def start_cmd(message: Message, state: FSMContext):
    await message.answer("🔐 Введите пароль для доступа к боту:")
    await state.set_state(AuthState.waiting_for_password)

# Проверка пароля
@dp.message(AuthState.waiting_for_password)
async def check_password(message: Message, state: FSMContext):
    if message.text == PASSWORD:
        await state.clear()
        await message.answer("✅ Доступ разрешён. Бот отслеживает чаты.")
    else:
        await message.answer("❌ Неверный пароль. Доступ запрещён.")
        await state.clear()

# Сохранение нового сообщения (после авторизации)
@dp.message()
async def save_message(message: Message):
    if not message.text or message.text.startswith('/'):
        return
    # Игнорируем команды и пустые сообщения
    add_message(message)

# Редактирование сообщения
@dp.edited_message()
async def edit_message(edited_msg: Message):
    old = get_message(edited_msg.chat.id, edited_msg.message_id)
    if old:
        old_text, user_id, username = old
        new_text = edited_msg.text or edited_msg.caption or "[non-text]"
        if old_text != new_text:
            mark_edited(edited_msg.chat.id, edited_msg.message_id, new_text)
            await bot.send_message(
                chat_id=edited_msg.chat.id,
                text=(
                    f"✏️ Пользователь @{username} отредактировал сообщение:\n\n"
                    f"📜 Было: {old_text}\n\n"
                    f"🆕 Стало: {new_text}\n\n"
                    f"🕒 {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
                )
            )

# Удаление сообщения (Telegram присылает событие message_deleted)
@dp.message_handler(content_types=types.ContentType.DELETE)
async def delete_message(event: types.MessageDeleteEvent):
    for msg_id in event.message_ids:
        old = get_message(event.chat.id, msg_id)
        if old:
            old_text, user_id, username = old
            mark_deleted(event.chat.id, msg_id)
            await bot.send_message(
                chat_id=event.chat.id,
                text=(
                    f"🗑 Пользователь @{username} удалил сообщение:\n\n"
                    f"📜 Текст: {old_text}\n\n"
                    f"🕒 {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
                )
            )

# Фоновая очистка БД раз в час
async def cleanup_task():
    while True:
        await asyncio.sleep(3600)  # 1 час
        delete_old_messages()

@dp.startup()
async def on_startup():
    asyncio.create_task(cleanup_task())
    logging.info("Бот запущен")

if __name__ == "__main__":
    dp.run_polling(bot)
