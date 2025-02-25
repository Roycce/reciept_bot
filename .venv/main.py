import os
import logging
import json
import asyncio
import uuid
from datetime import datetime
from typing import Dict
from aiogram import Bot, Dispatcher, types, exceptions
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    KeyboardButton,
    ReplyKeyboardMarkup,
)
from dotenv import load_dotenv
from gspread_asyncio import AsyncioGspreadClientManager
from oauth2client.service_account import ServiceAccountCredentials

# Настройка логирования
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)

# Загрузка переменных окружения
load_dotenv()
API_TOKEN = os.getenv("BOT_API_TOKEN")
ADMIN_IDS = list(map(int, os.getenv("ADMIN_IDS", "").split(","))) if os.getenv("ADMIN_IDS") else []

# Константы
DATA_FILE = "users.json"
SHEET_URL = os.getenv("SHEET_URL")
WORKSHEET_NAME = os.getenv("WORKSHEET_NAME")

# Инициализация бота и диспетчера
bot = Bot(token=API_TOKEN)
dp = Dispatcher(storage=MemoryStorage())

# Временное хранилище и блокировка
temp_storage: Dict[str, dict] = {}
temp_storage_lock = asyncio.Lock()


class CheckForm(StatesGroup):
    username = State()
    date = State()
    amount1 = State()
    amount2 = State()
    fullname = State()


class GoogleSheetsManager:
    def __init__(self):
        self.client_manager = AsyncioGspreadClientManager(
            lambda: ServiceAccountCredentials.from_json_keyfile_name(
                "credentials.json",
                [
                    "https://www.googleapis.com/auth/spreadsheets",
                    "https://www.googleapis.com/auth/drive",
                ],
            )
        )

    async def append_data(self, data: dict, status: str) -> bool:
        try:
            client = await self.client_manager.authorize()
            spreadsheet = await client.open_by_url(SHEET_URL)
            worksheet = await spreadsheet.worksheet(WORKSHEET_NAME)
            row = [
                data["check_id"],
                data["username"],
                data["date"],
                data["amount1"],
                data["amount2"],
                data["fullname"],
                status
            ]
            await worksheet.append_row(row)
            return True
        except Exception as e:
            logger.error(f"Google Sheets Error: {str(e)}", exc_info=True)
            return False


gsheets = GoogleSheetsManager()


def load_users() -> dict:
    try:
        with open(DATA_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {}



def save_users(users: dict):
    with open(DATA_FILE, "w", encoding="utf-8") as f:
        json.dump(users, f, indent=4, ensure_ascii=False)


def get_user_id(username: str) -> int | None:
    users = load_users()
    user_data = users.get(username.lower())
    return user_data["user_id"] if user_data else None


def get_cancel_keyboard() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[[KeyboardButton(text="❌ Отмена")]],
        resize_keyboard=True,
    )


def get_date_keyboard() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="📅 Сегодня"), KeyboardButton(text="📝 Ввести дату")],
            [KeyboardButton(text="❌ Отмена")]
        ],
        resize_keyboard=True,
    )


@dp.message(Command("start"))
async def cmd_start(message: types.Message):
    users = load_users()
    username = message.from_user.username
    user_id = message.from_user.id

    if username:
        # Если пользователь уже существует, обновляем его данные
        if username.lower() in users:
            users[username.lower()]["user_id"] = user_id
        else:
            # Если пользователь новый, добавляем его с пустой заметкой
            users[username.lower()] = {"user_id": user_id, "note": ""}
        save_users(users)

    if user_id not in ADMIN_IDS:
        for admin_id in ADMIN_IDS:
            try:
                await bot.send_message(
                    admin_id,
                    f"👤 Новый пользователь: @{username or 'Не указан'} (ID: {user_id})"
                )
            except Exception as e:
                logger.error(f"Ошибка уведомления админа: {e}")

    await message.answer(
        "🌟 Добро пожаловать в бота для управления чеками!\n"
        "Используйте /check чтобы создать новый чек.",
        reply_markup=get_cancel_keyboard(),
    )


