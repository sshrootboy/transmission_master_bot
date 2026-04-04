import asyncio
import os
import tempfile
from urllib.parse import urlsplit
from aiogram import Bot, Dispatcher, F
from aiogram.client.session.aiohttp import AiohttpSession
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
TELEGRAM_PROXY_URL = os.getenv("TELEGRAM_PROXY_URL") or os.getenv("TELEGRAM_SOCKS5_PROXY")

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

def format_proxy_for_log(proxy_url: str) -> str:
    """Возвращает безопасное представление proxy для логов"""
    try:
        parsed = urlsplit(proxy_url)
        if not parsed.scheme or not parsed.hostname:
            return "configured"
        auth = "***@" if parsed.username else ""
        port = f":{parsed.port}" if parsed.port else ""
        return f"{parsed.scheme}://{auth}{parsed.hostname}{port}"
    except Exception:
        return "configured"

def create_bot() -> Bot:
    """Создание Telegram-бота с optional proxy"""
    if TELEGRAM_PROXY_URL:
        session = AiohttpSession(proxy=TELEGRAM_PROXY_URL)
        return Bot(token=BOT_TOKEN, session=session)
    return Bot(token=BOT_TOKEN)

# Инициализация бота и диспетчера
bot = create_bot()
storage = MemoryStorage()
dp = Dispatcher(storage=storage)

# FSM States для управления диалогом
class TorrentStates(StatesGroup):
    waiting_for_category = State()
    selecting_torrent_to_delete = State()
    confirming_deletion = State()

# Временное хранилище для magnet-ссылок, .torrent файлов и выбранных торрентов
user_magnets = {}
user_torrent_files = {}
user_selected_torrents = {}
delete_page = {}

def cleanup_user_torrent_file(user_id: int) -> None:
    """Удаляет временный .torrent файл пользователя"""
    torrent_path = user_torrent_files.pop(user_id, None)
    if not torrent_path:
        return
    try:
        os.remove(torrent_path)
    except FileNotFoundError:
        return
    except Exception as e:
        print(f"Ошибка при удалении временного .torrent файла: {e}")

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

# Экранирование текста для Markdown
def escape_markdown(text: str) -> str:
    """Экранирование спецсимволов Markdown"""
    if text is None:
        return ""
    return (
        text.replace('_', '\\_')
        .replace('*', '\\*')
        .replace('[', '\\[')
        .replace('`', '\\`')
    )

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

# Приоритет статусов для сортировки
def get_status_priority(torrent) -> tuple:
    """Определение приоритета статуса для сортировки"""
    status = torrent.status.lower()

    # Проверяем есть ли ошибки
    has_error = torrent.error != 0 or (hasattr(torrent, 'error_string') and torrent.error_string)

    if status == "downloading" or status == "download pending":
        priority = 1
    elif has_error:
        priority = 2
    elif status == "seeding" or status == "seed pending":
        priority = 3
    else:
        priority = 4

    # Сортируем по приоритету, потом по ID (обратный порядок - новые сверху)
    return (priority, -torrent.id)

# Подсчет статусов торрентов
def get_status_counts(torrents):
    """Подсчет статусов торрентов"""
    active = sum(1 for t in torrents if t.status == "downloading")
    seeding = sum(1 for t in torrents if t.status == "seeding")
    paused = sum(1 for t in torrents if t.status == "stopped")
    errors = sum(1 for t in torrents if t.error != 0)
    total = len(torrents)
    return active, seeding, paused, errors, total

# Сортировка торрентов
def sort_torrents(torrents):
    """Сортировка: загружающиеся -> с ошибками -> готовые -> остальные"""
    try:
        return sorted(torrents, key=get_status_priority)
    except Exception as e:
        print(f"Ошибка при сортировке торрентов: {e}")
        return torrents

# Пагинация торрентов
def paginate_torrents(torrents, page=0, per_page=9):
    """Выборка торрентов по странице"""
    total = len(torrents)
    start_idx = page * per_page
    end_idx = start_idx + per_page
    page_torrents = torrents[start_idx:end_idx]
    return page_torrents, total, start_idx, end_idx

