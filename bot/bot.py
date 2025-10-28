import asyncio
import os
from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import Command
from aiogram.types import Message, ReplyKeyboardMarkup, KeyboardButton, InlineKeyboardMarkup, InlineKeyboardButton, CallbackQuery
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
import transmission_rpc
from dotenv import load_dotenv

# Загрузка переменных окружения
load_dotenv()

# Конфигурация бота
BOT_TOKEN = os.getenv("BOT_TOKEN")
ALLOWED_USER_IDS = [int(id.strip()) for id in os.getenv("ALLOWED_USER_IDS", "").split(",") if id.strip()]

# Конфигурация Transmission
TRANSMISSION_HOST = os.getenv("TRANSMISSION_HOST", "transmission")
TRANSMISSION_PORT = int(os.getenv("TRANSMISSION_PORT", "9091"))
TRANSMISSION_USER = os.getenv("TRANSMISSION_USER")
TRANSMISSION_PASS = os.getenv("TRANSMISSION_PASS")

# Конфигурация мониторинга
CHECK_INTERVAL = int(os.getenv("CHECK_INTERVAL", "60"))
MAX_TORRENTS_DISPLAY = int(os.getenv("MAX_TORRENTS_DISPLAY", "10"))

# Временная зона
TIMEZONE = os.getenv("TZ", "Europe/Moscow")

# Emoji для статусов
EMOJI_DOWNLOADING = os.getenv("EMOJI_DOWNLOADING", "⬇️")
EMOJI_SEEDING = os.getenv("EMOJI_SEEDING", "✅")
EMOJI_PAUSED = os.getenv("EMOJI_PAUSED", "⏸️")
EMOJI_ERROR = os.getenv("EMOJI_ERROR", "❌")
EMOJI_COMPLETED = os.getenv("EMOJI_COMPLETED", "🎉")

# Категории для загрузки
DOWNLOAD_CATEGORIES = os.getenv("DOWNLOAD_CATEGORIES", "Movies,Series,Music,Other").split(",")

# Инициализация бота и диспетчера
bot = Bot(token=BOT_TOKEN)
storage = MemoryStorage()
dp = Dispatcher(storage=storage)

# FSM States для управления диалогом
class TorrentStates(StatesGroup):
    waiting_for_category = State()

# Временное хранилище для magnet-ссылок
user_magnets = {}

# Подключение к Transmission
def get_transmission_client():
    """Создание клиента Transmission с параметрами из env"""
    return transmission_rpc.Client(
        host=TRANSMISSION_HOST,
        port=TRANSMISSION_PORT,
        username=TRANSMISSION_USER if TRANSMISSION_USER else None,
        password=TRANSMISSION_PASS if TRANSMISSION_PASS else None
    )

client = get_transmission_client()

# Проверка прав доступа
def check_access(user_id: int) -> bool:
    """Проверка доступа пользователя"""
    if not ALLOWED_USER_IDS:
        return False
    return user_id in ALLOWED_USER_IDS

# Форматирование размера файла
def format_size(size_bytes: int) -> str:
    """Форматирование размера в человекочитаемый формат"""
    for unit in ['B', 'KB', 'MB', 'GB', 'TB']:
        if size_bytes < 1024.0:
            return f"{size_bytes:.2f} {unit}"
        size_bytes /= 1024.0
    return f"{size_bytes:.2f} PB"

# Получение emoji для статуса
def get_status_emoji(status: str) -> str:
    """Получение emoji в зависимости от статуса торрента"""
    status_map = {
        "downloading": EMOJI_DOWNLOADING,
        "seeding": EMOJI_SEEDING,
        "stopped": EMOJI_PAUSED,
        "checking": "🔍",
        "check pending": "⏳",
        "download pending": "⏳",
        "seed pending": "⏳"
    }
    return status_map.get(status.lower(), EMOJI_PAUSED)

# Создание главной клавиатуры с кнопками
def get_main_keyboard():
    """Главное меню с кнопками"""
    keyboard = ReplyKeyboardMarkup(
        keyboard=[
            [
                KeyboardButton(text="📋 Список торрентов"),
                KeyboardButton(text="📊 Статус")
            ],
            [
                KeyboardButton(text="❓ Помощь")
            ]
        ],
        resize_keyboard=True,
        input_field_placeholder="Отправьте magnet-ссылку или выберите действие"
    )
    return keyboard

# Создание inline-клавиатуры для выбора категории
def get_category_keyboard():
    """Клавиатура для выбора категории загрузки"""
    buttons = []

    # Группируем кнопки по 2 в ряд
    for i in range(0, len(DOWNLOAD_CATEGORIES), 2):
        row = []
        for j in range(i, min(i + 2, len(DOWNLOAD_CATEGORIES))):
            category = DOWNLOAD_CATEGORIES[j].strip()
            # Добавляем emoji для категорий
            emoji = {
                "Movies": "🎬",
                "Series": "📺",
                "Music": "🎵",
                "Other": "📁"
            }.get(category, "📂")

            row.append(InlineKeyboardButton(
                text=f"{emoji} {category}",
                callback_data=f"category_{category}"
            ))
        buttons.append(row)

    # Добавляем кнопку отмены
    buttons.append([InlineKeyboardButton(text="❌ Отмена", callback_data="cancel")])

    keyboard = InlineKeyboardMarkup(inline_keyboard=buttons)
    return keyboard