@dp.message(Command("add_user"))
async def cmd_add_user(message: types.Message):
    if message.from_user.id not in ADMIN_IDS:
        return await message.answer("🚫 Эта команда доступна только администраторам.")

    args = message.text.split(maxsplit=3)
    if len(args) < 3:
        return await message.answer("❌ Используйте: /add_user <username> <user_id> [заметка]")

    username, user_id = args[1].lower(), args[2]
    note = args[3] if len(args) > 3 else ""

    if not user_id.isdigit():
        return await message.answer("❌ user_id должен быть числом!")

    users = load_users()
    users[username] = {"user_id": int(user_id), "note": note}
    save_users(users)

    await message.answer(f"✅ Пользователь @{username} (ID: {user_id}) добавлен! Заметка: {note}")



@dp.message(Command("list_users"))
async def cmd_list_users(message: types.Message):
    if message.from_user.id not in ADMIN_IDS:
        return await message.answer("🚫 Эта команда доступна только администраторам.")

    users = load_users()
    if not users:
        return await message.answer("📂 Список пользователей пуст.")

    user_list = "\n".join([f"@{username} -> {user_data['user_id']} | Заметка: {user_data.get('note', 'Нет заметки')}"
                           for username, user_data in users.items()])
    await message.answer(f"📋 Список пользователей:\n{user_list}")

@dp.message(Command("set_note"))
async def cmd_set_note(message: types.Message):
    if message.from_user.id not in ADMIN_IDS:
        return await message.answer("🚫 Эта команда доступна только администраторам.")

    args = message.text.split(maxsplit=2)
    if len(args) < 3:
        return await message.answer("❌ Используйте: /set_note <username> <заметка>")

    username, note = args[1].lower(), args[2]

    users = load_users()
    if username not in users:
        return await message.answer(f"❌ Пользователь @{username} не найден!")

    users[username]['note'] = note
    save_users(users)

    await message.answer(f"✅ Заметка для @{username} успешно обновлена!")

@dp.message(Command("check"))
async def cmd_check(message: types.Message, state: FSMContext):
    if message.from_user.id not in ADMIN_IDS:
        return await message.answer("🚫 Эта команда доступна только администраторам.")

    await state.set_state(CheckForm.username)
    await message.answer(
        "👤 Введите юзернейм *без @*, заметку или юзер id получателя:",
        parse_mode="Markdown",
        reply_markup=get_cancel_keyboard(),
    )

@dp.message(CheckForm.username)
async def process_username(message: types.Message, state: FSMContext):
    if message.text == "❌ Отмена":
        await state.clear()
        return await message.answer("❌ Создание чека отменено.", reply_markup=types.ReplyKeyboardRemove())

    users = load_users()
    input_data = message.text.strip().lower()

    # Поиск по юзернейму
    if input_data in users:
        username = input_data
        await message.answer(f"✅ Найден пользователь по юзернейму: @{username}")
    else:
        # Поиск по user_id
        user_by_id = next((username for username, data in users.items() if str(data["user_id"]) == input_data), None)
        if user_by_id:
            username = user_by_id
            await message.answer(f"✅ Найден пользователь по user_id: @{username}")
        else:
            # Поиск по заметке
            user_by_note = next((username for username, data in users.items() if input_data in data.get("note", "").lower()), None)
            if user_by_note:
                username = user_by_note
                await message.answer(f"✅ Найден пользователь по заметке: @{username}")
            else:
                return await message.answer("❌ Пользователь не найден! Проверьте введенные данные.")

    await state.update_data(username=username)
    await state.set_state(CheckForm.date)
    await message.answer(
        "📅 Выберите дату чека:",
        reply_markup=get_date_keyboard(),
    )