def get_pagination_buttons(page, total, per_page, prefix):
    """Кнопки навигации по страницам"""
    start_idx = page * per_page
    end_idx = start_idx + per_page
    buttons = []

    if page > 0:
        buttons.append(InlineKeyboardButton(
            text="⬅️ Назад",
            callback_data=f"{prefix}{page-1}"
        ))

    if end_idx < total:
        buttons.append(InlineKeyboardButton(
            text="➡️ Далее",
            callback_data=f"{prefix}{page+1}"
        ))

    return buttons

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
                KeyboardButton(text="🗑 Удалить торрент"),
                KeyboardButton(text="❓ Помощь")
            ]
        ],
        resize_keyboard=True,
        input_field_placeholder="Отправьте magnet-ссылку или .torrent файл"
    )
    return keyboard

# Создание inline-клавиатуры для выбора категории
def get_category_keyboard():
    """Клавиатура для выбора категории загрузки"""
    buttons = []

    for i in range(0, len(DOWNLOAD_CATEGORIES), 2):
        row = []
        for j in range(i, min(i + 2, len(DOWNLOAD_CATEGORIES))):
            category = DOWNLOAD_CATEGORIES[j].strip()
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

    buttons.append([InlineKeyboardButton(text="❌ Отмена", callback_data="cancel")])

    keyboard = InlineKeyboardMarkup(inline_keyboard=buttons)
    return keyboard

# Создание клавиатуры со списком торрентов для удаления
def get_torrents_keyboard(page=0, per_page=9):
    """Клавиатура со списком торрентов для удаления (по 9 штук)"""
    try:
        torrents = client.get_torrents()
        torrents = sort_torrents(torrents)

        page_torrents, total, _, _ = paginate_torrents(torrents, page=page, per_page=per_page)

        buttons = []

        for torrent in page_torrents:
            # Ограничиваем длину названия
            name = torrent.name[:40] + "..." if len(torrent.name) > 40 else torrent.name
            emoji = get_status_emoji(torrent.status)

            buttons.append([InlineKeyboardButton(
                text=f"{emoji} {name}",
                callback_data=f"delete_select_{torrent.id}"
            )])

        # Навигация
        nav_buttons = get_pagination_buttons(page, total, per_page, "delete_page_")

        if nav_buttons:
            buttons.append(nav_buttons)

        # Кнопка отмены
        buttons.append([InlineKeyboardButton(text="❌ Отмена", callback_data="cancel")])

        keyboard = InlineKeyboardMarkup(inline_keyboard=buttons)
        return keyboard, total

    except Exception as e:
        print(f"Ошибка при получении списка торрентов: {e}")
        return None, 0

# Формирование страницы списка торрентов с пагинацией
def get_torrents_list_page(page=0, per_page=MAX_TORRENTS_DISPLAY):
    """Текст списка торрентов и inline-клавиатура для навигации"""
    torrents = client.get_torrents()
    if not torrents:
        return None, None

    torrents = sort_torrents(torrents)
    total = len(torrents)
    max_page = max(0, (total - 1) // per_page)
    page = min(max(page, 0), max_page)

    page_torrents, _, _, _ = paginate_torrents(torrents, page=page, per_page=per_page)
    total_pages = max_page + 1

    response = f"📋 *Активные торренты* (страница {page + 1} из {total_pages}):\n\n"

    for torrent in page_torrents:
        progress = torrent.progress
        status = get_status_emoji(torrent.status)
        size = format_size(torrent.total_size)

        name = escape_markdown(torrent.name)
        name = name[:50] + '...' if len(name) > 50 else name

        error_text = ""
        if hasattr(torrent, 'error_string') and torrent.error_string:
            error_text = f"\n   ⚠️ Ошибка: {escape_markdown(torrent.error_string)}"

        response += f"{status} `{name}`\n"
        response += f"   📊 Прогресс: *{progress:.1f}%* | 📦 Размер: *{size}*{error_text}\n\n"

    nav_buttons = get_pagination_buttons(page, total, per_page, "list_page_")
    keyboard = InlineKeyboardMarkup(inline_keyboard=[nav_buttons]) if nav_buttons else None

    return response, keyboard

# Клавиатура подтверждения удаления
def get_delete_confirmation_keyboard(torrent_id):
    """Клавиатура для подтверждения удаления"""
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(
                text="🗑 Удалить с файлами",
                callback_data=f"confirm_delete_with_files_{torrent_id}"
            )
        ],
        [
            InlineKeyboardButton(
                text="📋 Удалить без файлов",
                callback_data=f"confirm_delete_no_files_{torrent_id}"
            )
        ],
        [
            InlineKeyboardButton(
                text="❌ Отмена",
                callback_data="cancel_delete"
            )
        ]
    ])
    return keyboard

