import asyncio
import base64
import json
import os
import logging
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Tuple
from enum import Enum

import aiofiles
from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import Command
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.context import FSMContext
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton, ReplyKeyboardMarkup, KeyboardButton
from aiogram.types import Message, CallbackQuery, ContentType
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.types import ChatMemberUpdated
from aiogram.filters import ChatMemberUpdatedFilter, IS_MEMBER, IS_NOT_MEMBER, LEAVE_TRANSITION

# Настройка логирования
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Настройки бота
BOT_TOKEN = '8522458548:AAGIlYVpM7CAkd9O7Pv49aGBEYfPj8p_fbM'
ADMIN_ID = 8154266510
STAFF_CHAT_ID = -1003644053522

# Инициализация бота
bot = Bot(token=BOT_TOKEN, default=DefaultBotProperties(parse_mode="HTML"))
storage = MemoryStorage()
dp = Dispatcher(storage=storage)

# Пути к файлам данных
USERS_FILE = 'users.json'
BUILDS_FILE = 'builds.json'
STATS_FILE = 'stats.json'
ADMINS_FILE = 'admins.json'
PENDING_BUILDS_FILE = 'pending_builds.json'
NOTIFICATIONS_FILE = 'notifications.json'

# Классы состояний
class AdminStates(StatesGroup):
    waiting_for_title = State()
    waiting_for_author = State()
    waiting_for_description = State()
    waiting_for_cover = State()
    waiting_for_link = State()
    waiting_for_price = State()
    waiting_for_contact = State()
    waiting_for_category = State()
    waiting_for_username = State()
    waiting_for_confirmation = State()

class DeleteBuildStates(StatesGroup):
    waiting_for_build_title = State()

@dp.callback_query(F.data == "delete_build")
async def delete_build_start(callback: CallbackQuery):
    """Начало удаления сборки"""
    if not await is_main_admin(callback.from_user.id):
        await callback.answer("Только главный администратор может удалять сборки.")
        return
    
    builds_data = await get_builds()
    builds = []
    
    for build_id, build_data in builds_data.items():
        build = Build.from_dict(build_data)
        builds.append(build)
    
    if not builds:
        await callback.answer("Нет сборок для удаления.")
        return
    
    await callback.message.edit_text(
        f"<b>Выберите сборку для удаления ({len(builds)}):</b>",
        reply_markup=get_delete_builds_keyboard(builds)
    )
    await callback.answer()

@dp.callback_query(F.data.startswith("delete_build_"))
async def confirm_delete_build(callback: CallbackQuery):
    """Подтверждение удаления сборки"""
    if not await is_main_admin(callback.from_user.id):
        await callback.answer("Только главный администратор может удалять сборки.")
        return
    
    build_id = callback.data.split("_")[-1]
    builds = await get_builds()
    build_data = builds.get(build_id)
    
    if not build_data:
        await callback.answer("Сборка не найдена.")
        return
    
    build = Build.from_dict(build_data)
    
    confirm_keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="✅ Да, удалить", callback_data=f"confirm_delete_{build_id}"),
            InlineKeyboardButton(text="❌ Нет, отмена", callback_data="delete_build")
        ]
    ])
    
    await callback.message.edit_text(
        f"<b>Вы уверены, что хотите удалить сборку?</b>\n\n"
        f"📁 <b>Название:</b> {build.title}\n"
        f"👤 <b>Автор:</b> {build.author}\n"
        f"💰 <b>Тип:</b> {'Платная' if build.category == BuildCategory.PAID else 'Бесплатная'}\n\n"
        f"<i>Это действие нельзя отменить!</i>",
        reply_markup=confirm_keyboard
    )
    await callback.answer()

@dp.callback_query(F.data.startswith("confirm_delete_"))
async def process_delete_build(callback: CallbackQuery):
    """Обработка удаления сборки"""
    if not await is_main_admin(callback.from_user.id):
        await callback.answer("Только главный администратор может удалять сборки.")
        return
    
    build_id = callback.data.split("_")[-1]
    builds = await get_builds()
    build_data = builds.get(build_id)
    
    if not build_data:
        await callback.answer("Сборка не найдена.")
        return
    
    build = Build.from_dict(build_data)
    
    # Удаляем сборку
    del builds[build_id]
    await save_builds(builds)
    
    # ОБНОВЛЯЕМ СТАТИСТИКУ - уменьшаем счетчик сборок
    stats = await get_stats()
    stats["builds_added"] = max(0, stats.get("builds_added", 0) - 1)
    await save_json(STATS_FILE, stats)
    
    # Если платная сборка - уменьшаем счетчик платных
    if build.category == BuildCategory.PAID:
        # Можно добавить отдельный счетчик для платных сборок
        pass
    
    # Уведомляем автора сборки (если это не главный админ)
    added_by = build_data.get("added_by")
    if added_by and added_by != ADMIN_ID:
        try:
            await bot.send_message(
                added_by,
                f"ℹ️ Ваша сборка '{build.title}' была удалена из каталога администратором."
            )
        except:
            pass
    
    await callback.answer(f"✅ Сборка '{build.title}' удалена.")
    
    # Возвращаемся к списку сборок для удаления
    builds_data = await get_builds()
    builds_list = []
    
    for bid, bdata in builds_data.items():
        build_obj = Build.from_dict(bdata)
        builds_list.append(build_obj)
    
    if not builds_list:
        await callback.message.edit_text(
            "✅ Сборка удалена.\n\nНет других сборок для удаления.",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="🔙 Назад", callback_data="admin_panel")]
            ])
        )
    else:
        await callback.message.edit_text(
            f"✅ Сборка удалена.\n\n<b>Выберите следующую сборку для удаления ({len(builds_list)}):</b>",
            reply_markup=get_delete_builds_keyboard(builds_list)
        )

def encode_advert_text(text: str) -> str:
    """Кодировать текст для передачи в callback с учетом ограничений Telegram"""
    # Ограничиваем длину текста перед кодированием
    # Telegram ограничение callback_data - 64 байта
    # Префикс "advert_confirm_" занимает 15 символов
    # Значит на текст остаётся ~49 байт
    
    # Сначала обрезаем текст если слишком длинный
    max_text_length = 200  # Безопасное значение
    if len(text) > max_text_length:
        text = text[:max_text_length]
    
    # Используем сжатие текста (удаляем пробелы и спец символы)
    compressed_text = text.replace('\n', ' ').replace('\r', ' ').strip()
    
    # Используем более короткое кодирование
    # Вместо base64 используем простое кодирование
    encoded = compressed_text.encode('utf-8').hex()
    
    # Обрезаем до безопасной длины
    max_encoded_length = 40
    if len(encoded) > max_encoded_length:
        encoded = encoded[:max_encoded_length]
    
    return encoded

def decode_advert_text(encoded: str) -> str:
    """Декодировать текст из callback"""
    try:
        decoded = bytes.fromhex(encoded).decode('utf-8')
        return decoded
    except:
        return ""

@dp.message(Command("syncstats"))
async def cmd_syncstats(message: Message):
    """Синхронизировать статистику с реальными данными (только для главного админа)"""
    user_id = message.from_user.id
    
    if user_id != ADMIN_ID:
        await message.answer("❌ Только главный администратор может использовать эту команду.")
        return
    
    # Получаем реальные данные
    builds = await get_builds()
    users = await get_users()
    
    # Считаем реальные показатели
    real_total_users = len(users)
    real_total_builds = len(builds)
    
    # Считаем скачивания
    real_total_downloads = 0
    for user_data in users.values():
        real_total_downloads += user_data.get("downloads_count", 0)
    
    # Считаем платные/бесплатные сборки
    paid_builds = 0
    free_builds = 0
    for build_data in builds.values():
        if build_data.get("category") == "paid":
            paid_builds += 1
        else:
            free_builds += 1
    
    # Обновляем статистику
    stats = await get_stats()
    
    old_stats = stats.copy()
    
    stats["total_users"] = real_total_users
    stats["builds_added"] = real_total_builds
    stats["total_downloads"] = real_total_downloads
    
    # Сохраняем дополнительную информацию
    stats["paid_builds_count"] = paid_builds
    stats["free_builds_count"] = free_builds
    stats["last_sync"] = datetime.now().isoformat()
    
    await save_json(STATS_FILE, stats)
    
    result_text = f"""
✅ <b>Статистика синхронизирована!</b>

📊 <b>До синхронизации:</b>
• Пользователей: {old_stats.get('total_users', 0)}
• Сборок: {old_stats.get('builds_added', 0)}
• Скачиваний: {old_stats.get('total_downloads', 0)}

📈 <b>После синхронизации:</b>
• Пользователей: {real_total_users}
• Сборок: {real_total_builds} ({free_builds} бесплатных, {paid_builds} платных)
• Скачиваний: {real_total_downloads}

🔄 <b>Изменения:</b>
• Пользователи: {real_total_users - old_stats.get('total_users', 0):+d}
• Сборки: {real_total_builds - old_stats.get('builds_added', 0):+d}
• Скачивания: {real_total_downloads - old_stats.get('total_downloads', 0):+d}

<i>Статистика обновлена в {datetime.now().strftime('%H:%M:%S')}</i>
    """
    
    await message.answer(result_text)

@dp.callback_query(F.data.startswith("delete_page_"))
async def change_delete_page(callback: CallbackQuery):
    """Смена страницы при удалении сборок"""
    if not await is_main_admin(callback.from_user.id):
        await callback.answer("Только главный администратор может удалять сборки.")
        return
    
    page = int(callback.data.split("_")[-1])
    
    builds_data = await get_builds()
    builds = []
    
    for build_id, build_data in builds_data.items():
        build = Build.from_dict(build_data)
        builds.append(build)
    
    try:
        await callback.message.edit_text(
            f"<b>Выберите сборку для удаления ({len(builds)}):</b>",
            reply_markup=get_delete_builds_keyboard(builds, page)
        )
    except:
        # Если не можем редактировать, отправляем новое сообщение
        await callback.message.answer(
            f"<b>Выберите сборку для удаления ({len(builds)}):</b>",
            reply_markup=get_delete_builds_keyboard(builds, page)
        )
    
    await callback.answer()

class AdminManagementStates(StatesGroup):
    waiting_for_admin_username = State()
    waiting_for_admin_remove = State()

class UserStates(StatesGroup):
    waiting_for_reset_confirmation = State()

# Структуры данных
class BuildCategory(Enum):
    FREE = "free"
    PAID = "paid"

class Build:
    def __init__(self, title: str, author: str, description: str, cover_url: str, 
                 download_link: str, category: BuildCategory, price: int = 0, 
                 contact: str = "", added_by: int = 0, added_at: str = "", build_id: str = ""):
        self.title = title
        self.author = author
        self.description = description
        self.cover_url = cover_url
        self.download_link = download_link
        self.category = category
        self.price = price
        self.contact = contact
        self.added_by = added_by
        self.added_at = added_at
        self.build_id = build_id or f"{int(datetime.now().timestamp())}"

    def to_dict(self):
        return {
            "title": self.title,
            "author": self.author,
            "description": self.description,
            "cover_url": self.cover_url,
            "download_link": self.download_link,
            "category": self.category.value,
            "price": self.price,
            "contact": self.contact,
            "added_by": self.added_by,
            "added_at": self.added_at,
            "build_id": self.build_id
        }

    @classmethod
    def from_dict(cls, data: dict):
        return cls(
            title=data.get("title", ""),
            author=data.get("author", ""),
            description=data.get("description", ""),
            cover_url=data.get("cover_url", ""),
            download_link=data.get("download_link", ""),
            category=BuildCategory(data.get("category", "free")),
            price=data.get("price", 0),
            contact=data.get("contact", ""),
            added_by=data.get("added_by", 0),
            added_at=data.get("added_at", ""),
            build_id=data.get("build_id", "")
        )

# Утилиты для работы с файлами
async def load_json(file_path: str) -> dict:
    """Загрузка данных из JSON файла"""
    if not os.path.exists(file_path):
        return {}
    
    async with aiofiles.open(file_path, 'r', encoding='utf-8') as f:
        content = await f.read()
        return json.loads(content) if content else {}

