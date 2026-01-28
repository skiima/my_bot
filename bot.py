import asyncio
import logging
import base64
import time
from typing import Dict, List, Optional, Any, Tuple
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from collections import defaultdict

from aiogram import Bot, Dispatcher, Router, F
from aiogram.types import Message, ReplyKeyboardMarkup, KeyboardButton
from aiogram.filters import Command, CommandStart
from aiogram.enums import ParseMode
from aiogram.client.default import DefaultBotProperties
import aiohttp

# Настройка логирования
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)

# Конфигурация
@dataclass
class Config:
    # Telegram Bot Token
    BOT_TOKEN: str = "8598326938:AAGmui3DA4oRAxN_pQHTenF6L6gEroNFZ9U"
    
    # OpenRouter API
    OPENROUTER_API_KEY: str = "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"
    OPENROUTER_API_URL: str = "https://openrouter.ai/api/v1/chat/completions"
    
    # Админ ID
    ADMIN_ID: int = 8154266510
    
    # Модели
    MODELS: Dict[str, Dict[str, Any]] = field(default_factory=lambda: {
        "gemini": {
            "name": "Google Gemini 3 Flash",
            "id": "google/gemini-3-flash-preview",
            "supports_images": True,
            "description": "Анализ изображений и текста"
        },
        "gpt4": {
            "name": "GPT-4o Mini",
            "id": "openai/gpt-4o-mini",
            "supports_images": True,
            "description": "Универсальная модель"
        },
        "claude": {
            "name": "Claude 4.5 Opus",
            "id": "anthropic/claude-opus-4.5",
            "supports_images": True,
            "description": "Детальный анализ"
        },
        "deepseek": {
            "name": "DeepSeek R1",
            "id": "deepseek/deepseek-r1",
            "supports_images": False,
            "description": "Текстовая модель"
        }
    })
    
    DEFAULT_MODEL: str = "gemini"
    
    # Настройки бота
    MAX_MESSAGE_LENGTH: int = 4000
    MAX_HISTORY_LENGTH: int = 10
    MAX_IMAGE_SIZE_MB: int = 5

config = Config()

# Класс для статистики
class Statistics:
    def __init__(self):
        self.user_first_seen: Dict[int, float] = {}  # user_id: timestamp
        self.user_last_seen: Dict[int, float] = {}   # user_id: timestamp
        self.requests: List[Tuple[int, float]] = []  # (user_id, timestamp)
        self.images_sent: List[Tuple[int, float]] = []  # (user_id, timestamp)
    
    def add_user(self, user_id: int):
        now = time.time()
        if user_id not in self.user_first_seen:
            self.user_first_seen[user_id] = now
        self.user_last_seen[user_id] = now
    
    def add_request(self, user_id: int):
        self.add_user(user_id)
        self.requests.append((user_id, time.time()))
    
    def add_image(self, user_id: int):
        self.add_user(user_id)
        self.images_sent.append((user_id, time.time()))
    
    def get_users_count(self, period_days: Optional[int] = None) -> int:
        if not period_days:
            return len(self.user_first_seen)
        
        cutoff = time.time() - (period_days * 24 * 3600)
        return len([uid for uid, ts in self.user_first_seen.items() if ts >= cutoff])
    
    def get_requests_count(self, period_days: Optional[int] = None) -> int:
        if not period_days:
            return len(self.requests)
        
        cutoff = time.time() - (period_days * 24 * 3600)
        return len([req for req in self.requests if req[1] >= cutoff])
    
    def get_images_count(self, period_days: Optional[int] = None) -> int:
        if not period_days:
            return len(self.images_sent)
        
        cutoff = time.time() - (period_days * 24 * 3600)
        return len([img for img in self.images_sent if img[1] >= cutoff])
    
    def get_active_users_today(self) -> int:
        cutoff = time.time() - (24 * 3600)
        return len([uid for uid, ts in self.user_last_seen.items() if ts >= cutoff])
    
    def get_daily_stats(self) -> Dict[str, int]:
        stats = defaultdict(int)
        cutoff = time.time() - (30 * 24 * 3600)  # 30 дней
        
        for user_id, timestamp in self.requests:
            if timestamp >= cutoff:
                date = datetime.fromtimestamp(timestamp).strftime('%Y-%m-%d')
                stats[date] += 1
        
        return dict(stats)
    
    def get_top_users(self, limit: int = 10) -> List[Tuple[int, int]]:
        user_counts = defaultdict(int)
        for user_id, _ in self.requests:
            user_counts[user_id] += 1
        
        return sorted(user_counts.items(), key=lambda x: x[1], reverse=True)[:limit]

