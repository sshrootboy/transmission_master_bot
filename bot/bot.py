import asyncio
import os
from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import Command
from aiogram.types import Message
from aiogram.fsm.storage.memory import MemoryStorage
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

# Инициализация бота и диспетчера
bot = Bot(token=BOT_TOKEN)
storage = MemoryStorage()
dp = Dispatcher(storage=storage)

# Подключение к Transmission
def get_transmission_client():
    """Создание клиента Transmission с параметрами из env"""
    return transmission_rpc.Client(
        host=TRANSMISSION_HOST,
        port=TRANSMISSION_PORT,
        username=TRANSMISSION_USER,
        password=TRANSMISSION_PASS
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

@dp.message(Command("start"))
async def cmd_start(message: Message):
    """Команда /start"""
    if not check_access(message.from_user.id):
        await message.answer("⛔ У вас нет доступа к этому боту.")
        return

    welcome_text = os.getenv(
        "WELCOME_MESSAGE",
        "🤖 Бот для управления Transmission\n\n"
        "📥 Отправьте magnet-ссылку для добавления торрента\n"
        "/list - показать список торрентов\n"
        "/status - показать статус системы\n"
        "/help - показать все команды"
    ).replace("\\n", "\n")

    await message.answer(welcome_text)

@dp.message(Command("help"))
async def cmd_help(message: Message):
    """Команда /help"""
    if not check_access(message.from_user.id):
        return

    help_text = os.getenv(
        "HELP_MESSAGE",
        "📖 Доступные команды:\n\n"
        "/start - приветствие\n"
        "/list - список активных торрентов\n"
        "/status - статус системы\n"
        "/help - это сообщение\n\n"
        "Просто отправьте magnet-ссылку для добавления торрента!"
    ).replace("\\n", "\n")

    await message.answer(help_text)

@dp.message(Command("list"))
async def cmd_list(message: Message):
    """Команда /list - показать список торрентов"""
    if not check_access(message.from_user.id):
        return

    try:
        torrents = client.get_torrents()

        if not torrents:
            empty_message = os.getenv("EMPTY_LIST_MESSAGE", "📭 Список торрентов пуст")
            await message.answer(empty_message)
            return

        list_header = os.getenv("LIST_HEADER", "📋 Активные торренты:")
        response = f"{list_header}\n\n"

        for torrent in torrents[:MAX_TORRENTS_DISPLAY]:
            progress = torrent.progress
            status = get_status_emoji(torrent.status)
            size = format_size(torrent.total_size)

            response += f"{status} {torrent.name}\n"
            response += f"   Прогресс: {progress:.1f}% | Размер: {size}\n\n"

        if len(torrents) > MAX_TORRENTS_DISPLAY:
            response += f"... и еще {len(torrents) - MAX_TORRENTS_DISPLAY} торрентов"

        await message.answer(response)
    except Exception as e:
        error_message = os.getenv("ERROR_MESSAGE", f"{EMOJI_ERROR} Ошибка: {{error}}")
        await message.answer(error_message.format(error=str(e)))

@dp.message(Command("status"))
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
            f"📊 Статус системы:\n\n"
            f"🔄 Загружается: {active}\n"
            f"✅ Раздается: {seeding}\n"
            f"⏸️ Остановлено: {paused}\n"
            f"📦 Всего: {total}\n\n"
            f"⬇️ Скорость загрузки: {format_size(download_speed)}/s\n"
            f"⬆️ Скорость отдачи: {format_size(upload_speed)}/s\n\n"
            f"📁 Папка загрузок: {session.download_dir}"
        )

        await message.answer(response)
    except Exception as e:
        error_message = os.getenv("ERROR_MESSAGE", f"{EMOJI_ERROR} Ошибка: {{error}}")
        await message.answer(error_message.format(error=str(e)))

@dp.message(F.text.startswith("magnet:"))
async def handle_magnet(message: Message):
    """Обработка magnet-ссылок"""
    if not check_access(message.from_user.id):
        return

    try:
        magnet_link = message.text.strip()
        torrent = client.add_torrent(magnet_link)

        success_message = os.getenv(
            "TORRENT_ADDED_MESSAGE",
            f"{EMOJI_COMPLETED} Торрент добавлен!\n\n"
            "📝 Название: {name}\n"
            "📊 ID: {torrent_id}"
        )

        await message.answer(
            success_message.format(
                name=torrent.name,
                torrent_id=torrent.id
            ).replace("\\n", "\n")
        )
    except Exception as e:
        error_message = os.getenv(
            "TORRENT_ADD_ERROR_MESSAGE",
            f"{EMOJI_ERROR} Ошибка при добавлении торрента: {{error}}"
        )
        await message.answer(error_message.format(error=str(e)))

async def check_completed_torrents():
    """Проверка завершенных торрентов и отправка уведомлений"""
    completed_cache = set()

    while True:
        try:
            torrents = client.get_torrents()

            for torrent in torrents:
                if torrent.progress == 100 and torrent.id not in completed_cache:
                    completed_cache.add(torrent.id)

                    completion_message = os.getenv(
                        "COMPLETION_MESSAGE",
                        f"{EMOJI_COMPLETED} Загрузка завершена!\n\n"
                        "📝 {name}\n"
                        "📦 Размер: {size}"
                    )

                    notification_text = completion_message.format(
                        name=torrent.name,
                        size=format_size(torrent.total_size)
                    ).replace("\\n", "\n")

                    for user_id in ALLOWED_USER_IDS:
                        try:
                            await bot.send_message(user_id, notification_text)
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

    asyncio.create_task(check_completed_torrents())
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