async def save_json(file_path: str, data: dict):
    """Сохранение данных в JSON файл"""
    async with aiofiles.open(file_path, 'w', encoding='utf-8') as f:
        await f.write(json.dumps(data, ensure_ascii=False, indent=2))

async def get_users() -> dict:
    """Получение данных о пользователях"""
    return await load_json(USERS_FILE)

async def save_users(users: dict):
    """Сохранение данных о пользователях"""
    await save_json(USERS_FILE, users)

async def get_builds() -> dict:
    """Получение данных о сборках"""
    return await load_json(BUILDS_FILE)

async def save_builds(builds: dict):
    """Сохранение данных о сборках"""
    await save_json(BUILDS_FILE, builds)

async def check_admin_access(user_id: int, chat_id: int) -> bool:
    """Проверка доступа администратора"""
    if not await is_admin(user_id):
        return False
    
    # Главному админу разрешаем везде
    if user_id == ADMIN_ID:
        return True
    
    # Обычным админам разрешаем только в чате сотрудников
    return chat_id == STAFF_CHAT_ID

async def get_stats() -> dict:
    """Получение статистики (с автоматической синхронизацией)"""
    stats = await load_json(STATS_FILE)
    
    if not stats:
        stats = {
            "total_users": 0,
            "total_downloads": 0,
            "builds_added": 0,
            "paid_builds_sold": 0,
            "total_resets": 0,
            "last_updated": datetime.now().isoformat()
        }
        await save_json(STATS_FILE, stats)
        return stats
    
    # Автоматически синхронизируем при каждом получении статистики
    # или раз в день (чтобы не нагружать систему)
    last_updated = stats.get("last_updated")
    if last_updated:
        last_update_time = datetime.fromisoformat(last_updated)
        if datetime.now() - last_update_time > timedelta(hours=24):
            # Автосинхронизация раз в сутки
            await auto_sync_stats(stats)
    
    return stats

async def auto_sync_stats(stats: dict):
    """Автоматическая синхронизация статистики"""
    try:
        builds = await get_builds()
        users = await get_users()
        
        # Обновляем только основные показатели
        stats["builds_added"] = len(builds)
        stats["total_users"] = len(users)
        
        # Считаем скачивания
        total_downloads = 0
        for user_data in users.values():
            total_downloads += user_data.get("downloads_count", 0)
        stats["total_downloads"] = total_downloads
        
        stats["last_updated"] = datetime.now().isoformat()
        
        await save_json(STATS_FILE, stats)
        logger.info("Статистика автоматически синхронизирована")
    except Exception as e:
        logger.error(f"Ошибка автосинхронизации статистики: {e}")

async def update_stats(field: str, value: int = 1):
    """Обновление статистики"""
    stats = await get_stats()
    stats[field] = stats.get(field, 0) + value
    await save_json(STATS_FILE, stats)

async def get_admins() -> dict:
    """Получение данных об администраторах"""
    return await load_json(ADMINS_FILE)

async def save_admins(admins: dict):
    """Сохранение данных об администраторах"""
    await save_json(ADMINS_FILE, admins)

async def get_pending_builds() -> dict:
    """Получение ожидающих сборок"""
    return await load_json(PENDING_BUILDS_FILE)

async def save_pending_builds(pending: dict):
    """Сохранение ожидающих сборок"""
    await save_json(PENDING_BUILDS_FILE, pending)

async def get_notifications() -> dict:
    """Получение данных об уведомлениях"""
    return await load_json(NOTIFICATIONS_FILE)

async def save_notifications(notifications: dict):
    """Сохранение данных об уведомлениях"""
    await save_json(NOTIFICATIONS_FILE, notifications)

# Основные функции
async def register_user(user_id: int, username: str = "", first_name: str = "") -> bool:
    """Регистрация нового пользователя"""
    users = await get_users()
    
    if str(user_id) not in users:
        users[str(user_id)] = {
            "username": username,
            "first_name": first_name,
            "last_download": None,
            "downloads_count": 0,
            "registered_at": datetime.now().isoformat(),
            "paid_resets": 0,
            "notifications_enabled": True
        }
        await save_users(users)
        await update_stats("total_users")
        return True
    return False

async def can_download(user_id: int) -> Tuple[bool, Optional[str], Optional[datetime]]:
    """Проверка, может ли пользователь скачать сборку"""
    users = await get_users()
    user_data = users.get(str(user_id))
    
    if not user_data or not user_data.get("last_download"):
        return True, None, None
    
    last_download = datetime.fromisoformat(user_data["last_download"])
    next_available = last_download + timedelta(hours=24)
    
    if datetime.now() >= next_available:
        return True, None, None
    else:
        time_left = next_available - datetime.now()
        hours = int(time_left.total_seconds() // 3600)
        minutes = int((time_left.total_seconds() % 3600) // 60)
        return False, f"{hours}ч {minutes}м", next_available

async def update_last_download(user_id: int, build_id: str):
    """Обновление времени последней загрузки"""
    users = await get_users()
    if str(user_id) in users:
        users[str(user_id)]["last_download"] = datetime.now().isoformat()
        users[str(user_id)]["downloads_count"] = users[str(user_id)].get("downloads_count", 0) + 1
        users[str(user_id)]["last_build"] = build_id
        await save_users(users)
        await update_stats("total_downloads")
        
        # Запланировать уведомление через 24 часа
        await schedule_download_notification(user_id)

async def schedule_download_notification(user_id: int):
    """Запланировать уведомление о доступности загрузки"""
    notifications = await get_notifications()
    notification_time = datetime.now() + timedelta(hours=24)
    
    if str(user_id) not in notifications:
        notifications[str(user_id)] = []
    
    notifications[str(user_id)].append({
        "type": "download_available",
        "scheduled_time": notification_time.isoformat(),
        "created_at": datetime.now().isoformat(),
        "sent": False
    })
    
    await save_notifications(notifications)

@dp.message(Command("clearkeyboard"))
async def cmd_clearkeyboard(message: Message):
    """Удалить клавиатуру (только для главного админа в чате сотрудников)"""
    user_id = message.from_user.id
    chat_id = message.chat.id
    
    # Проверяем что это главный админ
    if user_id != ADMIN_ID:
        await message.answer("❌ Только главный администратор может использовать эту команду.")
        return
    
    # Проверяем что это чат сотрудников
    if chat_id != STAFF_CHAT_ID:
        await message.answer(f"❌ Эта команда доступна только в чате сотрудников (ID: {STAFF_CHAT_ID}).")
        return
    
    # Удаляем клавиатуру у всех сообщений в этом чате
    await message.answer(
        "✅ Клавиатура удалена.",
        reply_markup=types.ReplyKeyboardRemove()
    )
    
    # Также можно удалить последние N сообщений с клавиатурами
    await message.answer(
        "📋 <b>Команды для очистки чата:</b>\n\n"
        "<code>/clearkeyboard</code> - удалить клавиатуры\n"
        "<code>/admin</code> - панель администратора\n"
        "<code>/clean</code> - удалить последние сообщения"
    )

@dp.message(Command("clean"))
async def cmd_clean(message: Message):
    """Удалить последние сообщения (только для главного админа в чате сотрудников)"""
    user_id = message.from_user.id
    chat_id = message.chat.id
    
    # Проверяем что это главный админ
    if user_id != ADMIN_ID:
        await message.answer("❌ Только главный администратор может использовать эту команду.")
        return
    
    # Проверяем что это чат сотрудников
    if chat_id != STAFF_CHAT_ID:
        await message.answer(f"❌ Эта команда доступна только в чате сотрудников (ID: {STAFF_CHAT_ID}).")
        return
    
    # Пробуем удалить сообщение с командой
    try:
        await message.delete()
    except:
        pass
    
    # Удаляем последние 10 сообщений
    deleted_count = 0
    for i in range(10):
        try:
            # В aiogram 3.x нужно использовать chat_id и message_id
            await bot.delete_message(chat_id=chat_id, message_id=message.message_id - i - 1)
            deleted_count += 1
        except:
            break
    
    if deleted_count > 0:
        # Отправляем сообщение об успехе и удаляем его через 3 секунды
        msg = await message.answer(f"✅ Удалено {deleted_count} сообщений.")
        await asyncio.sleep(3)
        try:
            await msg.delete()
        except:
            pass
    else:
        msg = await message.answer("❌ Не удалось удалить сообщения.")
        await asyncio.sleep(3)
        try:
            await msg.delete()
        except:
            pass

async def check_and_send_notifications():
    """Проверка и отправка уведомлений"""
    notifications = await get_notifications()
    current_time = datetime.now()
    
    for user_id_str, user_notifications in list(notifications.items()):
        user_id = int(user_id_str)
        users = await get_users()
        user_data = users.get(str(user_id))
        
        if not user_data or not user_data.get("notifications_enabled", True):
            continue
        
        for i, notification in enumerate(user_notifications[:]):
            if not notification["sent"]:
                scheduled_time = datetime.fromisoformat(notification["scheduled_time"])
                if current_time >= scheduled_time:
                    try:
                        if notification["type"] == "download_available":
                            await bot.send_message(
                                user_id,
                                "🎉 Ваш лимит на скачивание сборок сброшен!\n"
                                "Теперь вы можете выбрать новую сборку."
                            )
                        
                        # Помечаем как отправленное
                        notifications[user_id_str][i]["sent"] = True
                    except Exception as e:
                        logger.error(f"Ошибка отправки уведомления пользователю {user_id}: {e}")
    
    await save_notifications(notifications)

async def reset_user_limit(user_id: int):
    """Сброс лимита пользователя"""
    users = await get_users()
    if str(user_id) in users:
        users[str(user_id)]["last_download"] = None
        users[str(user_id)]["paid_resets"] = users[str(user_id)].get("paid_resets", 0) + 1
        await save_users(users)
        await update_stats("total_resets")
        return True
    return False

async def is_admin(user_id: int) -> bool:
    """Проверка, является ли пользователь администратором"""
    admins = await get_admins()
    # Проверяем является ли главным админом ИЛИ есть в списке админов
    return user_id == ADMIN_ID or str(user_id) in admins

async def is_main_admin(user_id: int) -> bool:
    """Проверка, является ли пользователь главным администратором"""
    return user_id == ADMIN_ID

# Клавиатуры
def get_main_keyboard(user_id: int = None) -> ReplyKeyboardMarkup:
    """Главная клавиатура"""
    keyboard = ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="📦 Доступные сборки")],
            [KeyboardButton(text="ℹ️ Информация")],
            [
                KeyboardButton(text="👨‍💼 Вакансии"),
                KeyboardButton(text="📢 Реклама")
            ],
            [KeyboardButton(text="🎮 Для ютуберов")]
        ],
        resize_keyboard=True
    )
    
    if user_id and user_id == ADMIN_ID:
        keyboard.keyboard.append([KeyboardButton(text="⚙️ Админ-панель")])
    
    return keyboard

def get_admin_keyboard() -> InlineKeyboardMarkup:
    """Клавиатура администратора"""
    keyboard = [
        [
            InlineKeyboardButton(text="➕ Добавить сборку", callback_data="add_build"),
            InlineKeyboardButton(text="📊 Статистика", callback_data="stats")
        ],
        [
            InlineKeyboardButton(text="👥 Администраторы", callback_data="admins_list"),
            InlineKeyboardButton(text="🔓 Сбросить лимит", callback_data="reset_limit")
        ],
        [
            InlineKeyboardButton(text="⏳ Ожидающие сборки", callback_data="pending_builds"),
            InlineKeyboardButton(text="📋 Все сборки", callback_data="all_builds")
        ],
        [
            InlineKeyboardButton(text="🗑️ Удалить сборку", callback_data="delete_build"),
            InlineKeyboardButton(text="👤 Добавить админа", callback_data="add_admin")
        ],
        [
            InlineKeyboardButton(text="🗑️ Удалить админа", callback_data="remove_admin")
        ],
        [
            InlineKeyboardButton(text="🏠 Главное меню", callback_data="main_menu")
        ]
    ]
    return InlineKeyboardMarkup(inline_keyboard=keyboard)