# Инициализация статистики
stats = Statistics()

# Инициализация бота
bot = Bot(
    token=config.BOT_TOKEN,
    default=DefaultBotProperties(parse_mode=ParseMode.HTML)
)
dp = Dispatcher()
router = Router()
dp.include_router(router)

# Хранилище данных
user_conversations: Dict[int, List[Dict]] = {}
user_last_images: Dict[int, Dict] = {}
processing_messages: Dict[int, int] = {}

class AIService:
    """Сервис для работы с AI"""
    
    def __init__(self):
        self.api_key = config.OPENROUTER_API_KEY
        self.api_url = config.OPENROUTER_API_URL
        self.current_model = config.DEFAULT_MODEL
        self.model_info = config.MODELS[self.current_model]
        
        self.headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json"
        }
    
    def set_model(self, model_id: str):
        """Установка модели"""
        if model_id in config.MODELS:
            self.current_model = model_id
            self.model_info = config.MODELS[model_id]
            return True
        return False
    
    def get_model_info(self) -> Dict[str, Any]:
        """Информация о текущей модели"""
        return self.model_info
    
    def get_all_models(self) -> Dict[str, Dict[str, Any]]:
        """Все доступные модели"""
        return config.MODELS
    
    async def encode_image_to_base64(self, image_bytes: bytes) -> str:
        """Кодирование изображения в base64"""
        return base64.b64encode(image_bytes).decode('utf-8')
    
    async def process_image(self, image_bytes: bytes, mime_type: str = "image/jpeg") -> Dict[str, str]:
        """Подготовка изображения для API"""
        if len(image_bytes) > config.MAX_IMAGE_SIZE_MB * 1024 * 1024:
            raise ValueError(f"Максимальный размер {config.MAX_IMAGE_SIZE_MB}MB")
        
        base64_image = await self.encode_image_to_base64(image_bytes)
        
        return {
            "type": "image_url",
            "image_url": {
                "url": f"data:{mime_type};base64,{base64_image}"
            }
        }
    
    async def generate_response(
        self, 
        user_id: int,
        message: str,
        images: Optional[List[Dict]] = None
    ) -> str:
        """Генерация ответа"""
        
        if user_id not in user_conversations:
            user_conversations[user_id] = []
        
        messages = user_conversations[user_id].copy()
        
        if not self.model_info["supports_images"] or not images:
            messages.append({"role": "user", "content": message})
        else:
            content = [{"type": "text", "text": message}]
            for image_data in images:
                content.append(image_data)
            
            messages.append({
                "role": "user",
                "content": content
            })
        
        if len(messages) > config.MAX_HISTORY_LENGTH * 2:
            messages = messages[-(config.MAX_HISTORY_LENGTH * 2):]
        
        payload = {
            "model": self.model_info["id"],
            "messages": messages,
            "temperature": 0.7,
            "max_tokens": 2000,
            "stream": False
        }
        
        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(
                    self.api_url,
                    headers=self.headers,
                    json=payload,
                    timeout=60
                ) as response:
                    
                    if response.status == 200:
                        data = await response.json()
                        assistant_message = data["choices"][0]["message"]["content"]
                        
                        user_conversations[user_id].extend([
                            {"role": "user", "content": message},
                            {"role": "assistant", "content": assistant_message}
                        ])
                        
                        if len(user_conversations[user_id]) > config.MAX_HISTORY_LENGTH * 2:
                            user_conversations[user_id] = user_conversations[user_id][-(config.MAX_HISTORY_LENGTH * 2):]
                        
                        return assistant_message
                    
                    elif response.status == 402:
                        return "⚠️ Нейросеть недоступна. Используйте другую модель."
                    
                    else:
                        error_text = await response.text()
                        logger.error(f"Ошибка API: {response.status} - {error_text}")
                        return f"⚠️ Ошибка. Попробуйте еще раз."
                        
        except aiohttp.ClientError:
            return "⚠️ Ошибка подключения."
        except asyncio.TimeoutError:
            return "⚠️ Превышено время ожидания."
        except Exception as e:
            logger.error(f"Ошибка: {e}")
            return "⚠️ Внутренняя ошибка."
    
    async def clear_history(self, user_id: int) -> None:
        """Очистка истории"""
        if user_id in user_conversations:
            user_conversations[user_id] = []
        if user_id in user_last_images:
            del user_last_images[user_id]