@dp.message(Command("start"))
async def cmd_start(message: Message):
    """Команда /start"""
    if not check_access(message.from_user.id):
        await message.answer("⛔ У вас нет доступа к этому боту.")
        return

    welcome_text = (
        "🤖 *Transmission Master Bot*\n\n"
        "📥 Отправьте magnet-ссылку или .torrent файл для добавления торрента\n"
        "📋 Используйте кнопки ниже для управления\n\n"
        "*Доступные команды:*\n"
        "📋 Список торрентов\n"
        "📊 Статус системы\n"
        "🗑 Удалить торрент\n"
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
        "📖 *Transmission Master Bot - Руководство*\n\n"
        "*Добавление торрента:*\n"
        "1️⃣ Отправьте magnet-ссылку или .torrent файл\n"
        "2️⃣ Выберите категорию (Movies, Series, Music, Other)\n"
        "3️⃣ Торрент начнет загружаться\n\n"
        "*Управление:*\n"
        "📋 *Список торрентов* - отсортированный список\n"
        "   • Сначала загружающиеся\n"
        "   • Затем с ошибками\n"
        "   • Потом готовые\n"
        "📊 *Статус* - информация о системе\n"
        "🗑 *Удалить торрент* - выбор торрента для удаления\n\n"
        "*Уведомления:*\n"
        "🔔 Получите уведомление когда загрузка завершится"
    )

    await message.answer(help_text, reply_markup=get_main_keyboard(), parse_mode="Markdown")

@dp.message(Command("list"))
@dp.message(F.text == "📋 Список торрентов")
async def cmd_list(message: Message):
    """Команда /list - показать список торрентов с сортировкой"""
    if not check_access(message.from_user.id):
        return

    try:
        response, keyboard = get_torrents_list_page(page=0, per_page=MAX_TORRENTS_DISPLAY)

        if response is None:
            empty_message = os.getenv("EMPTY_LIST_MESSAGE", "📭 Список торрентов пуст")
            await message.answer(empty_message, reply_markup=get_main_keyboard())
            return

        await message.answer(response, reply_markup=keyboard, parse_mode="Markdown")
    except Exception as e:
        await message.answer(f"{EMOJI_ERROR} Ошибка: {str(e)}", reply_markup=get_main_keyboard())

@dp.callback_query(F.data.startswith("list_page_"))
async def handle_list_page(callback: CallbackQuery):
    """Пагинация списка торрентов"""
    if not check_access(callback.from_user.id):
        return

    try:
        page = int(callback.data.replace("list_page_", ""))
        response, keyboard = get_torrents_list_page(page=page, per_page=MAX_TORRENTS_DISPLAY)

        if response is None:
            await callback.message.edit_text("📭 Список торрентов пуст")
            await callback.answer()
            return

        await callback.message.edit_text(response, reply_markup=keyboard, parse_mode="Markdown")
        await callback.answer()
    except Exception as e:
        await callback.answer(f"❌ Ошибка: {str(e)}", show_alert=True)