def get_remove_admin_keyboard(admins: dict, page: int = 0, admins_per_page: int = 5) -> InlineKeyboardMarkup:
    """Клавиатура для удаления администраторов"""
    keyboard_buttons = []
    
    # Преобразуем словарь в список для пагинации
    admin_items = list(admins.items())
    start_idx = page * admins_per_page
    end_idx = start_idx + admins_per_page
    
    for admin_id, admin_data in admin_items[start_idx:end_idx]:
        username = admin_data.get("username", "Неизвестно")
        builds_added = admin_data.get("builds_added", 0)
        
        # Не показываем кнопку для удаления самого себя (главного админа)
        if int(admin_id) == ADMIN_ID:
            continue
            
        keyboard_buttons.append([
            InlineKeyboardButton(
                text=f"🗑️ @{username[:15]} ({builds_added} сборок)", 
                callback_data=f"remove_admin_{admin_id}"
            )
        ])
    
    # Навигация
    navigation_buttons = []
    if page > 0:
        navigation_buttons.append(InlineKeyboardButton(text="◀️ Назад", callback_data=f"remove_admin_page_{page-1}"))
    
    if end_idx < len(admin_items):
        navigation_buttons.append(InlineKeyboardButton(text="Вперед ▶️", callback_data=f"remove_admin_page_{page+1}"))
    
    if navigation_buttons:
        keyboard_buttons.append(navigation_buttons)
    
    keyboard_buttons.append([
        InlineKeyboardButton(text="🔙 Назад", callback_data="admin_panel")
    ])
    
    return InlineKeyboardMarkup(inline_keyboard=keyboard_buttons)

@dp.callback_query(F.data == "remove_admin")
async def remove_admin_start(callback: CallbackQuery):
    """Начало удаления администратора"""
    if not await is_main_admin(callback.from_user.id):
        await callback.answer("❌ Только главный администратор может удалять администраторов.")
        return
    
    admins = await get_admins()
    
    # Фильтруем главного администратора из списка
    filtered_admins = {k: v for k, v in admins.items() if int(k) != ADMIN_ID}
    
    if not filtered_admins:
        await callback.answer("❌ Нет администраторов для удаления (кроме главного).")
        return
    
    await callback.message.edit_text(
        f"<b>Выберите администратора для удаления ({len(filtered_admins)}):</b>\n\n"
        f"<i>Примечание: Главный администратор не может быть удален.</i>",
        reply_markup=get_remove_admin_keyboard(filtered_admins)
    )
    await callback.answer()

@dp.callback_query(F.data.startswith("remove_admin_"))
async def confirm_remove_admin(callback: CallbackQuery):
    """Подтверждение удаления администратора"""
    if not await is_main_admin(callback.from_user.id):
        await callback.answer("❌ Только главный администратор может удалять администраторов.")
        return
    
    parts = callback.data.split("_")
    
    if parts[2] == "page":
        # Обработка пагинации
        page = int(parts[3])
        admins = await get_admins()
        filtered_admins = {k: v for k, v in admins.items() if int(k) != ADMIN_ID}
        
        await callback.message.edit_text(
            f"<b>Выберите администратора для удаления ({len(filtered_admins)}):</b>\n\n"
            f"<i>Примечание: Главный администратор не может быть удален.</i>",
            reply_markup=get_remove_admin_keyboard(filtered_admins, page)
        )
        await callback.answer()
        return
    
    # Удаление конкретного администратора
    admin_id = parts[2]
    
    # Проверяем, не пытаемся ли удалить главного администратора
    if int(admin_id) == ADMIN_ID:
        await callback.answer("❌ Нельзя удалить главного администратора.")
        return
    
    admins = await get_admins()
    admin_data = admins.get(admin_id)
    
    if not admin_data:
        await callback.answer("❌ Администратор не найден.")
        return
    
    username = admin_data.get("username", "Неизвестно")
    builds_added = admin_data.get("builds_added", 0)
    added_at = admin_data.get("added_at", "")
    
    # Клавиатура подтверждения
    confirm_keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="✅ Да, удалить", callback_data=f"confirm_remove_{admin_id}"),
            InlineKeyboardButton(text="❌ Нет, отмена", callback_data="remove_admin")
        ]
    ])
    
    confirmation_text = f"""
⚠️ <b>Подтверждение удаления администратора</b>

👤 <b>Администратор:</b>
• ID: <code>{admin_id}</code>
• Юзернейм: @{username}
• Сборок добавлено: {builds_added}
• Добавлен: {added_at[:10] if added_at else 'Неизвестно'}

🔴 <b>Последствия удаления:</b>
• Администратор потеряет доступ к /admin
• Не сможет добавлять новые сборки
• Сохранит все добавленные ранее сборки
• Будет уведомлен об удалении прав

<b>Вы уверены, что хотите удалить этого администратора?</b>
"""
    
    await callback.message.edit_text(
        confirmation_text,
        reply_markup=confirm_keyboard
    )
    await callback.answer()

@dp.callback_query(F.data.startswith("confirm_remove_"))
async def process_remove_admin(callback: CallbackQuery):
    """Обработка удаления администратора"""
    if not await is_main_admin(callback.from_user.id):
        await callback.answer("❌ Только главный администратор может удалять администраторов.")
        return
    
    admin_id = callback.data.split("_")[-1]
    
    # Дополнительная проверка
    if int(admin_id) == ADMIN_ID:
        await callback.answer("❌ Нельзя удалить главного администратора.")
        return
    
    admins = await get_admins()
    
    if admin_id not in admins:
        await callback.answer("❌ Администратор не найден.")
        return
    
    admin_data = admins[admin_id]
    username = admin_data.get("username", "Неизвестно")
    
    # Удаляем администратора
    del admins[admin_id]
    await save_admins(admins)
    
    # Уведомляем бывшего администратора
    try:
        await bot.send_message(
            int(admin_id),
            "⚠️ <b>Ваши права администратора были отозваны</b>\n\n"
            "Вы больше не имеете доступа к:\n"
            "• Команде /admin\n"
            "• Добавлению новых сборок\n"
            "• Просмотру статистики\n\n"
            "Все добавленные вами сборки остаются в боте.\n"
            "По вопросам обращайтесь к главному администратору."
        )
    except Exception as e:
        logger.error(f"Не удалось уведомить администратора {admin_id}: {e}")
    
    # Обновляем сообщение
    await callback.message.edit_text(
        f"✅ <b>Администратор удален</b>\n\n"
        f"👤 @{username} больше не имеет прав администратора.\n"
        f"📋 Осталось администраторов: {len(admins)}",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="🗑️ Удалить еще", callback_data="remove_admin")],
            [InlineKeyboardButton(text="🔙 В админ-панель", callback_data="admin_panel")]
        ])
    )
    
    await callback.answer(f"Администратор @{username} удален")

@dp.message(Command("removeadmin"))
async def cmd_removeadmin(message: Message):
    """Удалить администратора по юзернейму (только для главного админа)"""
    user_id = message.from_user.id
    
    if user_id != ADMIN_ID:
        await message.answer("❌ Только главный администратор может использовать эту команду.")
        return
    
    args = message.text.split()
    if len(args) < 2:
        await message.answer("Использование: /removeadmin @username")
        return
    
    username = args[1].lstrip('@')
    
    # Ищем администратора по юзернейму
    admins = await get_admins()
    admin_id_to_remove = None
    admin_data_to_remove = None
    
    for admin_id, admin_data in admins.items():
        if admin_data.get("username") == username:
            admin_id_to_remove = admin_id
            admin_data_to_remove = admin_data
            break
    
    if not admin_id_to_remove:
        await message.answer(f"❌ Администратор @{username} не найден.")
        return
    
    # Проверяем, не пытаемся ли удалить главного администратора
    if int(admin_id_to_remove) == ADMIN_ID:
        await message.answer("❌ Нельзя удалить главного администратора.")
        return
    
    # Удаляем администратора
    del admins[admin_id_to_remove]
    await save_admins(admins)
    
    # Уведомляем бывшего администратора
    try:
        await bot.send_message(
            int(admin_id_to_remove),
            "⚠️ <b>Ваши права администратора были отозваны</b>\n\n"
            "Вы больше не имеете доступа к функциям администратора."
        )
    except:
        pass
    
    await message.answer(
        f"✅ <b>Администратор удален</b>\n\n"
        f"👤 @{username} больше не имеет прав администратора.\n"
        f"📋 Осталось администраторов: {len(admins)}"
    )

@dp.message(Command("help"))
async def cmd_help(message: Message):
    """Показать все команды бота (только для главного админа)"""
    user_id = message.from_user.id
    
    if user_id != ADMIN_ID:
        await message.answer("❌ Только главный администратор может использовать эту команду.")
        return
    
    help_text = """
<b>🤖 ПОЛНЫЙ СПИСОК КОМАНД БОТА</b>

<u>📋 ОСНОВНЫЕ КОМАНДЫ:</u>
<code>/start</code> - Начать работу с ботом
<code>/help</code> - Показать это меню (только главный админ)

<u>👑 КОМАНДЫ ГЛАВНОГО АДМИНА:</u>
<code>/admin</code> - Панель администратора
<code>/syncstats</code> - Синхронизировать статистику с реальными данными
<code>/removeadmin @username</code> - Удалить администратора по юзернейму
<code>/admininfo</code> - Подробная информация об администраторах
<code>/clearkeyboard</code> - Удалить клавиатуру в чате сотрудников
<code>/clean</code> - Удалить последние сообщения в чате сотрудников
<code>/advert</code> - Подать объявление

<u>⚙️ КОМАНДЫ АДМИНИСТРАТОРОВ:</u>
<code>/admin</code> - Панель администратора (в чате сотрудников)

<u>🎯 КОМАНДЫ ИЗ КЛАВИАТУРЫ:</u>
• 📦 Доступные сборки - Показать все сборки
• ℹ️ Информация - Информация о боте
• 👨‍💼 Вакансии - Информация о вакансиях
• 📢 Реклама - Информация о рекламе
• 🎮 Для ютуберов - Информация для ютуберов
• ⚙️ Админ-панель - Панель администратора (только для админов)

<u>🛠️ ФУНКЦИИ АДМИН-ПАНЕЛИ:</u>
• ➕ Добавить сборку - Добавить новую сборку
• 📊 Статистика - Показать статистику бота
• 👥 Администраторы - Список администраторов
• 🔓 Сбросить лимит - Сбросить лимит пользователю
• ⏳ Ожидающие сборки - Сборки на модерации
• 📋 Все сборки - Список всех сборок
• 🗑️ Удалить сборку - Удалить сборку из каталога
• 👤 Добавить админа - Добавить нового администратора
• 🗑️ Удалить админа - Удалить администратора
• 🏠 Главное меню - Вернуться в главное меню

<u>🔧 ДОПОЛНИТЕЛЬНЫЕ ВОЗМОЖНОСТИ:</u>
• Сброс лимита за 100 руб. - При достижении лимита скачивания
• Модерация сборок - Проверка сборок от обычных админов
• Автоуведомления - Уведомления о сбросе лимита через 24 часа
• Пагинация сборок - Навигация по списку сборок
• Категории сборок - Платные и бесплатные сборки

<u>📁 СИСТЕМНЫЕ ФАЙЛЫ:</u>
• <code>users.json</code> - Данные пользователей
• <code>builds.json</code> - Опубликованные сборки
• <code>stats.json</code> - Статистика бота
• <code>admins.json</code> - Данные администраторов
• <code>pending_builds.json</code> - Сборки на модерации
• <code>notifications.json</code> - Запланированные уведомления

<u>🔄 РАБОЧИЕ ПРОЦЕССЫ:</u>
• Автосинхронизация статистики - Раз в 24 часа
• Проверка уведомлений - Каждую минуту
• Ограничение скачивания - 1 сборка в 24 часа
• Модерация контента - Только главным админом

<b>⚠️ ОСОБЫЕ ПРАВИЛА ДОСТУПА:</b>
• Главный админ (ID: <code>{ADMIN_ID}</code>) - Полный доступ везде
• Обычные админы - Только в чате сотрудников (ID: <code>{STAFF_CHAT_ID}</code>)
• /admin - Для обычных админов работает ТОЛЬКО в чате сотрудников
• /clearkeyboard и /clean - ТОЛЬКО для главного админа в чате сотрудников

<i>Последнее обновление: {date}</i>
""".format(
        ADMIN_ID=ADMIN_ID,
        STAFF_CHAT_ID=STAFF_CHAT_ID,
        date=datetime.now().strftime("%d.%m.%Y %H:%M")
    )
    
    # Отправляем сообщение с форматированием
    await message.answer(help_text, parse_mode=ParseMode.HTML)