# Инициализация сервиса
ai_service = AIService()

# Клавиатуры
def get_main_keyboard() -> ReplyKeyboardMarkup:
    buttons = [
        [KeyboardButton(text="💬 Задать вопрос"), KeyboardButton(text="📸 Отправить фото")],
        [KeyboardButton(text="🔄 Сменить модель"), KeyboardButton(text="🗑️ Очистить чат")]
    ]
    return ReplyKeyboardMarkup(keyboard=buttons, resize_keyboard=True)

def get_models_keyboard() -> ReplyKeyboardMarkup:
    models = ai_service.get_all_models()
    buttons = []
    
    for model_id, model_info in models.items():
        emoji = "🖼️" if model_info["supports_images"] else "📝"
        text = f"{emoji} {model_info['name']}"
        buttons.append([KeyboardButton(text=text)])
    
    buttons.append([KeyboardButton(text="🔙 Назад")])
    return ReplyKeyboardMarkup(keyboard=buttons, resize_keyboard=True)

def get_admin_keyboard() -> ReplyKeyboardMarkup:
    buttons = [
        [KeyboardButton(text="📊 Статистика"), KeyboardButton(text="💰 Баланс API")],
        [KeyboardButton(text="👥 Топ пользователей"), KeyboardButton(text="📈 Активность")],
        [KeyboardButton(text="🔙 Главное меню")]
    ]
    return ReplyKeyboardMarkup(keyboard=buttons, resize_keyboard=True)

# Обработчики команд
@router.message(CommandStart())
async def cmd_start(message: Message):
    """Команда /start"""
    stats.add_user(message.from_user.id)
    
    model_info = ai_service.get_model_info()
    
    # Проверяем, админ ли это
    is_admin = message.from_user.id == config.ADMIN_ID
    
    welcome_text = (
        "✨ <b>Добро пожаловать в AI Assistant!</b>\n\n"
        "Я могу помочь вам с:\n"
        "• 📝 Ответами на вопросы\n"
        "• 🖼️ Анализом изображений\n"
        "• 🧮 Решением задач\n\n"
        f"<b>Текущая модель:</b> {model_info['name']}\n"
        "<b>Поддержка фото:</b> " + ("✅ Да" if model_info["supports_images"] else "❌ Нет")
    )
    
    if is_admin:
        welcome_text += "\n\n👑 <i>Доступна админ панель: /admin</i>"
    
    await message.answer(welcome_text, reply_markup=get_main_keyboard())

@router.message(Command("admin"))
async def cmd_admin(message: Message):
    """Админ панель - доступна только владельцу"""
    if message.from_user.id != config.ADMIN_ID:
        return
    
    await message.answer(
        "👑 <b>Админ панель</b>\n\n"
        "Выберите действие:",
        reply_markup=get_admin_keyboard()
    )

@router.message(Command("stats"))
async def cmd_stats(message: Message):
    """Быстрая статистика - только для админа"""
    if message.from_user.id != config.ADMIN_ID:
        return
    
    total_users = stats.get_users_count()
    requests_today = stats.get_requests_count(1)
    active_today = stats.get_active_users_today()
    
    quick_stats = (
        "📊 <b>Быстрая статистика</b>\n\n"
        f"👥 Пользователей: {total_users}\n"
        f"📨 Запросов сегодня: {requests_today}\n"
        f"🟢 Активных сегодня: {active_today}\n"
        f"💾 Активных чатов: {len(user_conversations)}\n\n"
        f"🤖 Модель: {ai_service.get_model_info()['name']}"
    )
    
    await message.answer(quick_stats)