@dp.message(Command("status"))
@dp.message(F.text == "📊 Статус")
async def cmd_status(message: Message):
    """Команда /status - показать статус системы"""
    if not check_access(message.from_user.id):
        return

    try:
        session = client.get_session()
        torrents = client.get_torrents()

        active, seeding, paused, errors, total = get_status_counts(torrents)

        download_speed = sum(t.rate_download for t in torrents)
        upload_speed = sum(t.rate_upload for t in torrents)

        response = (
            "📊 *Transmission Master Bot - Статус:*\n\n"
            f"🔄 Загружается: *{active}*\n"
            f"✅ Раздается: *{seeding}*\n"
            f"⏸️ Остановлено: *{paused}*\n"
        )

        if errors > 0:
            response += f"❌ С ошибками: *{errors}*\n"

        response += (
            f"📦 Всего: *{total}*\n\n"
            f"⬇️ Скорость загрузки: *{format_size(download_speed)}/s*\n"
            f"⬆️ Скорость отдачи: *{format_size(upload_speed)}/s*\n\n"
        )

        await message.answer(response, reply_markup=get_main_keyboard(), parse_mode="Markdown")
    except Exception as e:
        await message.answer(f"{EMOJI_ERROR} Ошибка: {str(e)}", reply_markup=get_main_keyboard())

@dp.message(F.text == "🗑 Удалить торрент")
async def cmd_delete(message: Message, state: FSMContext):
    """Команда удаления торрента - показать список"""
    if not check_access(message.from_user.id):
        return

    # Инициализируем страницу
    delete_page[message.from_user.id] = 0

    keyboard, total = get_torrents_keyboard(page=0)

    if keyboard is None or total == 0:
        await message.answer("📭 Список торрентов пуст", reply_markup=get_main_keyboard())
        return

    await message.answer(
        f"🗑 *Выберите торрент для удаления:*\n_Всего торрентов: {total}_",
        reply_markup=keyboard,
        parse_mode="Markdown"
    )

    await state.set_state(TorrentStates.selecting_torrent_to_delete)

@dp.callback_query(F.data.startswith("delete_page_"))
async def handle_delete_page(callback: CallbackQuery, state: FSMContext):
    """Обработка пагинации списка торрентов"""
    if not check_access(callback.from_user.id):
        return

    try:
        page = int(callback.data.replace("delete_page_", ""))
        delete_page[callback.from_user.id] = page

        keyboard, total = get_torrents_keyboard(page=page)

        if keyboard is None:
            await callback.answer("❌ Ошибка загрузки списка")
            return

        await callback.message.edit_text(
            f"🗑 *Выберите торрент для удаления:*\n_Страница {page + 1}, всего торрентов: {total}_",
            reply_markup=keyboard,
            parse_mode="Markdown"
        )

        await callback.answer()
    except Exception as e:
        await callback.answer(f"❌ Ошибка: {str(e)}", show_alert=True)

@dp.callback_query(F.data.startswith("delete_select_"))
async def handle_delete_select(callback: CallbackQuery, state: FSMContext):
    """Обработка выбора торрента для удаления"""
    if not check_access(callback.from_user.id):
        return

    try:
        torrent_id = int(callback.data.replace("delete_select_", ""))

        torrent = client.get_torrent(torrent_id)

        # Сохраняем выбранный торрент
        user_selected_torrents[callback.from_user.id] = torrent_id

        name = torrent.name
        size = format_size(torrent.total_size)
        progress = torrent.progress

        confirmation_text = (
            f"🗑 *Удаление торрента:*\n\n"
            f"📝 `{name}`\n"
            f"📦 Размер: *{size}*\n"
            f"📊 Прогресс: *{progress:.1f}%*\n\n"
            f"Выберите способ удаления:"
        )

        await callback.message.edit_text(
            confirmation_text,
            reply_markup=get_delete_confirmation_keyboard(torrent_id),
            parse_mode="Markdown"
        )

        await state.set_state(TorrentStates.confirming_deletion)
        await callback.answer()

    except Exception as e:
        await callback.answer(f"❌ Ошибка: {str(e)}", show_alert=True)