@dp.message(Command("admininfo"))
async def cmd_admininfo(message: Message):
    """Подробная информация об администраторах (только для главного админа)"""
    user_id = message.from_user.id
    
    if user_id != ADMIN_ID:
        await message.answer("❌ Только главный администратор может использовать эту команду.")
        return
    
    admins = await get_admins()
    builds = await get_builds()
    
    if not admins:
        await message.answer("❌ Нет администраторов в базе.")
        return
    
    info_text = "<b>📋 Подробная информация об администраторах:</b>\n\n"
    
    # Считаем статистику по сборкам для каждого админа
    admin_stats = {}
    for build_data in builds.values():
        added_by = build_data.get("added_by")
        if added_by:
            admin_stats[str(added_by)] = admin_stats.get(str(added_by), 0) + 1
    
    # Главный администратор
    info_text += f"👑 <b>Главный администратор:</b>\n"
    info_text += f"• ID: <code>{ADMIN_ID}</code>\n"
    info_text += f"• Юзернейм: @zavremya\n"
    info_text += f"• Сборок добавлено: {admin_stats.get(str(ADMIN_ID), 0)}\n\n"
    
    # Обычные администраторы
    regular_admins = {k: v for k, v in admins.items() if int(k) != ADMIN_ID}
    
    if regular_admins:
        info_text += f"👥 <b>Обычные администраторы ({len(regular_admins)}):</b>\n\n"
        
        for admin_id, admin_data in regular_admins.items():
            username = admin_data.get("username", "Неизвестно")
            builds_added = admin_stats.get(admin_id, 0)
            added_at = admin_data.get("added_at", "")
            added_by = admin_data.get("added_by", "Неизвестно")
            
            info_text += f"<b>• @{username}</b>\n"
            info_text += f"  ID: <code>{admin_id}</code>\n"
            info_text += f"  Сборок: {builds_added}\n"
            info_text += f"  Добавлен: {added_at[:10] if added_at else 'Неизвестно'}\n"
            info_text += f"  Кем добавлен: {added_by}\n\n"
    else:
        info_text += "👥 <b>Обычные администраторы:</b> Нет\n\n"
    
    info_text += f"📊 <b>Общая статистика:</b>\n"
    info_text += f"• Всего администраторов: {len(admins)}\n"
    info_text += f"• Всего сборок в боте: {len(builds)}\n"
    info_text += f"• Сборок от администраторов: {sum(admin_stats.values())}\n"
    
    await message.answer(info_text[:4000])  # Ограничение Telegram

def get_delete_builds_keyboard(builds: List[Build], page: int = 0, builds_per_page: int = 5) -> InlineKeyboardMarkup:
    """Клавиатура для удаления сборок"""
    keyboard_buttons = []
    
    start_idx = page * builds_per_page
    end_idx = start_idx + builds_per_page
    
    for build in builds[start_idx:end_idx]:
        emoji = "💰" if build.category == BuildCategory.PAID else "🆓"
        keyboard_buttons.append([
            InlineKeyboardButton(
                text=f"🗑️ {emoji} {build.title[:20]}", 
                callback_data=f"delete_build_{build.build_id}"
            )
        ])
    
    # Навигация
    navigation_buttons = []
    if page > 0:
        navigation_buttons.append(InlineKeyboardButton(text="◀️ Назад", callback_data=f"delete_page_{page-1}"))
    
    if end_idx < len(builds):
        navigation_buttons.append(InlineKeyboardButton(text="Вперед ▶️", callback_data=f"delete_page_{page+1}"))
    
    if navigation_buttons:
        keyboard_buttons.append(navigation_buttons)
    
    keyboard_buttons.append([
        InlineKeyboardButton(text="🔙 Назад", callback_data="admin_panel")
    ])
    
    return InlineKeyboardMarkup(inline_keyboard=keyboard_buttons)

def get_builds_keyboard(builds: List[Build], page: int = 0, builds_per_page: int = 5) -> InlineKeyboardMarkup:
    """Клавиатура со сборками"""
    keyboard_buttons = []
    
    start_idx = page * builds_per_page
    end_idx = start_idx + builds_per_page
    
    for build in builds[start_idx:end_idx]:
        emoji = "💰" if build.category == BuildCategory.PAID else "🆓"
        keyboard_buttons.append([
            InlineKeyboardButton(
                text=f"{emoji} {build.title[:20]} ({build.author[:10]})", 
                callback_data=f"build_{build.build_id}"
            )
        ])
    
    # Навигация
    navigation_buttons = []
    if page > 0:
        navigation_buttons.append(InlineKeyboardButton(text="◀️ Назад", callback_data=f"page_{page-1}"))
    
    if end_idx < len(builds):
        navigation_buttons.append(InlineKeyboardButton(text="Вперед ▶️", callback_data=f"page_{page+1}"))
    
    if navigation_buttons:
        keyboard_buttons.append(navigation_buttons)
    
    keyboard_buttons.append([
        InlineKeyboardButton(text="🔙 Главное меню", callback_data="main_menu")
    ])
    
    return InlineKeyboardMarkup(inline_keyboard=keyboard_buttons)

def get_build_details_keyboard(build: Build, can_download: bool = True) -> InlineKeyboardMarkup:
    """Клавиатура с деталями сборки"""
    keyboard_buttons = []
    
    if can_download:
        if build.category == BuildCategory.PAID:
            # Для платных сборок: первая кнопка - URL для связи с админом
            keyboard_buttons.append([
                InlineKeyboardButton(
                    text=f"💳 Купить за {build.price} руб.", 
                    url=f"tg://user?id={ADMIN_ID}"
                )
            ])
            # Вторая кнопка - URL для связи с продавцом (если указан)
            if build.contact:
                contact_link = f"https://t.me/{build.contact[1:]}" if build.contact.startswith('@') else build.contact
                keyboard_buttons.append([
                    InlineKeyboardButton(
                        text="📞 Связаться с продавцом", 
                        url=contact_link
                    )
                ])
        else:
            # Для бесплатных сборок: одна URL-кнопка для скачивания
            keyboard_buttons.append([
                InlineKeyboardButton(
                    text="⬇️ Скачать сборку", 
                    url=build.download_link
                )
            ])
    else:
        # Если лимит исчерпан: кнопка для сброса лимита (callback_data)
        keyboard_buttons.append([
            InlineKeyboardButton(
                text="🔄 Сбросить лимит (100 руб.)", 
                callback_data="reset_limit_payment"
            )
        ])
    
    # Кнопка возврата к списку сборок
    keyboard_buttons.append([
        InlineKeyboardButton(text="📦 К другим сборкам", callback_data="back_to_builds")
    ])
    
    return InlineKeyboardMarkup(inline_keyboard=keyboard_buttons)

def get_pending_builds_keyboard(pending_builds: dict) -> InlineKeyboardMarkup:
    """Клавиатура для ожидающих сборок"""
    keyboard_buttons = []
    
    for build_id, build_data in list(pending_builds.items())[:10]:  # Ограничиваем 10 сборками
        title = build_data.get('title', 'Без названия')[:15]
        author = build_data.get('author', 'Неизвестно')[:10]
        keyboard_buttons.append([
            InlineKeyboardButton(
                text=f"{title} ({author})",
                callback_data=f"review_build_{build_id}"
            )
        ])
    
    keyboard_buttons.append([
        InlineKeyboardButton(text="🔙 Назад", callback_data="admin_panel")
    ])
    
    return InlineKeyboardMarkup(inline_keyboard=keyboard_buttons)

def get_review_build_keyboard(build_id: str) -> InlineKeyboardMarkup:
    """Клавиатура для ревью сборки"""
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="✅ Одобрить", callback_data=f"approve_{build_id}"),
            InlineKeyboardButton(text="❌ Отклонить", callback_data=f"reject_{build_id}")
        ],
        [
            InlineKeyboardButton(text="🔙 Назад", callback_data="pending_builds")
        ]
    ])

def get_info_keyboard() -> InlineKeyboardMarkup:
    """Клавиатура для информации"""
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="👨‍💼 Вакансии", callback_data="vacancies_info"),
            InlineKeyboardButton(text="📢 Реклама", callback_data="advertisement_info")
        ],
        [
            InlineKeyboardButton(text="🎮 Для ютуберов", callback_data="youtubers_info")
        ],
        [
            InlineKeyboardButton(text="🔙 Главное меню", callback_data="main_menu")
        ]
    ])

# Обработчики команд
@dp.message(Command("start"))
async def cmd_start(message: Message):
    """Обработчик команды /start"""
    user_id = message.from_user.id
    chat_id = message.chat.id
    username = message.from_user.username or ""
    first_name = message.from_user.first_name or ""
    
    # Запрещаем /start в чате сотрудников
    if chat_id == STAFF_CHAT_ID:
        # Разрешаем только главному админу
        if user_id != ADMIN_ID:
            try:
                await message.delete()
            except:
                pass
            
            warning_msg = await message.answer(
                "❌ <b>Команда /start не работает в этом чате.</b>\n\n"
                "Используйте /help для списка команд.\n\n"
                "<i>Это сообщение удалится через 5 секунд...</i>"
            )
            
            await asyncio.sleep(5)
            try:
                await warning_msg.delete()
            except:
                pass
            
            return
    
    await register_user(user_id, username, first_name)
    
    # Получаем РЕАЛЬНЫЕ данные, а не из stats.json
    builds = await get_builds()
    users = await get_users()
    
    # Считаем скачивания
    total_downloads = 0
    for user_data in users.values():
        total_downloads += user_data.get("downloads_count", 0)
    
    welcome_text = f"""
<b>👋 Добро пожаловать в сборник Amazing Online!</b>

Здесь собраны лучшие сборки от популярных ютуберов по игре Amazing Online.

📊 <b>Статистика бота:</b>
• Пользователей: {len(users)}
• Сборок скачано: {total_downloads}
• Доступных сборок: {len(builds)}

💡 <b>Возможности:</b>
• Выбор сборки 1 раз в 24 часа
• Платные и бесплатные сборки
• Возможность сбросить лимит

<b>Для начала работы нажмите "📦 Доступные сборки"</b>
    """
    
    await message.answer(welcome_text, reply_markup=get_main_keyboard(user_id))

@dp.message(Command("admin"))
async def cmd_admin(message: Message):
    """Обработчик команды /admin"""
    user_id = message.from_user.id
    chat_id = message.chat.id
    
    # Проверяем является ли пользователь админом
    if not await is_admin(user_id):
        await message.answer("У вас нет доступа к этой команде.")
        return
    
    # Главному админу разрешаем везде
    if user_id == ADMIN_ID:
        await message.answer("⚙️ <b>Панель администратора</b>", reply_markup=get_admin_keyboard())
        return
    
    # Обычным админам разрешаем только в чате сотрудников
    if chat_id == STAFF_CHAT_ID:
        await message.answer("⚙️ <b>Панель администратора</b>", reply_markup=get_admin_keyboard())
    else:
        await message.answer("Команда /admin доступна только в чате сотрудников для обычных администраторов.")

@dp.message(F.text == "⚙️ Админ-панель")
async def admin_panel_button(message: Message):
    """Обработчик кнопки админ-панели"""
    user_id = message.from_user.id
    chat_id = message.chat.id
    
    # Проверяем является ли пользователь админом
    if not await is_admin(user_id):
        await message.answer("У вас нет доступа к админ-панели.")
        return
    
    # Главному админу разрешаем везде
    if user_id == ADMIN_ID:
        await message.answer("⚙️ <b>Панель администратора</b>", reply_markup=get_admin_keyboard())
        return
    
    # Обычным админам разрешаем только в чате сотрудников
    if chat_id == STAFF_CHAT_ID:
        await message.answer("⚙️ <b>Панель администратора</b>", reply_markup=get_admin_keyboard())
    else:
        await message.answer("Админ-панель доступна только в чате сотрудников для обычных администраторов.")