@dp.message(CheckForm.date)
async def process_date(message: types.Message, state: FSMContext):
    if message.text == "❌ Отмена":
        await state.clear()
        return await message.answer("❌ Создание чека отменено.", reply_markup=types.ReplyKeyboardRemove())

    if message.text == "📅 Сегодня":
        date = datetime.now().strftime("%d.%m.%Y")
    else:
        try:
            datetime.strptime(message.text, "%d.%m.%Y")
            date = message.text
        except ValueError:
            return await message.answer("❌ Неверный формат! Используйте ДД.ММ.ГГГГ")

    await state.update_data(date=date)
    await state.set_state(CheckForm.amount1)
    await message.answer(
        "💰 Введите сумму 1:",
        reply_markup=get_cancel_keyboard(),
    )


@dp.message(CheckForm.amount1)
async def process_amount1(message: types.Message, state: FSMContext):
    if message.text == "❌ Отмена":
        await state.clear()
        return await message.answer("❌ Создание чека отменено.", reply_markup=types.ReplyKeyboardRemove())

    if not message.text.isdigit():
        return await message.answer("❌ Сумма должна быть числом!")

    await state.update_data(amount1=message.text)
    await state.set_state(CheckForm.amount2)
    await message.answer(
        "💰 Введите сумму 2:",
        reply_markup=get_cancel_keyboard(),
    )


@dp.message(CheckForm.amount2)
async def process_amount2(message: types.Message, state: FSMContext):
    if message.text == "❌ Отмена":
        await state.clear()
        return await message.answer("❌ Создание чека отменено.", reply_markup=types.ReplyKeyboardRemove())

    if not message.text.isdigit():
        return await message.answer("❌ Сумма должна быть числом!")

    await state.update_data(amount2=message.text)
    await state.set_state(CheckForm.fullname)
    await message.answer(
        "📛 Введите ФИО получателя:",
        reply_markup=get_cancel_keyboard(),
    )


@dp.message(CheckForm.fullname)
async def process_fullname(message: types.Message, state: FSMContext):
    if message.text == "❌ Отмена":
        await state.clear()
        return await message.answer("❌ Создание чека отменено.", reply_markup=types.ReplyKeyboardRemove())

    await state.update_data(fullname=message.text)
    data = await state.get_data()

    check_preview = (
        "📋 *Превью чека*\n"
        f"👤: @{data['username']}\n"
        f"📅 Дата: {data['date']}\n"
        f"💰 Сумма 1: {data['amount1']}\n"
        f"💰 Сумма 2: {data['amount2']}\n"
        f"📛 ФИО: {data['fullname']}"
    )

    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✅ Отправить", callback_data="send_check"),
         InlineKeyboardButton(text="🔄 Переделать", callback_data="redo_check")]
    ])
    await message.answer(check_preview, reply_markup=keyboard, parse_mode="Markdown")


@dp.callback_query(lambda c: c.data == "send_check")
async def send_check(callback_query: types.CallbackQuery, state: FSMContext):
    data = await state.get_data()
    user_id = get_user_id(data["username"])

    if not user_id:
        await callback_query.message.edit_text(f"❌ Пользователь @{data['username']} не найден!")
        return

    # Генерируем ID чека
    check_id = str(uuid.uuid4())
    data["check_id"] = check_id

    # Сохраняем в Google Sheets с временным статусом "Ожидание"
    try:
        success = await gsheets.append_data(data, "Ожидание")
        if not success:
            raise Exception("Ошибка при добавлении в Google Sheets")
    except Exception as e:
        logger.error(f"Ошибка записи в Google Sheets: {str(e)}")
        await callback_query.message.edit_text("❌ Ошибка при сохранении чека!")
        return

    # Сохраняем чек во временное хранилище
    async with temp_storage_lock:
        temp_storage[check_id] = data

    # Отправляем пользователю
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(
            text="✔ Подтвердить",
            callback_data=f"confirm_check:{user_id}:{check_id}"
        )],
        [InlineKeyboardButton(
            text="✖ Отклонить",
            callback_data=f"reject_check:{user_id}:{check_id}"
        )]
    ])

    try:
        await bot.send_message(
            user_id,
            f"🔔 Вам отправлен чек для подтверждения:\n"
            f"📅 Дата: {data['date']}\n"
            f"💰 Сумма 1: {data['amount1']}\n"
            f"💰 Сумма 2: {data['amount2']}\n"
            f"📛 ФИО: {data['fullname']}\n"
            f"Статус: Ожидание ⏳",
            reply_markup=keyboard
        )
        await callback_query.message.edit_text("✅ Чек успешно отправлен пользователю!")
        await state.clear()
    except exceptions.TelegramBadRequest as e:
        logger.error(f"Ошибка отправки: {e}")
        await callback_query.message.edit_text("❌ Пользователь не найден или чат недоступен!")
    except Exception as e:
        logger.error(f"Ошибка отправки чека: {str(e)}")
        await callback_query.message.edit_text("❌ Ошибка при отправке чека!")