@dp.callback_query(F.data.startswith("confirm_delete_"))
async def handle_delete_confirm(callback: CallbackQuery, state: FSMContext):
    """Обработка подтверждения удаления"""
    if not check_access(callback.from_user.id):
        return

    try:
        parts = callback.data.split("_")
        delete_files = parts[2] == "with"
        torrent_id = int(parts[-1])

        torrent = client.get_torrent(torrent_id)
        name = torrent.name

        # Удаляем торрент
        client.remove_torrent(torrent_id, delete_data=delete_files)

        # Удаляем из кеша
        if callback.from_user.id in user_selected_torrents:
            del user_selected_torrents[callback.from_user.id]

        action = "с файлами" if delete_files else "без файлов"
        success_text = (
            f"✅ *Торрент удален {action}*\n\n"
            f"📝 `{name}`"
        )

        await callback.message.edit_text(success_text, parse_mode="Markdown")
        await callback.message.answer("Что дальше?", reply_markup=get_main_keyboard())
        await callback.answer(f"✅ Удалено {action}")

    except Exception as e:
        await callback.answer(f"❌ Ошибка: {str(e)}", show_alert=True)

    await state.clear()

@dp.callback_query(F.data == "cancel_delete")
async def handle_cancel_delete(callback: CallbackQuery, state: FSMContext):
    """Отмена удаления"""
    if not check_access(callback.from_user.id):
        return

    if callback.from_user.id in user_selected_torrents:
        del user_selected_torrents[callback.from_user.id]

    await callback.message.edit_text("❌ Удаление отменено")
    await callback.message.answer("Что дальше?", reply_markup=get_main_keyboard())
    await callback.answer("Отменено")
    await state.clear()

@dp.message(F.text.startswith("magnet:"))
async def handle_magnet(message: Message, state: FSMContext):
    """Обработка magnet-ссылок"""
    if not check_access(message.from_user.id):
        return

    cleanup_user_torrent_file(message.from_user.id)
    magnet_link = message.text.strip()
    user_magnets[message.from_user.id] = magnet_link

    await message.answer(
        "📂 *Выберите категорию для загрузки:*",
        reply_markup=get_category_keyboard(),
        parse_mode="Markdown"
    )

    await state.set_state(TorrentStates.waiting_for_category)

@dp.message(F.document)
async def handle_torrent_file(message: Message, state: FSMContext):
    """Обработка .torrent файлов"""
    if not check_access(message.from_user.id):
        return

    document = message.document
    file_name = (document.file_name or "").lower()

    if not file_name.endswith(".torrent"):
        await message.answer("❌ Пожалуйста, отправьте файл с расширением .torrent.", reply_markup=get_main_keyboard())
        return

    if message.from_user.id in user_magnets:
        del user_magnets[message.from_user.id]
    cleanup_user_torrent_file(message.from_user.id)

    tmp_path = None
    try:
        with tempfile.NamedTemporaryFile(prefix="tmbot_", suffix=".torrent", delete=False) as tmp_file:
            tmp_path = tmp_file.name

        download = getattr(bot, "download", None)
        if download:
            await download(document, destination=tmp_path)
        else:
            file = await bot.get_file(document.file_id)
            await bot.download_file(file.file_path, destination=tmp_path)

        if not os.path.exists(tmp_path) or os.path.getsize(tmp_path) == 0:
            raise ValueError("Файл .torrent пуст или не был загружен")

        user_torrent_files[message.from_user.id] = tmp_path

        await message.answer(
            "📂 *Выберите категорию для загрузки:*",
            reply_markup=get_category_keyboard(),
            parse_mode="Markdown"
        )

        await state.set_state(TorrentStates.waiting_for_category)
    except Exception as e:
        if tmp_path and os.path.exists(tmp_path):
            os.remove(tmp_path)
        await message.answer(f"{EMOJI_ERROR} Ошибка при загрузке .torrent файла: {str(e)}", reply_markup=get_main_keyboard())
        await state.clear()