@dp.message(F.text == "📦 Доступные сборки")
async def show_builds(message: Message):
    """Показать доступные сборки"""
    builds_data = await get_builds()
    builds = []
    
    for build_id, build_data in builds_data.items():
        build = Build.from_dict(build_data)
        builds.append(build)
    
    if not builds:
        await message.answer("На данный момент нет доступных сборок.")
        return
    
    await message.answer(f"<b>Доступные сборки ({len(builds)}):</b>", 
                        reply_markup=get_builds_keyboard(builds))

@dp.message(F.text == "ℹ️ Информация")
async def show_info(message: Message):
    """Показать информацию"""
    info_text = """
<b>📢 Информация о боте:</b>

🎮 <b>Сборки Amazing Online</b>
В этом боте собраны лучшие сборки от популярных ютуберов по игре Amazing Online.

⏳ <b>Лимиты</b>
• Вы можете скачать одну сборку бесплатно каждые 24 часа
• Для сброса лимита: 100 рублей
• По вопросам оплаты: @zavremya

💰 <b>Платные сборки</b>
• Некоторые сборки доступны за плату
• Оплата напрямую автору сборки
• После оплаты вы получите ссылку на скачивание

<b>Выберите раздел для получения подробной информации:</b>
    """
    
    await message.answer(info_text, reply_markup=get_info_keyboard())

@dp.message(F.text == "👨‍💼 Вакансии")
async def show_vacancies(message: Message):
    """Показать информацию о вакансиях"""
    vacancies_text = """
<b>👨‍💼 Требуются администраторы</b>

Мы ищем активных администраторов для развития нашего бота со сборками Amazing Online.

<b>📌 Обязанности:</b>
• Публикация новых сборок в боте
• Реклама и продвижение бота
• Модерация контента
• Взаимодействие с пользователями

<b>💰 Оплата:</b>
• Уникальные сборки и моды из приват-блоков популярных ютуберов
• Эксклюзивные материалы по игре Amazing Online
• При активной работе - зарплата в реальных деньгах
• Бонусы за достижение целей

<b>🎁 Дополнительные плюшки:</b>
• Ранний доступ к новым сборкам
• Эксклюзивные модификации
• Приватные материалы от топовых ютуберов
• Возможность сотрудничества с известными авторами

<b>📝 Требования:</b>
• Знание игры Amazing Online
• Активность в Telegram
• Ответственность и коммуникабельность
• Опыт администрирования - приветствуется

<b>📞 Контакты для отклика:</b>
@zavremya

Отправьте краткое сообщение о себе и почему вы хотите стать администратором.
    """
    
    await message.answer(vacancies_text, reply_markup=get_info_keyboard())

@dp.message(F.text == "📢 Реклама")
async def show_advertisement(message: Message):
    """Показать информацию о рекламе"""
    ad_text = """
<b>📢 Размещение рекламы в боте</b>

Привлекайте новых подписчиков и продвигайте свой контент через нашего бота!

<b>🎯 Целевая аудитория:</b>
• Игроки Amazing Online
• Поклонники сборок и модов
• Активные пользователи Telegram

<b>💎 Форматы рекламы:</b>
• Реклама в главном меню бота
• Упоминание в описаниях сборок
• Отдельные рекламные сообщения
• Реклама при скачивании сборок

<b>💰 Стоимость рекламы:</b>
• <b>250 рублей</b> - базовый пакет
• 500 рублей - расширенный пакет
• 1000 рублей - премиум размещение

<b>📊 Охват:</b>
Бот активно развивается и привлекает новых пользователей ежедневно.

<b>🎮 Для ютуберов:</b>
• Продвижение вашего YouTube канала
• Привлечение подписчиков в Telegram
• Реклама ваших сборок

<b>📞 Контакты для заказа рекламы:</b>
@zavremya

Укажите в сообщении:
1. Тип рекламы
2. Ссылки на ваш контент
3. Желаемый срок размещения
    """
    
    await message.answer(ad_text, reply_markup=get_info_keyboard())

@dp.message(F.text == "🎮 Для ютуберов")
async def show_youtubers_info(message: Message):
    """Показать информацию для ютуберов"""
    youtubers_text = """
<b>🎮 Сотрудничество с ютуберами</b>

Размещайте свои сборки Amazing Online в нашем боте и привлекайте больше зрителей!

<b>🤝 Преимущества сотрудничества:</b>
• Бесплатное размещение ваших сборок
• Привлечение новой аудитории к вашему контенту
• Увеличение просмотров на YouTube
• Рост подписчиков в Telegram
• Возможность монетизации через платные сборки

<b>📦 Размещение сборок:</b>
• Бесплатные сборки - бесплатное размещение
• Платные сборки - процент от продаж или фиксированная плата
• Продвижение вашего канала в описаниях сборок
• Упоминание в рекламных материалах бота

<b>💰 Продажа сборок:</b>
• Размещайте платные сборки в боте
• Получайте оплату напрямую от пользователей
• Мы помогаем с технической стороной
• Поддержка и консультации

<b>📢 Реклама вашего канала:</b>
• Стоимость рекламы - 250 рублей
• Размещение в разделе информации бота
• Упоминание в рассылках
• Продвижение среди целевой аудитории

<b>📞 Контакты для сотрудничества:</b>
@zavremya

Пришлите ссылку на ваш YouTube канал и примеры сборок для обсуждения условий.
    """
    
    await message.answer(youtubers_text, reply_markup=get_info_keyboard())

# Callback обработчики
@dp.callback_query(F.data == "add_admin")
async def add_admin_start(callback: CallbackQuery, state: FSMContext):
    """Начало добавления администратора"""
    if not await is_main_admin(callback.from_user.id):
        await callback.answer("У вас нет прав для добавления администраторов.")
        return
    
    await state.set_state(AdminManagementStates.waiting_for_admin_username)
    
    await callback.message.edit_text(
        "Введите юзернейм пользователя (без @) для добавления в администраторы:"
    )
    await callback.answer()

@dp.chat_member()
async def chat_member_handler(update: types.ChatMemberUpdated):
    """Обработчик событий участников чата"""
    try:
        # Проверяем что это чат сотрудников
        if update.chat.id != STAFF_CHAT_ID:
            return
        
        user_id = update.new_chat_member.user.id
        username = update.new_chat_member.user.username or update.new_chat_member.user.first_name or "Пользователь"
        
        # Получаем текущих админов
        admins = await get_admins()
        
        # Проверяем изменения статуса
        old_status = update.old_chat_member.status
        new_status = update.new_chat_member.status
        
        # Логируем событие
        logger.info(f"Chat member update: {user_id} (@{username}) {old_status} -> {new_status}")
        
        # 1. ПОЛЬЗОВАТЕЛЬ ВОШЕЛ В ЧАТ ИЛИ СТАЛ УЧАСТНИКОМ
        if new_status in ["member", "administrator", "creator"] and old_status in ["left", "kicked", "restricted"]:
            # Пользователь вошел в чат или стал участником
            # Автоматически делаем его администратором бота
            
            # Проверяем не главный ли это админ
            if user_id == ADMIN_ID:
                # Главный админ уже в списке
                return
            
            # Проверяем не админ ли уже
            if str(user_id) not in admins:
                # Добавляем в администраторы
                admins[str(user_id)] = {
                    "username": username,
                    "builds_added": 0,
                    "added_at": datetime.now().isoformat(),
                    "added_by": "auto_chat_join",
                    "chat_join_date": datetime.now().isoformat()
                }
                await save_admins(admins)
                
                # Отправляем приветственное сообщение
                welcome_text = f"""
👋 <b>Добро пожаловать в чат сотрудников!</b>

🎉 <b>Вас автоматически назначили администратором бота!</b>

📋 <b>Теперь вы можете:</b>
• Использовать команду /admin
• Добавлять бесплатные сборки
• Просматривать статистику бота
• Отправлять сборки на модерацию

📌 <b>Важные правила:</b>
• Все сборки проходят модерацию
• Команда /admin работает только в чате сотрудников

🔧 <b>Для начала работы:</b>
1. Используйте команду /admin
2. Изучите раздел "ℹ️ Информация"
3. Прочитайте правила добавления сборок

<b>Главный администратор:</b> @zavremya
                """
                
                try:
                    await bot.send_message(user_id, welcome_text)
                    logger.info(f"Auto-added admin: {user_id} (@{username})")
                except Exception as e:
                    logger.error(f"Failed to notify new admin {user_id}: {e}")
        
        # 2. ПОЛЬЗОВАТЕЛЬ ВЫШЕЛ ИЗ ЧАТА ИЛИ БЫЛ ИСКЛЮЧЕН
        elif new_status in ["left", "kicked"] and old_status in ["member", "administrator", "creator"]:
            # Пользователь вышел из чата или был исключен
            # Автоматически снимаем права администратора
            
            # Не снимаем права у главного админа
            if user_id == ADMIN_ID:
                return
            
            # Проверяем был ли админом
            if str(user_id) in admins:
                admin_data = admins[str(user_id)]
                
                # Сохраняем данные для истории
                removed_admin_info = {
                    "username": admin_data.get("username", username),
                    "removed_at": datetime.now().isoformat(),
                    "was_in_chat": True,
                    "builds_added": admin_data.get("builds_added", 0)
                }
                
                # Удаляем из администраторов
                del admins[str(user_id)]
                await save_admins(admins)
                
                # Отправляем уведомление
                goodbye_text = f"""
⚠️ <b>Вы вышли из чата сотрудников</b>

Ваши права администратора бота были автоматически сняты.

📋 <b>Вы больше не можете:</b>
• Использовать команду /admin
• Добавлять новые сборки
• Просматривать статистику

✅ <b>Вы сохраняете:</b>
• Доступ ко всем функциям обычного пользователя
• Возможность скачивать сборки
• Все ранее добавленные вами сборки остаются в боте

<b>Для восстановления прав:</b>
Вернитесь в чат сотрудников или обратитесь к главному администратору.

📞 <b>Контакты:</b> @zavremya
                """
                
                try:
                    await bot.send_message(user_id, goodbye_text)
                    logger.info(f"Auto-removed admin: {user_id} (@{username})")
                except Exception as e:
                    logger.error(f"Failed to notify removed admin {user_id}: {e}")
    
    except Exception as e:
        logger.error(f"Error in chat_member_handler: {e}")

class AdvertisementStates(StatesGroup):
    waiting_for_confirmation = State()

@dp.message(Command("advert"))
async def cmd_advert_simple(message: Message, state: FSMContext):
    """Простая команда для рассылки с использованием state"""
    user_id = message.from_user.id
    
    if user_id != ADMIN_ID:
        await message.answer("❌ Только главный администратор может использовать эту команду.")
        return
    
    args = message.text.split(maxsplit=1)
    
    if len(args) > 1:
        advert_text = args[1]
        
        # Сохраняем текст в state
        await state.update_data(advert_text=advert_text)
        await state.set_state(AdvertisementStates.waiting_for_confirmation)
        
        # Предпросмотр
        preview = f"""
🔔 <b>ПРЕДПРОСМОТР РАССЫЛКИ</b>

{advert_text[:300]}{'...' if len(advert_text) > 300 else ''}

📅 {datetime.now().strftime('%d.%m.%Y %H:%M')}

<b>Отправить всем пользователям?</b>
"""
        
        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="✅ Отправить", callback_data="confirm_advert")],
            [InlineKeyboardButton(text="❌ Отменить", callback_data="cancel_advert")]
        ])
        
        await message.answer(preview, reply_markup=keyboard)
        
    else:
        await message.answer("Использование: /advert [текст]")

@dp.callback_query(F.data == "confirm_advert", AdvertisementStates.waiting_for_confirmation)
async def confirm_advert_simple(callback: CallbackQuery, state: FSMContext):
    """Подтверждение рассылки"""
    # Получаем текст из state
    data = await state.get_data()
    advert_text = data.get('advert_text', '')
    
    if not advert_text:
        await callback.answer("❌ Текст не найден")
        return
    
    # Очищаем state
    await state.clear()
    
    # Отправляем
    await send_advertisement_simple(callback, advert_text)