async def update_status_in_sheets(check_data: dict, new_status: str):
    try:
        client = await gsheets.client_manager.authorize()
        spreadsheet = await client.open_by_url(SHEET_URL)
        worksheet = await spreadsheet.worksheet(WORKSHEET_NAME)
        rows = await worksheet.get_all_values()

        for i, row in enumerate(rows, start=1):
            if row and row[0] == check_data["check_id"]:
                await worksheet.update_cell(i, len(row), new_status)
                return True
    except Exception as e:
        logger.error(f"Ошибка обновления статуса в Google Sheets: {str(e)}")
    return False


@dp.callback_query(lambda c: c.data.startswith("confirm_check:"))
async def confirm_check(callback_query: types.CallbackQuery):
    parts = callback_query.data.split(":")
    user_id = int(parts[1])
    check_id = parts[2]

    async with temp_storage_lock:
        check_data = temp_storage.get(check_id)

    if not check_data:
        return await callback_query.message.answer("❌ Чек устарел или не найден!")

    success = await update_status_in_sheets(check_data, "Принят ✅")

    for admin_id in ADMIN_IDS:
        try:
            await bot.send_message(
                admin_id,
                f"📊 Чек от @{check_data['username']} ({check_data['date']})\n"
                f"Суммы: {check_data['amount1']}/{check_data['amount2']}\n"
                f"ФИО: {check_data['fullname']}\n"
                f"Статус: {'Принят ✅' if success else 'Ошибка ❌'}"
            )
        except Exception as e:
            logger.error(f"Ошибка отправки админу: {str(e)}")

    await callback_query.message.edit_reply_markup(reply_markup=None)

    if success:
        await callback_query.message.answer("✅ Чек подтверждён!")  # Отправляем новое сообщение
    else:
        await callback_query.message.answer("❌ Ошибка при обновлении статуса!")


@dp.callback_query(lambda c: c.data.startswith("reject_check:"))
async def reject_check(callback_query: types.CallbackQuery):
    parts = callback_query.data.split(":")
    user_id = int(parts[1])
    check_id = parts[2]

    async with temp_storage_lock:
        check_data = temp_storage.get(check_id)

    if not check_data:
        return await callback_query.message.answer("❌ Чек устарел или не найден!")

    success = await update_status_in_sheets(check_data, "Отклонён ❌")

    for admin_id in ADMIN_IDS:
        try:
            await bot.send_message(
                admin_id,
                f"📊 Чек от @{check_data['username']} ({check_data['date']})\n"
                f"Суммы: {check_data['amount1']}/{check_data['amount2']}\n"
                f"ФИО: {check_data['fullname']}\n"
                f"Статус: {'Отклонён ❌' if success else 'Ошибка ❌'}"
            )
        except Exception as e:
            logger.error(f"Ошибка отправки админу: {str(e)}")

    await callback_query.message.edit_reply_markup(reply_markup=None)

    if success:
        await callback_query.message.answer("❌ Чек отклонён!")  # Отправляем новое сообщение
    else:
        await callback_query.message.answer("❌ Ошибка при обновлении статуса!")


async def main():
    logger.info("Запуск бота...")
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())