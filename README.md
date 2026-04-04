# Transmission Master Bot

Telegram-бот для управления загрузками в Transmission через `docker compose`.

## Что умеет

- Принимать `magnet:` ссылки и `.torrent` файлы
- Раскладывать загрузки по категориям `Movies`, `Series`, `Music`, `Other`
- Показывать список торрентов и статус клиента Transmission
- Удалять торренты с файлами или без них
- Отправлять уведомления о завершении загрузки

## Быстрый старт

1. Скопируйте пример конфигурации:

```bash
cp .env.example .env
```

2. Откройте `.env` и заполните обязательные значения.

3. Запустите сервисы:

```bash
docker compose up -d --build
```

4. Откройте своего бота в Telegram и отправьте `/start`.

## Использование

- Отправьте боту `magnet:` ссылку или `.torrent` файл
- Выберите категорию загрузки
- Используйте кнопки `Список торрентов`, `Статус`, `Удалить торрент`, `Помощь`
- Веб-интерфейс Transmission будет доступен на `http://localhost:9091`, если вы не меняли порт в `.env`

## Полезные команды

```bash
docker compose ps
docker compose logs -f transmission_bot
docker compose restart transmission_bot
docker compose down
```