async def send_advertisement_simple(callback: CallbackQuery, advert_text: str):
    """Простая отправка рассылки"""
    await callback.message.edit_text("📤 <b>Отправляю...</b>")
    
    users = await get_users()
    sent = 0
    
    message_text = f"🔔 ОБЪЯВЛЕНИЕ ОТ ГЛАВНОГО АДМИНИСТРАТОРА 🔔\n\n{advert_text}\n\n📅 {datetime.now().strftime('%d.%m.%Y %H:%M')}"
    
    for user_id_str in users:
        try:
            await bot.send_message(int(user_id_str), message_text)
            sent += 1
        except:
            pass
    
    await callback.message.edit_text(f"✅ <b>Готово!</b>\n\nОтправлено {sent} пользователям")
    await callback.answer()

@dp.message(AdminManagementStates.waiting_for_admin_username)
async def process_add_admin(message: Message, state: FSMContext):
    """Обработка добавления администратора"""
    username = message.text.lstrip('@')
    
    # Ищем пользователя по юзернейму в users.json
    users = await get_users()
    user_id_to_add = None
    
    for uid, user_data in users.items():
        if user_data.get("username") == username:
            user_id_to_add = int(uid)
            break
    
    if user_id_to_add:
        admins = await get_admins()
        
        if str(user_id_to_add) in admins:
            await message.answer(f"❌ Пользователь @{username} уже является администратором.")
        else:
            admins[str(user_id_to_add)] = {
                "username": username,
                "builds_added": 0,
                "added_by": message.from_user.id,
                "added_at": datetime.now().isoformat()
            }
            await save_admins(admins)
            await message.answer(f"✅ Пользователь @{username} добавлен в администраторы.")
            
            # Уведомляем нового администратора
            try:
                await bot.send_message(
                    user_id_to_add,
                    "🎉 Вас добавили в администраторы бота со сборками Amazing Online!\n\n"
                    "Теперь вы можете:\n"
                    "• Добавлять сборки (требуют модерации)\n"
                    "• Просматривать статистику\n"
                    "• Использовать команду /admin для доступа к панели"
                )
            except:
                pass
    else:
        await message.answer(f"❌ Пользователь с юзернеймом @{username} не найден в базе бота.")
    
    await state.clear()
    await message.answer("⚙️ <b>Панель администратора</b>", reply_markup=get_admin_keyboard())

@dp.callback_query(F.data.startswith("advert_confirm_"))
async def confirm_advertisement(callback: CallbackQuery):
    """Подтверждение и отправка рассылки"""
    if callback.from_user.id != ADMIN_ID:
        await callback.answer("❌ Только главный администратор может отправлять рассылки.")
        return
    
    # Извлекаем закодированный текст
    encoded_text = callback.data.replace("advert_confirm_", "")
    advert_text = decode_advert_text(encoded_text)
    
    if not advert_text:
        await callback.answer("❌ Ошибка: не удалось расшифровать текст объявления.")
        return
    
    # Обновляем сообщение о начале отправки
    await callback.message.edit_text("🔄 <b>Начинаю рассылку...</b>\n\nПожалуйста, подождите.")
    
    # Получаем всех пользователей
    users = await get_users()
    total_users = len(users)
    
    if total_users == 0:
        await callback.message.edit_text("❌ <b>Ошибка:</b> Нет пользователей для рассылки.")
        await callback.answer()
        return
    
    # Формируем финальное сообщение ТОЧНО как нужно
    final_message = f"""
🔔 <b>ОБЪЯВЛЕНИЕ ОТ ГЛАВНОГО АДМИНИСТРАТОРА</b> 🔔

{advert_text}

📅 <i>{datetime.now().strftime('%d.%m.%Y %H:%M')}</i>
"""
    
    # Статистика отправки
    sent_count = 0
    blocked_count = 0
    failed_count = 0
    
    # Создаем сообщение с прогрессом
    progress_msg = await callback.message.answer("▫️▫️▫️▫️▫️▫️▫️▫️▫️▫️ 0%")
    
    # Отправляем каждому пользователю
    user_ids = list(users.keys())
    
    for i, user_id_str in enumerate(user_ids):
        try:
            user_id = int(user_id_str)
            await bot.send_message(user_id, final_message, parse_mode="HTML")
            sent_count += 1
            
            # Обновляем прогресс каждые 10% или в конце
            if i % max(1, total_users // 10) == 0 or i == total_users - 1:
                progress = min(100, int((i + 1) / total_users * 100))
                progress_bar = "█" * (progress // 10) + "▫️" * (10 - progress // 10)
                await progress_msg.edit_text(f"{progress_bar} {progress}%")
            
            # Задержка чтобы не превысить лимиты Telegram (30 сообщений/сек)
            if i % 20 == 0:
                await asyncio.sleep(0.5)
                
        except Exception as e:
            error_msg = str(e).lower()
            if "blocked" in error_msg or "forbidden" in error_msg:
                blocked_count += 1
            else:
                failed_count += 1
                logger.error(f"Ошибка отправки пользователю {user_id_str}: {e}")
    
    # Отчет об отправке
    report_text = f"""
✅ <b>РАССЫЛКА ЗАВЕРШЕНА</b>

📊 <b>Результаты:</b>
• Всего пользователей: {total_users}
• Успешно отправлено: {sent_count} ✅
• Заблокировали бота: {blocked_count} 🚫
• Ошибок отправки: {failed_count} ❌
• Процент доставки: {sent_count/total_users*100:.1f}%

⏱️ <b>Время завершения:</b> {datetime.now().strftime('%H:%M:%S')}

📈 <b>Статистика:</b>
• Активных пользователей: {sent_count}
• Неактивных (заблокировали): {blocked_count}
"""
    
    # Обновляем основное сообщение
    await callback.message.edit_text(report_text)
    
    # Удаляем сообщение с прогрессом
    try:
        await progress_msg.delete()
    except:
        pass
    
    await callback.answer(f"Рассылка отправлена {sent_count} пользователям")

@dp.callback_query(F.data == "advert_cancel")
async def cancel_advertisement(callback: CallbackQuery):
    """Отмена рассылки"""
    if callback.from_user.id != ADMIN_ID:
        await callback.answer("❌ Только главный администратор может отменять рассылки.")
        return
    
    await callback.message.edit_text("❌ <b>Рассылка отменена.</b>\n\nОбъявление не было отправлено.")
    await callback.answer("Рассылка отменена")

@dp.callback_query(F.data == "remove_admin")
async def remove_admin_start(callback: CallbackQuery, state: FSMContext):
    """Начало удаления администратора"""
    if not await is_main_admin(callback.from_user.id):
        await callback.answer("У вас нет прав для удаления администраторов.")
        return
    
    admins = await get_admins()
    
    if not admins:
        await callback.message.edit_text(
            "Нет администраторов для удаления.",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="🔙 Назад", callback_data="admin_panel")]
            ])
        )
        return
    
    # Создаем клавиатуру со списком администраторов для удаления
    keyboard_buttons = []
    for admin_id, admin_data in admins.items():
        username = admin_data.get("username", "Неизвестно")
        keyboard_buttons.append([
            InlineKeyboardButton(
                text=f"🗑️ @{username}",
                callback_data=f"remove_admin_{admin_id}"
            )
        ])
    
    keyboard_buttons.append([
        InlineKeyboardButton(text="🔙 Назад", callback_data="admin_panel")
    ])
    
    await callback.message.edit_text(
        "Выберите администратора для удаления:",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=keyboard_buttons)
    )
    await callback.answer()

@dp.callback_query(F.data.startswith("remove_admin_"))
async def process_remove_admin(callback: CallbackQuery):
    """Обработка удаления администратора"""
    if not await is_main_admin(callback.from_user.id):
        await callback.answer("У вас нет прав для удаления администраторов.")
        return
    
    admin_id = callback.data.split("_")[-1]
    
    # Нельзя удалить самого себя (главного админа)
    if int(admin_id) == ADMIN_ID:
        await callback.answer("Нельзя удалить главного администратора.")
        return
    
    admins = await get_admins()
    
    if admin_id in admins:
        username = admins[admin_id].get("username", "Неизвестно")
        del admins[admin_id]
        await save_admins(admins)
        
        await callback.message.edit_text(
            f"✅ Администратор @{username} удален.",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="🔙 Назад", callback_data="admin_panel")]
            ])
        )
        
        # Уведомляем бывшего администратора
        try:
            await bot.send_message(
                int(admin_id),
                "⚠️ Ваши права администратора в боте со сборками Amazing Online были отозваны."
            )
        except:
            pass
    else:
        await callback.answer("Администратор не найден.")
    
    await callback.answer()

@dp.callback_query(F.data == "admin_panel")
async def admin_panel_callback(callback: CallbackQuery):
    """Обработчик кнопки админ-панели"""
    # Разрешаем доступ всем админам
    if await is_admin(callback.from_user.id):
        # Пробуем редактировать сообщение, если это возможно
        try:
            await callback.message.edit_text(
                "⚙️ <b>Панель администратора</b>",
                reply_markup=get_admin_keyboard()
            )
        except:
            # Если не можем редактировать, отправляем новое сообщение
            await callback.message.answer(
                "⚙️ <b>Панель администратора</b>",
                reply_markup=get_admin_keyboard()
            )
    await callback.answer()

@dp.callback_query(F.data == "add_build")
async def add_build_start(callback: CallbackQuery, state: FSMContext):
    """Начало добавления сборки"""
    user_id = callback.from_user.id
    
    if not await is_admin(user_id):
        await callback.answer("У вас нет прав для добавления сборок.")
        return
    
    # Проверяем, может ли пользователь добавлять платные сборки
    can_add_paid = user_id == ADMIN_ID  # Только главный админ
    
    # Устанавливаем состояние правильно для aiogram 3.x
    await state.set_state(AdminStates.waiting_for_category)
    
    if can_add_paid:
        # Главному админу показываем оба варианта
        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [
                InlineKeyboardButton(text="🆓 Бесплатная", callback_data="category_free"),
                InlineKeyboardButton(text="💰 Платная", callback_data="category_paid")
            ],
            [
                InlineKeyboardButton(text="🔙 Отмена", callback_data="admin_panel")
            ]
        ])
    else:
        # Обычным админам показываем только бесплатный вариант
        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [
                InlineKeyboardButton(text="🆓 Бесплатная сборка", callback_data="category_free")
            ],
            [
                InlineKeyboardButton(text="🔙 Отмена", callback_data="admin_panel")
            ]
        ])
    
    await callback.message.edit_text(
        "Выберите тип сборки:" if can_add_paid else "Добавление сборки:",
        reply_markup=keyboard
    )
    await callback.answer()

@dp.callback_query(AdminStates.waiting_for_category, F.data.startswith("category_"))
async def process_category(callback: CallbackQuery, state: FSMContext):
    """Обработка выбора категории"""
    user_id = callback.from_user.id
    category = callback.data.split("_")[1]
    
    # Проверяем права на создание платных сборок
    if category == "paid" and user_id != ADMIN_ID:
        await callback.answer("❌ Обычные администраторы не могут создавать платные сборки.")
        return
    
    await state.update_data(category=BuildCategory.PAID if category == "paid" else BuildCategory.FREE)
    await state.set_state(AdminStates.waiting_for_title)
    
    await callback.message.edit_text(
        f"Добавление {'платной' if category == 'paid' else 'бесплатной'} сборки\n\n"
        "Введите название сборки:"
    )
    await callback.answer()

@dp.message(AdminStates.waiting_for_title)
async def process_title(message: Message, state: FSMContext):
    """Обработка названия сборки"""
    await state.update_data(title=message.text)
    await state.set_state(AdminStates.waiting_for_author)
    await message.answer("Введите автора сборки (никнейм ютубера):")

@dp.message(AdminStates.waiting_for_author)
async def process_author(message: Message, state: FSMContext):
    """Обработка автора сборки"""
    await state.update_data(author=message.text)
    await state.set_state(AdminStates.waiting_for_description)
    await message.answer("Введите описание сборки:")

@dp.message(AdminStates.waiting_for_description)
async def process_description(message: Message, state: FSMContext):
    """Обработка описания сборки"""
    await state.update_data(description=message.text)
    await state.set_state(AdminStates.waiting_for_cover)
    await message.answer("Отправьте обложку (превью) сборки (фото):")

