# transmission_master_bot

```
docker compose up -d --build
```

Для запуска Telegram-бота через `socks5` proxy можно указать переменную окружения:

```env
TELEGRAM_PROXY_URL=socks5://user:password@proxy.example.com:1080
```

После изменения переменных пересоберите контейнер:

```bash
docker compose up -d --build
```