@dp.message(Command("start"))
async def cmd_start(message: Message):
    """Команда /start"""
    if not check_access(message.from_user.id):
        await message.answer("⛔ У вас нет доступа к этому боту.")
        return

    welcome_text = (
        "🤖 *Бот для управления Transmission*\n\n"
        "📥 Отправьте magnet-ссылку для добавления торрента\n"
        "📋 Используйте кнопки ниже для управления\n\n"
        "*Доступные команды:*\n"
        "📋 Список торрентов\n"
        "📊 Статус системы\n"
        "❓ Помощь"
    )

    await message.answer(welcome_text, reply_markup=get_main_keyboard(), parse_mode="Markdown")

@dp.message(Command("help"))
@dp.message(F.text == "❓ Помощь")
async def cmd_help(message: Message):
    """Команда /help"""
    if not check_access(message.from_user.id):
        return

    help_text = (
        "📖 *Руководство по использованию*\n\n"
        "*Добавление торрента:*\n"
        "1️⃣ Отправьте magnet-ссылку\n"
        "2️⃣ Выберите категорию (Movies, Series, Music, Other)\n"
        "3️⃣ Торрент начнет загружаться\n\n"
        "*Управление:*\n"
        "📋 *Список торрентов* - показать активные загрузки\n"
        "📊 *Статус* - информация о системе\n\n"
        "*Уведомления:*\n"
        "🔔 Получите уведомление когда загрузка завершится"
    )

    await message.answer(help_text, reply_markup=get_main_keyboard(), parse_mode="Markdown")

@dp.message(Command("list"))
@dp.message(F.text == "📋 Список торрентов")
async def cmd_list(message: Message):
    """Команда /list - показать список торрентов"""
    if not check_access(message.from_user.id):
        return

    try:
        torrents = client.get_torrents()

        if not torrents:
            empty_message = os.getenv("EMPTY_LIST_MESSAGE", "📭 Список торрентов пуст")
            await message.answer(empty_message, reply_markup=get_main_keyboard())
            return

        list_header = "📋 *Активные торренты:*\n\n"
        response = list_header

        for torrent in torrents[:MAX_TORRENTS_DISPLAY]:
            progress = torrent.progress
            status = get_status_emoji(torrent.status)
            size = format_size(torrent.total_size)

            # Экранируем специальные символы для Markdown
            name = torrent.name.replace('_', '\\_').replace('*', '\\*').replace('[', '\\[')

            response += f"{status} `{name[:50]}{'...' if len(name) > 50 else ''}`\n"
            response += f"   📊 Прогресс: *{progress:.1f}%* | 📦 Размер: *{size}*\n\n"

        if len(torrents) > MAX_TORRENTS_DISPLAY:
            response += f"\n_... и еще {len(torrents) - MAX_TORRENTS_DISPLAY} торрентов_"

        await message.answer(response, reply_markup=get_main_keyboard(), parse_mode="Markdown")
    except Exception as e:
        await message.answer(f"{EMOJI_ERROR} Ошибка: {str(e)}", reply_markup=get_main_keyboard())

@dp.message(Command("status"))
@dp.message(F.text == "📊 Статус")
async def cmd_status(message: Message):
    """Команда /status - показать статус системы"""
    if not check_access(message.from_user.id):
        return

    try:
        session = client.get_session()
        torrents = client.get_torrents()

        active = sum(1 for t in torrents if t.status == "downloading")
        seeding = sum(1 for t in torrents if t.status == "seeding")
        paused = sum(1 for t in torrents if t.status == "stopped")
        total = len(torrents)

        download_speed = sum(t.rate_download for t in torrents)
        upload_speed = sum(t.rate_upload for t in torrents)

        response = (
            "📊 *Статус системы:*\n\n"
            f"🔄 Загружается: *{active}*\n"
            f"✅ Раздается: *{seeding}*\n"
            f"⏸️ Остановлено: *{paused}*\n"
            f"📦 Всего: *{total}*\n\n"
            f"⬇️ Скорость загрузки: *{format_size(download_speed)}/s*\n"
            f"⬆️ Скорость отдачи: *{format_size(upload_speed)}/s*\n\n"
            f"📁 Папка загрузок: `{session.download_dir}`"
        )

        await message.answer(response, reply_markup=get_main_keyboard(), parse_mode="Markdown")
    except Exception as e:
        await message.answer(f"{EMOJI_ERROR} Ошибка: {str(e)}", reply_markup=get_main_keyboard())