@dp.message(AdminStates.waiting_for_cover, F.photo)
async def process_cover(message: Message, state: FSMContext):
    """Обработка обложки сборки"""
    await state.update_data(cover_url=message.photo[-1].file_id)
    await state.set_state(AdminStates.waiting_for_link)
    await message.answer("Введите ссылку для скачивания сборки:")

@dp.message(AdminStates.waiting_for_link)
async def process_link(message: Message, state: FSMContext):
    """Обработка ссылки на скачивание"""
    await state.update_data(download_link=message.text)
    
    data = await state.get_data()
    user_id = message.from_user.id
    
    # Если обычный админ пытается создать платную сборку, прерываем
    if data.get('category') == BuildCategory.PAID and user_id != ADMIN_ID:
        await message.answer(
            "❌ <b>Ошибка:</b> Обычные администраторы не могут создавать платные сборки.\n\n"
            "Пожалуйста, начните добавление заново и выберите бесплатную сборку.",
            reply_markup=get_main_keyboard(user_id)
        )
        await state.clear()
        return
    
    if data['category'] == BuildCategory.PAID:
        await state.set_state(AdminStates.waiting_for_price)
        await message.answer("Введите цену сборки в рублях:")
    else:
        # Завершаем добавление бесплатной сборки
        await finish_build_creation(message, state, data)

async def finish_build_creation(message: Message, state: FSMContext, data: dict):
    """Завершение создания сборки"""
    user_id = message.from_user.id
    
    build = Build(
        title=data['title'],
        author=data['author'],
        description=data['description'],
        cover_url=data['cover_url'],
        download_link=data['download_link'],
        category=data['category'],
        price=data.get('price', 0),
        contact=data.get('contact', ''),
        added_by=user_id,
        added_at=datetime.now().isoformat()
    )
    
    # Если главный админ - публикуем сразу
    if user_id == ADMIN_ID:
        builds = await get_builds()
        builds[build.build_id] = build.to_dict()
        await save_builds(builds)
        await update_stats("builds_added")
        
        # Обновляем статистику админа
        admins = await get_admins()
        admin_key = str(user_id)
        if admin_key not in admins:
            admins[admin_key] = {"builds_added": 0, "username": message.from_user.username}
        admins[admin_key]["builds_added"] = admins[admin_key].get("builds_added", 0) + 1
        await save_admins(admins)
        
        await message.answer(f"✅ Сборка '{build.title}' успешно добавлена!", 
                           reply_markup=get_main_keyboard(user_id))
    
    # Если обычный админ - отправляем на модерацию
    else:
        pending = await get_pending_builds()
        pending[build.build_id] = build.to_dict()
        pending[build.build_id]["requester_id"] = user_id
        pending[build.build_id]["requester_username"] = message.from_user.username or "Неизвестно"
        await save_pending_builds(pending)
        
        # Уведомляем главного админа
        preview_text = f"""
🆕 <b>Новая сборка на модерации:</b>

📁 <b>Название:</b> {build.title}
👤 <b>Автор:</b> {build.author}
💰 <b>Тип:</b> {'Платная' if build.category == BuildCategory.PAID else 'Бесплатная'}
👨‍💼 <b>Добавил:</b> @{message.from_user.username or 'Неизвестно'}
        """
        
        await bot.send_photo(
            ADMIN_ID,
            build.cover_url,
            caption=preview_text,
            reply_markup=get_review_build_keyboard(build.build_id)
        )
        
        await message.answer("✅ Сборка отправлена на модерацию. Ожидайте подтверждения.",
                           reply_markup=get_main_keyboard(user_id))
    
    await state.clear()

@dp.message(AdminStates.waiting_for_price)
async def process_price(message: Message, state: FSMContext):
    """Обработка цены сборки"""
    user_id = message.from_user.id
    
    # Проверяем права на создание платных сборок
    if user_id != ADMIN_ID:
        await message.answer("❌ Обычные администраторы не могут создавать платные сборки.")
        await state.clear()
        return
    
    try:
        price = int(message.text)
        if price <= 0:
            raise ValueError
        
        await state.update_data(price=price)
        await state.set_state(AdminStates.waiting_for_contact)
        await message.answer("Введите контакт для связи (юзернейм в Telegram):")
    
    except ValueError:
        await message.answer("Пожалуйста, введите корректную цену (целое число больше 0):")

@dp.message(AdminStates.waiting_for_contact)
async def process_contact(message: Message, state: FSMContext):
    """Обработка контакта для связи"""
    user_id = message.from_user.id
    
    # Проверяем права на создание платных сборок
    if user_id != ADMIN_ID:
        await message.answer("❌ Обычные администраторы не могут создавать платные сборки.")
        await state.clear()
        return
    
    contact = message.text if message.text.startswith('@') else f"@{message.text}"
    await state.update_data(contact=contact)
    
    data = await state.get_data()
    await finish_build_creation(message, state, data)

@dp.callback_query(F.data == "stats")
async def show_stats(callback: CallbackQuery):
    """Показать статистику"""
    if not await is_admin(callback.from_user.id):
        await callback.answer("У вас нет прав для просмотра статистики.")
        return
    
    stats = await get_stats()
    users = await get_users()
    builds = await get_builds()
    
    paid_builds = 0
    free_builds = 0
    for build in builds.values():
        if build.get("category") == "paid":
            paid_builds += 1
        else:
            free_builds += 1
    
    active_today = 0
    for user in users.values():
        reg_date = datetime.fromisoformat(user.get('registered_at', '2020-01-01'))
        if reg_date > datetime.now() - timedelta(days=1):
            active_today += 1
    
    stats_text = f"""
<b>📊 Статистика бота:</b>

👥 <b>Пользователи:</b>
• Всего пользователей: {stats['total_users']}
• Активных сегодня: {active_today}
• Всего скачиваний: {stats['total_downloads']}

📦 <b>Сборки:</b>
• Всего сборок: {stats.get('builds_added', 0)}
• Бесплатных: {free_builds}
• Платных: {paid_builds}

💰 <b>Финансы:</b>
• Сбросов лимита: {stats.get('total_resets', 0)}
• Примерный доход: {stats.get('total_resets', 0) * 100} руб.
    """
    
    await callback.message.edit_text(
        stats_text,
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="🔙 Назад", callback_data="admin_panel")]
        ])
    )
    await callback.answer()

@dp.callback_query(F.data == "admins_list")
async def show_admins(callback: CallbackQuery):
    """Показать список администраторов"""
    if not await is_main_admin(callback.from_user.id):
        await callback.answer("У вас нет прав для просмотра списка администраторов.")
        return
    
    admins = await get_admins()
    
    if not admins:
        admins_text = "Нет зарегистрированных администраторов."
    else:
        admins_text = "<b>👨‍💼 Администраторы:</b>\n\n"
        for admin_id, admin_data in admins.items():
            username = admin_data.get("username", "Неизвестно")
            builds_added = admin_data.get("builds_added", 0)
            admins_text += f"• @{username}\n  Сборок добавлено: {builds_added}\n\n"
    
    await callback.message.edit_text(
        admins_text,
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="🔙 Назад", callback_data="admin_panel")]
        ])
    )
    await callback.answer()

@dp.callback_query(F.data == "reset_limit")
async def reset_limit_menu(callback: CallbackQuery, state: FSMContext):
    """Меню сброса лимита"""
    if not await is_main_admin(callback.from_user.id):
        await callback.answer("У вас нет прав для сброса лимитов.")
        return
    
    await state.set_state(AdminStates.waiting_for_username)
    
    await callback.message.edit_text(
        "Введите юзернейм пользователя (без @):"
    )
    await callback.answer()

@dp.message(AdminStates.waiting_for_username)
async def process_reset_username(message: Message, state: FSMContext):
    """Обработка юзернейма для сброса лимита"""
    username = message.text.lstrip('@')
    
    users = await get_users()
    user_id_to_reset = None
    
    # Ищем пользователя по юзернейму
    for uid, user_data in users.items():
        if user_data.get("username") == username:
            user_id_to_reset = int(uid)
            break
    
    if user_id_to_reset:
        if await reset_user_limit(user_id_to_reset):
            await message.answer(f"✅ Лимит для пользователя @{username} успешно сброшен.")
            
            # Уведомляем пользователя
            try:
                await bot.send_message(
                    user_id_to_reset,
                    "🎉 Ваш лимит на скачивание сборок был сброшен администратором!"
                )
            except:
                pass
        else:
            await message.answer("❌ Не удалось сбросить лимит.")
    else:
        await message.answer("❌ Пользователь с таким юзернеймом не найден.")
    
    await state.clear()
    await message.answer("⚙️ <b>Панель администратора</b>", reply_markup=get_admin_keyboard())

@dp.callback_query(F.data == "pending_builds")
async def show_pending_builds(callback: CallbackQuery):
    """Показать ожидающие сборки"""
    if not await is_main_admin(callback.from_user.id):
        await callback.answer("У вас нет прав для просмотра ожидающих сборок.")
        return
    
    pending = await get_pending_builds()
    
    if not pending:
        try:
            await callback.message.edit_text(
                "Нет сборок, ожидающих модерации.",
                reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                    [InlineKeyboardButton(text="🔙 Назад", callback_data="admin_panel")]
                ])
            )
        except:
            await callback.message.answer(
                "Нет сборок, ожидающих модерации.",
                reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                    [InlineKeyboardButton(text="🔙 Назад", callback_data="admin_panel")]
                ])
            )
        return
    
    try:
        await callback.message.edit_text(
            f"<b>Ожидающие модерации сборки ({len(pending)}):</b>",
            reply_markup=get_pending_builds_keyboard(pending)
        )
    except:
        await callback.message.answer(
            f"<b>Ожидающие модерации сборки ({len(pending)}):</b>",
            reply_markup=get_pending_builds_keyboard(pending)
        )
    
    await callback.answer()

@dp.callback_query(F.data.startswith("review_build_"))
async def review_build(callback: CallbackQuery):
    """Просмотр сборки для модерации"""
    if not await is_main_admin(callback.from_user.id):
        await callback.answer("У вас нет прав для модерации сборок.")
        return
    
    build_id = callback.data.split("_")[-1]
    pending = await get_pending_builds()
    build_data = pending.get(build_id)
    
    if not build_data:
        await callback.answer("Сборка не найдена.")
        return
    
    build = Build.from_dict(build_data)
    requester = f"@{build_data.get('requester_username', 'Неизвестно')}"
    
    preview_text = f"""
<b>📋 Сборка на модерации:</b>

📁 <b>Название:</b> {build.title}
👤 <b>Автор:</b> {build.author}
📝 <b>Описание:</b> {build.description[:200]}...
💰 <b>Тип:</b> {'Платная' if build.category == BuildCategory.PAID else 'Бесплатная'}
💳 <b>Цена:</b> {build.price if build.category == BuildCategory.PAID else 'Бесплатно'}
📞 <b>Контакт:</b> {build.contact if build.category == BuildCategory.PAID else 'Не требуется'}
👨‍💼 <b>Добавил:</b> {requester}
🕒 <b>Дата добавления:</b> {build.added_at[:10]}
🔗 <b>Ссылка:</b> {build.download_link[:50]}...
    """
    
    await bot.send_photo(
        callback.from_user.id,
        build.cover_url,
        caption=preview_text,
        reply_markup=get_review_build_keyboard(build_id)
    )
    
    await callback.answer()

@dp.callback_query(F.data.startswith("approve_"))
async def approve_build(callback: CallbackQuery):
    """Одобрение сборки"""
    if not await is_main_admin(callback.from_user.id):
        await callback.answer("У вас нет прав для одобрения сборок.")
        return
    
    build_id = callback.data.split("_")[-1]
    pending = await get_pending_builds()
    build_data = pending.get(build_id)
    
    if not build_data:
        await callback.answer("Сборка не найдена.")
        return
    
    # Добавляем в основные сборки
    builds = await get_builds()
    builds[build_id] = build_data
    await save_builds(builds)
    await update_stats("builds_added")
    
    # Обновляем статистику админа
    requester_id = build_data.get("requester_id")
    if requester_id:
        admins = await get_admins()
        admin_key = str(requester_id)
        if admin_key not in admins:
            admins[admin_key] = {"builds_added": 0, "username": build_data.get("requester_username", "")}
        admins[admin_key]["builds_added"] = admins[admin_key].get("builds_added", 0) + 1
        await save_admins(admins)
    
    # Удаляем из ожидающих
    del pending[build_id]
    await save_pending_builds(pending)
    
    # Уведомляем админа, который добавил сборку
    try:
        await bot.send_message(
            requester_id,
            f"✅ Ваша сборка '{build_data['title']}' была одобрена и опубликована!"
        )
    except:
        pass
    
    await callback.answer("Сборка одобрена и опубликована.")
    
    # Обновляем сообщение с модерацией
    try:
        await callback.message.edit_caption(
            caption=f"✅ Сборка '{build_data['title']}' одобрена и опубликована.",
            reply_markup=None
        )
    except:
        pass

