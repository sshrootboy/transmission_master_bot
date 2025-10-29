#!/bin/bash

echo "🚀 Установка Telegram Bot для Transmission"
echo "=========================================="
echo ""

# Проверка Docker
if ! command -v docker &> /dev/null; then
    echo "❌ Docker не установлен. Установите Docker: https://docs.docker.com/get-docker/"
    exit 1
fi

echo "✅ Docker установлен"
echo ""

# Создание структуры папок
echo "📁 Создание структуры папок..."
mkdir -p transmission downloads transmission/watch downloads/complete downloads/incomplete

# Проверка .env файла
if [ ! -f .env ]; then
    echo "⚠️  Файл .env не найден"

    if [ -f .env.example ]; then
        echo "📋 Копирую .env.example в .env"
        cp .env.example .env
        echo ""
        echo "⚙️  ВАЖНО: Отредактируйте .env файл и заполните:"
        echo "   - BOT_TOKEN (получите у @BotFather)"
        echo "   - ALLOWED_USER_IDS (ваш Telegram ID от @userinfobot)"
        echo "   - TRANSMISSION_USER и TRANSMISSION_PASS"
        echo ""
        echo "Затем запустите: docker-compose up -d"
        exit 0
    else
        echo "❌ .env.example не найден. Создайте .env файл вручную."
        exit 1
    fi
fi

echo "✅ Файл .env найден"
echo ""

# Проверка обязательных параметров
echo "🔍 Проверка конфигурации..."

if grep -q "your_bot_token_here" .env; then
    echo "❌ BOT_TOKEN не настроен в .env"
    echo "   Получите токен у @BotFather и обновите .env"
    exit 1
fi

if grep -q "123456789" .env; then
    echo "⚠️  ALLOWED_USER_IDS выглядит как пример"
    echo "   Получите ваш ID у @userinfobot и обновите .env"
fi

echo "✅ Базовая конфигурация выглядит правильно"
echo ""

# Получение PUID и PGID
CURRENT_PUID=$(id -u)
CURRENT_PGID=$(id -g)

echo "ℹ️  Ваш PUID: $CURRENT_PUID"
echo "ℹ️  Ваш PGID: $CURRENT_PGID"

# Обновление PUID и PGID в .env если они не заданы
if ! grep -q "PUID=" .env || grep -q "PUID=1000" .env; then
    echo "📝 Обновляю PUID в .env..."
    sed -i.bak "s/PUID=.*/PUID=$CURRENT_PUID/" .env
fi

if ! grep -q "PGID=" .env || grep -q "PGID=1000" .env; then
    echo "📝 Обновляю PGID в .env..."
    sed -i.bak "s/PGID=.*/PGID=$CURRENT_PGID/" .env
fi

echo ""
echo "🐳 Запуск Docker Compose..."
docker compose up -d --build

echo ""
echo "✅ Система запущена!"
echo ""
echo "📊 Проверить статус: docker-compose ps"
echo "📜 Просмотр логов: docker-compose logs -f transmission_bot"
echo "🌐 Веб-интерфейс Transmission: http://localhost:9091"
echo ""
echo "🤖 Откройте вашего бота в Telegram и отправьте /start"