@dp.message(F.text.startswith("magnet:"))
async def handle_magnet(message: Message, state: FSMContext):
    """Обработка magnet-ссылок"""
    if not check_access(message.from_user.id):
        return

    magnet_link = message.text.strip()

    # Сохраняем magnet-ссылку для пользователя
    user_magnets[message.from_user.id] = magnet_link

    # Отправляем клавиатуру для выбора категории
    await message.answer(
        "📂 *Выберите категорию для загрузки:*",
        reply_markup=get_category_keyboard(),
        parse_mode="Markdown"
    )

    # Устанавливаем состояние ожидания выбора категории
    await state.set_state(TorrentStates.waiting_for_category)

@dp.callback_query(F.data.startswith("category_"))
async def handle_category_selection(callback: CallbackQuery, state: FSMContext):
    """Обработка выбора категории"""
    if not check_access(callback.from_user.id):
        await callback.answer("⛔ У вас нет доступа")
        return

    # Получаем выбранную категорию
    category = callback.data.replace("category_", "")

    # Получаем сохраненную magnet-ссылку
    magnet_link = user_magnets.get(callback.from_user.id)

    if not magnet_link:
        await callback.answer("❌ Ошибка: magnet-ссылка не найдена")
        await callback.message.edit_text("❌ Ошибка: попробуйте отправить magnet-ссылку заново")
        await state.clear()
        return

    try:
        # Получаем базовую папку загрузок
        session = client.get_session()
        base_download_dir = session.download_dir

        # Формируем путь с подпапкой
        download_path = f"{base_download_dir}/{category}"

        # Добавляем торрент с указанием папки
        torrent = client.add_torrent(magnet_link, download_dir=download_path)

        # Удаляем сохраненную ссылку
        del user_magnets[callback.from_user.id]

        emoji = {
            "Movies": "🎬",
            "Series": "📺",
            "Music": "🎵",
            "Other": "📁"
        }.get(category, "📂")

        success_message = (
            f"{EMOJI_COMPLETED} *Торрент добавлен!*\n\n"
            f"📝 Название: `{torrent.name}`\n"
            f"{emoji} Категория: *{category}*\n"
            f"📊 ID: `{torrent.id}`\n"
            f"📁 Папка: `{download_path}`"
        )

        # Удаляем сообщение с кнопками
        await callback.message.edit_text(success_message, parse_mode="Markdown")

        # Отправляем новое сообщение с главным меню
        await callback.message.answer("Что дальше?", reply_markup=get_main_keyboard())

        await callback.answer("✅ Торрент добавлен!")

    except Exception as e:
        await callback.message.edit_text(f"{EMOJI_ERROR} Ошибка при добавлении торрента: {str(e)}")
        await callback.answer("❌ Ошибка")

    # Очищаем состояние
    await state.clear()

@dp.callback_query(F.data == "cancel")
async def handle_cancel(callback: CallbackQuery, state: FSMContext):
    """Обработка отмены"""
    if not check_access(callback.from_user.id):
        return

    # Удаляем сохраненную ссылку
    if callback.from_user.id in user_magnets:
        del user_magnets[callback.from_user.id]

    await callback.message.edit_text("❌ Отменено")
    await callback.message.answer("Отправьте новую magnet-ссылку или используйте кнопки", reply_markup=get_main_keyboard())
    await callback.answer("Отменено")
    await state.clear()

async def check_completed_torrents():
    """Проверка завершенных торрентов и отправка уведомлений"""
    completed_cache = set()

    while True:
        try:
            torrents = client.get_torrents()

            for torrent in torrents:
                if torrent.progress == 100 and torrent.id not in completed_cache:
                    completed_cache.add(torrent.id)

                    completion_message = (
                        f"{EMOJI_COMPLETED} *Загрузка завершена!*\n\n"
                        f"📝 {torrent.name}\n"
                        f"📦 Размер: *{format_size(torrent.total_size)}*"
                    )

                    for user_id in ALLOWED_USER_IDS:
                        try:
                            await bot.send_message(
                                user_id, 
                                completion_message,
                                parse_mode="Markdown",
                                reply_markup=get_main_keyboard()
                            )
                        except Exception as e:
                            print(f"Ошибка отправки уведомления пользователю {user_id}: {e}")

        except Exception as e:
            print(f"Ошибка проверки торрентов: {e}")

        await asyncio.sleep(CHECK_INTERVAL)

async def main():
    """Главная функция запуска бота"""
    print(f"🚀 Запуск бота...")
    print(f"📡 Transmission: {TRANSMISSION_HOST}:{TRANSMISSION_PORT}")
    print(f"⏰ Интервал проверки: {CHECK_INTERVAL} сек")
    print(f"👥 Разрешенные пользователи: {ALLOWED_USER_IDS}")
    print(f"📂 Категории загрузок: {DOWNLOAD_CATEGORIES}")

    asyncio.create_task(check_completed_torrents())
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