@dp.callback_query(F.data.startswith("reject_"))
async def reject_build(callback: CallbackQuery):
    """Отклонение сборки"""
    if not await is_main_admin(callback.from_user.id):
        await callback.answer("У вас нет прав для отклонения сборок.")
        return
    
    build_id = callback.data.split("_")[-1]
    pending = await get_pending_builds()
    build_data = pending.get(build_id)
    
    if not build_data:
        await callback.answer("Сборка не найдена.")
        return
    
    # Удаляем из ожидающих
    del pending[build_id]
    await save_pending_builds(pending)
    
    # Уведомляем админа, который добавил сборку
    requester_id = build_data.get("requester_id")
    if requester_id:
        try:
            await bot.send_message(
                requester_id,
                f"❌ Ваша сборка '{build_data['title']}' была отклонена."
            )
        except:
            pass
    
    await callback.answer("Сборка отклонена.")
    
    # Обновляем сообщение с модерацией
    try:
        await callback.message.edit_caption(
            caption=f"❌ Сборка '{build_data['title']}' отклонена.",
            reply_markup=None
        )
    except:
        pass

@dp.callback_query(F.data == "all_builds")
async def show_all_builds(callback: CallbackQuery):
    """Показать все сборки"""
    if not await is_admin(callback.from_user.id):
        await callback.answer("У вас нет прав для просмотра всех сборок.")
        return
    
    builds_data = await get_builds()
    builds = []
    
    for build_id, build_data in builds_data.items():
        build = Build.from_dict(build_data)
        builds.append(build)
    
    if not builds:
        await callback.message.edit_text(
            "Нет доступных сборок.",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="🔙 Назад", callback_data="admin_panel")]
            ])
        )
        return
    
    builds_list = "<b>📋 Все сборки:</b>\n\n"
    for i, build in enumerate(builds, 1):
        builds_list += f"<b>{i}. {build.title}</b> ({build.author})\n"
        builds_list += f"   Тип: {'💰 Платная' if build.category == BuildCategory.PAID else '🆓 Бесплатная'}\n"
        builds_list += f"   Добавлена: {build.added_at[:10]}\n\n"
    
    await callback.message.edit_text(
        builds_list[:4000],  # Ограничение Telegram
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="🔙 Назад", callback_data="admin_panel")]
        ])
    )
    await callback.answer()

@dp.callback_query(F.data.startswith("build_"))
async def show_build_details(callback: CallbackQuery):
    """Показать детали сборки"""
    build_id = callback.data.split("_")[-1]
    builds = await get_builds()
    build_data = builds.get(build_id)
    
    if not build_data:
        await callback.answer("Сборка не найдена.")
        return
    
    build = Build.from_dict(build_data)
    user_id = callback.from_user.id
    
    # Проверяем лимит только для бесплатных сборок
    can_download_result = True
    time_left = ""
    
    if build.category == BuildCategory.FREE:
        can_download_result, time_left, next_available = await can_download(user_id)
    
    # Формируем описание
    description = f"""
<b>📁 {build.title}</b>
<b>👤 Автор:</b> {build.author}

<b>📝 Описание:</b>
{build.description}

<b>💰 Статус:</b> {'Платная сборка' if build.category == BuildCategory.PAID else 'Бесплатная сборка'}
    """
    
    if build.category == BuildCategory.PAID:
        description += f"\n<b>💳 Цена:</b> {build.price} рублей"
        description += f"\n<b>📞 Контакт для связи:</b> {build.contact}"
        description += "\n\n<b>Для покупки свяжитесь с продавцом</b>"
    else:
        if not can_download_result:
            description += f"\n\n⏳ <b>Вы сможете скачать эту сборку через:</b> {time_left}"
        else:
            description += "\n\n✅ <b>Готово к скачиванию</b>"
    
    try:
        # Отправляем фото с описанием
        await bot.send_photo(
            callback.from_user.id,
            build.cover_url,
            caption=description,
            reply_markup=get_build_details_keyboard(build, can_download_result)
        )
        
        # Если это бесплатная сборка и можно скачать, обновляем время
        if can_download_result and build.category == BuildCategory.FREE:
            await update_last_download(user_id, build_id)
        
        await callback.answer()
    except Exception as e:
        logger.error(f"Ошибка при отправке деталей сборки: {e}")
        await callback.answer("Произошла ошибка при отображении сборки", show_alert=True)

@dp.callback_query(F.data == "reset_limit_payment")
async def reset_limit_payment(callback: CallbackQuery):
    """Сброс лимита за плату"""
    user_id = callback.from_user.id
    
    payment_text = """
<b>🔄 Сброс лимита загрузки</b>

Для сброса лимита на скачивание сборок:

1. <b>Оплатите 100 рублей</b>
2. <b>Отправьте скриншот об оплате</b>
3. <b>Укажите свой юзернейм</b>

📞 <b>Контакт для оплаты:</b> @zavremya

После оплаты ваш лимит будет сброшен в течение 24 часов.
    """
    
    await bot.send_message(user_id, payment_text)
    await callback.answer("Инструкции по сбросу лимита отправлены.")

@dp.callback_query(F.data.startswith("page_"))
async def change_page(callback: CallbackQuery):
    """Смена страницы со сборками"""
    page = int(callback.data.split("_")[-1])
    
    builds_data = await get_builds()
    builds = []
    
    for build_id, build_data in builds_data.items():
        build = Build.from_dict(build_data)
        builds.append(build)
    
    try:
        await callback.message.edit_reply_markup(
            reply_markup=get_builds_keyboard(builds, page)
        )
    except:
        # Если не можем редактировать, отправляем новое сообщение
        await callback.message.answer(
            f"<b>Доступные сборки ({len(builds)}):</b>",
            reply_markup=get_builds_keyboard(builds, page)
        )
    
    await callback.answer()

@dp.callback_query(F.data == "back_to_builds")
async def back_to_builds(callback: CallbackQuery):
    """Возврат к списку сборок"""
    builds_data = await get_builds()
    builds = []
    
    for build_id, build_data in builds_data.items():
        build = Build.from_dict(build_data)
        builds.append(build)
    
    try:
        await callback.message.edit_text(
            f"<b>Доступные сборки ({len(builds)}):</b>",
            reply_markup=get_builds_keyboard(builds)
        )
    except:
        # Если сообщение уже отредактировано или удалено, отправляем новое
        await callback.message.answer(
            f"<b>Доступные сборки ({len(builds)}):</b>",
            reply_markup=get_builds_keyboard(builds)
        )
    
    await callback.answer()

@dp.callback_query(F.data == "main_menu")
async def back_to_main_menu(callback: CallbackQuery):
    """Возврат в главное меню"""
    user_id = callback.from_user.id
    stats = await get_stats()
    
    welcome_text = f"""
<b>👋 Добро пожаловать в сборник Amazing Online!</b>

Здесь собраны лучшие сборки от популярных ютуберов по игре Amazing Online.

📊 <b>Статистика бота:</b>
• Пользователей: {stats['total_users']}
• Сборок скачано: {stats['total_downloads']}
• Доступных сборок: {stats.get('builds_added', 0)}

💡 <b>Возможности:</b>
• Выбор сборки 1 раз в 24 часа
• Платные и бесплатные сборки
• Возможность сбросить лимит

<b>Для начала работы нажмите "📦 Доступные сборки"</b>
    """
    
    await callback.message.edit_text(
        welcome_text,
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="📦 Доступные сборки", callback_data="back_to_builds")],
            [InlineKeyboardButton(text="ℹ️ Информация", callback_data="show_info")],
            [
                InlineKeyboardButton(text="👨‍💼 Вакансии", callback_data="vacancies_info"),
                InlineKeyboardButton(text="📢 Реклама", callback_data="advertisement_info")
            ],
            [InlineKeyboardButton(text="🎮 Для ютуберов", callback_data="youtubers_info")],
            ([InlineKeyboardButton(text="⚙️ Админ-панель", callback_data="admin_panel")] 
             if await is_main_admin(user_id) else [])
        ])
    )
    
    await callback.answer()

@dp.callback_query(F.data.in_(["vacancies_info", "advertisement_info", "youtubers_info", "show_info"]))
async def info_callbacks(callback: CallbackQuery):
    """Обработка информационных callback'ов"""
    data = callback.data
    
    if data == "vacancies_info":
        vacancies_text = """
<b>👨‍💼 Требуются администраторы</b>

Мы ищем активных администраторов для развития нашего бота со сборками Amazing Online.

<b>📌 Обязанности:</b>
• Публикация новых сборок в боте
• Реклама и продвижение бота
• Модерация контента
• Взаимодействие с пользователями

<b>💰 Оплата:</b>
• Уникальные сборки и моды из приват-блоков популярных ютуберов
• Эксклюзивные материалы по игре Amazing Online
• При активной работе - зарплата в реальных деньгах
• Бонусы за достижение целей

<b>📞 Контакты для отклика:</b>
@zavremya
        """
        await bot.send_message(callback.from_user.id, vacancies_text, reply_markup=get_info_keyboard())
        
    elif data == "advertisement_info":
        ad_text = """
<b>📢 Размещение рекламы в боте</b>

Привлекайте новых подписчиков и продвигайте свой контент через нашего бота!

<b>💰 Стоимость рекламы:</b>
• <b>250 рублей</b> - базовый пакет
• 500 рублей - расширенный пакет
• 1000 рублей - премиум размещение

<b>📞 Контакты для заказа рекламы:</b>
@zavremya
        """
        await bot.send_message(callback.from_user.id, ad_text, reply_markup=get_info_keyboard())
        
    elif data == "youtubers_info":
        youtubers_text = """
<b>🎮 Сотрудничество с ютуберами</b>

Размещайте свои сборки Amazing Online в нашем боте и привлекайте больше зрителей!

<b>📞 Контакты для сотрудничества:</b>
@zavremya
        """
        await bot.send_message(callback.from_user.id, youtubers_text, reply_markup=get_info_keyboard())
        
    elif data == "show_info":
        info_text = """
<b>📢 Информация о боте:</b>

🎮 <b>Сборки Amazing Online</b>
В этом боте собраны лучшие сборки от популярных ютуберов по игре Amazing Online.

⏳ <b>Лимиты</b>
• Вы можете скачать одну сборку бесплатно каждые 24 часа
• Для сброса лимита: 100 рублей
• По вопросам оплаты: @zavremya

<b>Выберите раздел для получения подробной информации:</b>
        """
        await bot.send_message(callback.from_user.id, info_text, reply_markup=get_info_keyboard())
    
    await callback.answer()

# Функция для периодической проверки уведомлений
async def notification_scheduler():
    """Планировщик уведомлений"""
    while True:
        try:
            await check_and_send_notifications()
        except Exception as e:
            logger.error(f"Ошибка в планировщике уведомлений: {e}")
        await asyncio.sleep(60)  # Проверяем каждую минуту

# Запуск бота
async def main():
    """Основная функция запуска бота"""
    # Создаем необходимые файлы, если их нет
    for file in [USERS_FILE, BUILDS_FILE, STATS_FILE, ADMINS_FILE, PENDING_BUILDS_FILE, NOTIFICATIONS_FILE]:
        if not os.path.exists(file):
            async with aiofiles.open(file, 'w', encoding='utf-8') as f:
                await f.write("{}")
    
    # Запускаем планировщик уведомлений
    asyncio.create_task(notification_scheduler())
    
    logger.info("Бот запущен...")
    await dp.start_polling(bot)

if __name__ == '__main__':
    asyncio.run(main())