@dp.callback_query(F.data.startswith("category_"))
async def handle_category_selection(callback: CallbackQuery, state: FSMContext):
    """Обработка выбора категории"""
    if not check_access(callback.from_user.id):
        await callback.answer("⛔ У вас нет доступа")
        return

    magnet_link = None
    torrent_file = None
    try:
        category = callback.data.replace("category_", "")
        magnet_link = user_magnets.get(callback.from_user.id)
        torrent_file = user_torrent_files.get(callback.from_user.id)

        if not magnet_link and not torrent_file:
            await callback.answer("❌ Ошибка: файл или ссылка не найдены")
            await callback.message.edit_text("❌ Ошибка: попробуйте отправить magnet-ссылку или .torrent файл заново")
            await state.clear()
            return

        session = client.get_session()
        base_download_dir = session.download_dir
        download_path = f"{base_download_dir}/{category}"

        if magnet_link:
            torrent = client.add_torrent(magnet_link, download_dir=download_path)
            del user_magnets[callback.from_user.id]
        else:
            with open(torrent_file, "rb") as f:
                torrent_data = f.read()
            if not torrent_data:
                raise ValueError("Файл .torrent пуст")
            torrent = client.add_torrent(torrent_data, download_dir=download_path)
            cleanup_user_torrent_file(callback.from_user.id)

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

        await callback.message.edit_text(success_message, parse_mode="Markdown")
        await callback.message.answer("Что дальше?", reply_markup=get_main_keyboard())
        await callback.answer("✅ Торрент добавлен!")

    except Exception as e:
        if torrent_file:
            cleanup_user_torrent_file(callback.from_user.id)
        await callback.message.edit_text(f"{EMOJI_ERROR} Ошибка при добавлении торрента: {str(e)}")
        await callback.answer("❌ Ошибка")

    await state.clear()

@dp.callback_query(F.data == "cancel")
async def handle_cancel(callback: CallbackQuery, state: FSMContext):
    """Обработка отмены"""
    if not check_access(callback.from_user.id):
        return

    if callback.from_user.id in user_magnets:
        del user_magnets[callback.from_user.id]
    cleanup_user_torrent_file(callback.from_user.id)

    await callback.message.edit_text("❌ Отменено")
    await callback.message.answer("Отправьте новую magnet-ссылку или .torrent файл", reply_markup=get_main_keyboard())
    await callback.answer("Отменено")
    await state.clear()

async def check_completed_torrents():
    """Проверка завершенных торрентов и отправка уведомлений"""
    completed_cache = set()
    initialized = False

    while True:
        try:
            torrents = client.get_torrents()

            if not initialized:
                completed_cache = {t.id for t in torrents if t.progress == 100}
                initialized = True
            else:
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

async def wait_for_rpc():
    """Ожидание доступности Transmission RPC"""
    attempt = 0
    while True:
        try:
            client.get_session()
            print("✅ Transmission RPC доступен")
            return
        except Exception as e:
            attempt += 1
            if attempt == 1 or attempt % 5 == 0:
                print(f"⏳ Ожидание Transmission RPC (попытка {attempt}): {e}")
            await asyncio.sleep(2)

async def main():
    """Главная функция запуска бота"""
    print(f"🚀 Запуск Transmission Master Bot...")
    print(f"📡 Transmission: {TRANSMISSION_HOST}:{TRANSMISSION_PORT}")
    if TELEGRAM_PROXY_URL:
        print(f"🌐 Telegram proxy: {format_proxy_for_log(TELEGRAM_PROXY_URL)}")
    print(f"⏰ Интервал проверки: {CHECK_INTERVAL} сек")
    print(f"👥 Разрешенные пользователи: {ALLOWED_USER_IDS}")
    print(f"📂 Категории загрузок: {DOWNLOAD_CATEGORIES}")

    await wait_for_rpc()

    if ALLOWED_USER_IDS:
        try:
            torrents = client.get_torrents()
            active, seeding, paused, errors, total = get_status_counts(torrents)

            startup_message = (
                "✅ *Transmission Master Bot запущен*\n\n"
                f"🔄 Загружается: *{active}*\n"
                f"✅ Раздается: *{seeding}*\n"
                f"⏸️ Остановлено: *{paused}*\n"
            )

            if errors > 0:
                startup_message += f"❌ С ошибками: *{errors}*\n"

            startup_message += f"📦 Всего: *{total}*"

            for user_id in ALLOWED_USER_IDS:
                try:
                    await bot.send_message(
                        user_id,
                        startup_message,
                        parse_mode="Markdown",
                        reply_markup=get_main_keyboard()
                    )
                except Exception as e:
                    print(f"Ошибка отправки старта пользователю {user_id}: {e}")
        except Exception as e:
            print(f"Ошибка отправки статуса при старте: {e}")

    asyncio.create_task(check_completed_torrents())
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