@router.message(Command("help"))
async def cmd_help(message: Message):
    """Команда /help"""
    help_text = (
        "🆘 <b>Помощь</b>\n\n"
        "<b>Основные функции:</b>\n"
        "• 💬 Задать вопрос - текстовый диалог\n"
        "• 📸 Отправить фото - анализ изображений\n"
        "• 🔄 Сменить модель - выбор AI модели\n"
        "• 🗑️ Очистить чат - сброс истории\n\n"
        "<b>Для работы с фото:</b>\n"
        "1. Отправьте изображение\n"
        "2. Напишите запрос\n"
        "3. Выберите модель с поддержкой фото\n\n"
        "<b>Примеры запросов:</b>\n"
        "• Что на фото?\n"
        "• Реши задачу\n"
        "• Объясни тему"
    )
    await message.answer(help_text)

@router.message(Command("clear"))
async def cmd_clear(message: Message):
    """Команда /clear"""
    await ai_service.clear_history(message.from_user.id)
    await message.answer("✅ Чат очищен", reply_markup=get_main_keyboard())

@router.message(Command("model"))
async def cmd_model(message: Message):
    """Команда /model"""
    models = ai_service.get_all_models()
    current_model = ai_service.current_model
    
    model_text = "🤖 <b>Выберите модель:</b>\n\n"
    
    for model_id, model_info in models.items():
        emoji = "🖼️" if model_info["supports_images"] else "📝"
        current = " ✅" if model_id == current_model else ""
        model_text += f"{emoji} <b>{model_info['name']}</b>{current}\n"
        model_text += f"   {model_info['description']}\n\n"
    
    await message.answer(model_text, reply_markup=get_models_keyboard())

# Обработчик фотографий
@router.message(F.photo)
async def handle_photo(message: Message):
    """Обработка фотографий"""
    user_id = message.from_user.id
    
    try:
        stats.add_image(user_id)
        
        photo = message.photo[-1]
        file_info = await bot.get_file(photo.file_id)
        file_bytes = await bot.download_file(file_info.file_path)
        image_bytes = file_bytes.read()
        
        if len(image_bytes) > config.MAX_IMAGE_SIZE_MB * 1024 * 1024:
            await message.answer(f"⚠️ Максимальный размер {config.MAX_IMAGE_SIZE_MB}MB")
            return
        
        user_last_images[user_id] = {
            "bytes": image_bytes,
            "mime_type": "image/jpeg"
        }
        
        model_info = ai_service.get_model_info()
        
        if model_info["supports_images"]:
            await message.answer(
                f"🖼️ <b>Фото сохранено</b> ({len(image_bytes)//1024}KB)\n\n"
                f"Теперь напишите запрос к фото\n\n"
                f"<i>Модель:</i> {model_info['name']}",
                reply_markup=get_main_keyboard()
            )
        else:
            await message.answer(
                f"🖼️ <b>Фото сохранено</b>\n\n"
                f"⚠️ Текущая модель не поддерживает фото\n"
                f"Используйте /model для смены",
                reply_markup=get_main_keyboard()
            )
        
    except Exception as e:
        logger.error(f"Ошибка фото: {e}")
        await message.answer("⚠️ Ошибка обработки фото")

# Обработчик текстовых сообщений
@router.message(F.text)
async def handle_message(message: Message):
    """Обработка текстовых сообщений"""
    
    user_id = message.from_user.id
    user_message = message.text.strip()
    
    # Обработка кнопок
    if user_message == "💬 Задать вопрос":
        await message.answer("✍️ Напишите ваш вопрос")
        return
    
    elif user_message == "📸 Отправить фото":
        await message.answer("📸 Отправьте изображение")
        return
    
    elif user_message == "🗑️ Очистить чат":
        await ai_service.clear_history(user_id)
        await message.answer("✅ Чат очищен", reply_markup=get_main_keyboard())
        return
    
    elif user_message == "🔄 Сменить модель":
        await cmd_model(message)
        return
    
    # Кнопки админки
    if user_id == config.ADMIN_ID:
        if user_message == "📊 Статистика":
            total_users = stats.get_users_count()
            users_today = stats.get_users_count(1)
            users_week = stats.get_users_count(7)
            
            total_requests = stats.get_requests_count()
            requests_today = stats.get_requests_count(1)
            requests_week = stats.get_requests_count(7)
            
            total_images = stats.get_images_count()
            images_today = stats.get_images_count(1)
            
            active_today = stats.get_active_users_today()
            
            stat_text = (
                "📊 <b>Статистика бота</b>\n\n"
                f"<b>👥 Пользователи:</b>\n"
                f"• Всего: {total_users}\n"
                f"• За сегодня: {users_today}\n"
                f"• За неделю: {users_week}\n"
                f"• Активных сегодня: {active_today}\n\n"
                
                f"<b>📨 Запросы:</b>\n"
                f"• Всего: {total_requests}\n"
                f"• За сегодня: {requests_today}\n"
                f"• За неделю: {requests_week}\n\n"
                
                f"<b>🖼️ Изображения:</b>\n"
                f"• Всего отправлено: {total_images}\n"
                f"• За сегодня: {images_today}\n\n"
                
                f"<b>💾 История чатов:</b>\n"
                f"• Активных чатов: {len(user_conversations)}\n"
                f"• Макс. история: {config.MAX_HISTORY_LENGTH} сообщений\n\n"
                
                f"<b>🤖 Модели:</b>\n"
                f"• Доступно: {len(config.MODELS)}\n"
                f"• Текущая: {ai_service.get_model_info()['name']}"
            )
            
            await message.answer(stat_text)
            return
        
        elif user_message == "💰 Баланс API":
            await message.answer("⏳ Проверяю баланс OpenRouter...")
            
            try:
                async with aiohttp.ClientSession() as session:
                    async with session.get(
                        "https://openrouter.ai/api/v1/auth/key",
                        headers={"Authorization": f"Bearer {config.OPENROUTER_API_KEY}"},
                        timeout=10
                    ) as response:
                        if response.status == 200:
                            data = await response.json()
                            balance = data.get("data", {}).get("credits", 0)
                            usage = data.get("data", {}).get("usage", {})
                            total_used = usage.get("total", 0)
                            
                            balance_text = (
                                "💰 <b>Баланс OpenRouter</b>\n\n"
                                f"• <b>Доступно:</b> {balance:.4f} кредитов\n"
                                f"• <b>Использовано:</b> {total_used:.4f} кредитов\n\n"
                                
                                "<b>💸 Примерные цены:</b>\n"
                                "• Gemini 3 Flash: ~0.001-0.01 кредита/запрос\n"
                                "• GPT-4o Mini: ~0.002 кредита/запрос\n"
                                "• Claude 4.5: ~0.015 кредита/запрос\n\n"
                                
                                "<b>📊 Прогноз:</b>\n"
                            )
                            
                            if balance > 0:
                                avg_cost = 0.003
                                estimated_requests = int(balance / avg_cost)
                                balance_text += f"• Примерно {estimated_requests} запросов осталось\n"
                            
                            balance_text += "\n🔗 Пополнить: https://openrouter.ai/account"
                            
                            await message.answer(balance_text)
                        else:
                            await message.answer("⚠️ Не удалось получить баланс")
            except Exception as e:
                logger.error(f"Ошибка проверки баланса: {e}")
                await message.answer("⚠️ Ошибка при проверке баланса")
            return
        
        elif user_message == "👥 Топ пользователей":
            top_users = stats.get_top_users(15)
            
            if not top_users:
                await message.answer("📭 Нет данных о пользователях")
                return
            
            users_text = "👥 <b>Топ пользователей по запросам</b>\n\n"
            
            for i, (user_id, count) in enumerate(top_users, 1):
                users_text += f"{i}. ID: {user_id} - {count} запросов\n"
            
            users_text += f"\n📈 Всего уникальных пользователей: {stats.get_users_count()}"
            
            await message.answer(users_text)
            return
        
        elif user_message == "📈 Активность":
            daily_stats = stats.get_daily_stats()
            
            if not daily_stats:
                await message.answer("📭 Нет данных для графика")
                return
            
            dates = sorted(daily_stats.keys())
            graph_text = "📈 <b>Активность по дням</b>\n\n"
            
            for date in dates[-7:]:
                count = daily_stats[date]
                bar = "█" * min(count, 20)
                graph_text += f"{date}: {bar} {count}\n"
            
            await message.answer(graph_text)
            return
        
        elif user_message == "🔙 Главное меню":
            await message.answer("Главное меню", reply_markup=get_main_keyboard())
            return
    
    # Выбор модели
    models = ai_service.get_all_models()
    for model_id, model_info in models.items():
        emoji = "🖼️" if model_info["supports_images"] else "📝"
        if user_message == f"{emoji} {model_info['name']}":
            if ai_service.set_model(model_id):
                await message.answer(
                    f"✅ Модель: <b>{model_info['name']}</b>\n"
                    f"{model_info['description']}",
                    reply_markup=get_main_keyboard()
                )
            return
    
    if user_message == "🔙 Назад":
        await message.answer("Главное меню", reply_markup=get_main_keyboard())
        return
    
    # Проверки
    if not user_message:
        await message.answer("✍️ Напишите сообщение")
        return
    
    if len(user_message) > config.MAX_MESSAGE_LENGTH:
        await message.answer(f"⚠️ Максимум {config.MAX_MESSAGE_LENGTH} символов")
        return
    
    # Добавляем статистику запроса
    stats.add_request(user_id)
    
    # Подготовка изображений
    images = []
    if user_id in user_last_images:
        model_info = ai_service.get_model_info()
        if model_info["supports_images"]:
            try:
                image_data = user_last_images[user_id]
                processed_image = await ai_service.process_image(
                    image_data["bytes"], 
                    image_data["mime_type"]
                )
                images.append(processed_image)
            except Exception as e:
                logger.error(f"Ошибка обработки фото: {e}")
    
    # Отправка статуса
    status_msg = await message.answer("⏳ Нейросеть генерирует ответ...")
    processing_messages[user_id] = status_msg.message_id
    
    # Получение ответа
    response = await ai_service.generate_response(user_id, user_message, images)
    
    # Удаление статуса
    if user_id in processing_messages:
        try:
            await bot.delete_message(user_id, processing_messages[user_id])
            del processing_messages[user_id]
        except:
            pass
    
    # Отправка ответа
    if len(response) > config.MAX_MESSAGE_LENGTH:
        parts = [response[i:i+config.MAX_MESSAGE_LENGTH] for i in range(0, len(response), config.MAX_MESSAGE_LENGTH)]
        for i, part in enumerate(parts, 1):
            await message.answer(f"{part}\n\n[Часть {i}/{len(parts)}]")
    else:
        await message.answer(response)

# Обработчик любых других типов сообщений
@router.message()
async def handle_other_messages(message: Message):
    """Обработчик не текстовых сообщений"""
    await message.answer(
        "⚠️ Я понимаю только текстовые сообщения и фотографии.\n\n"
        "📸 Отправьте фото или напишите текстовый вопрос."
    )

# Основная функция
async def main():
    """Запуск бота"""
    logger.info("Запуск AI Assistant...")
    
    print("\n" + "="*50)
    print("🤖 AI Assistant запущен!")
    print(f"👑 Админ ID: {config.ADMIN_ID}")
    print(f"🤖 Модель: {ai_service.get_model_info()['name']}")
    print("="*50 + "\n")
    
    # Запуск бота
    await dp.start_polling(bot)

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("Бот остановлен")
        print("\n👋 Бот остановлен")
    except Exception as e:
        logger.error(f"Критическая ошибка: {e}")
        print(f"❌ Ошибка: {e}")