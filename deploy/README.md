# Деплой 1C Info Bot на Linux (Docker + Nginx + Let's Encrypt)

Полный деплой бота мониторинга релизов 1С для Delo Space на публичный HTTPS-сервер,
чтобы BotX мог слать боту webhook-сообщения и бот отвечал в чате.

## Состав деплоя

```
deploy/
├── Dockerfile           # образ бота (python:3.11-slim)
├── docker-compose.yml    # bot + nginx + certbot
├── install.sh            # одношаговая установка (сертификат + запуск)
├── nginx/nginx.conf      # HTTPS-терминатор, прокси на бота
└── (создаётся при запуске: certbot/ — сертификаты)
```

## Требования к серверу

- Linux (Ubuntu/Debian рекомендуются)
- **docker** + **docker compose**
- Публичный IP, домен с A-записью → IP (например `bot.example.com`)
- Порты 80 и 443 открыты наружу (firewall: `sudo ufw allow 80,443/tcp`)

## Шаги

### 1. Скопировать проект на сервер

```bash
scp -r ~/ТЗ_Проекты/_Инструменты/1c_releases/delo_space_bot user@server:/opt/
# или через git
```

### 2. Заполнить `.env` на сервере

```bash
cd /opt/delo_space_bot
cp .env.example .env
nano .env    # BOT_ID, CTS_URL, SECRET_KEY, ITS_USERNAME, ITS_PASSWORD
chmod 600 .env
```

### 3. Установить (один скрипт)

```bash
cd /opt/delo_space_bot/deploy
export DOMAIN=bot.example.com
export EMAIL=you@example.com
bash install.sh
```

Скрипт:
1. Подставляет домен в `nginx.conf`
2. Запускает бота + Nginx (HTTP)
3. Выпускает сертификат Let's Encrypt
4. Перезапускает Nginx с HTTPS
5. Печатает итоговый webhook-адрес

### 4. Отдать webhook админу

Адрес для BotX:
```
https://bot.example.com/command
```
Сообщить админу Delo Space, чтобы заменил webhook бота на этот адрес.

## Проверка

```bash
curl https://bot.example.com/status          # статус бота
docker compose logs -f bot                   # логи бота
docker compose ps                            # состояние контейнеров
```

## Управление

```bash
cd /opt/delo_space_bot/deploy
docker compose up -d              # запустить
docker compose down              # остановить
docker compose logs -f bot       # логи
docker compose pull && docker compose up -d --build   # обновить
```

## Автообновление сертификата

Certbot-контейнер обновляет сертификат каждые 12ч автоматически.
Если потребуется вручную:
```bash
docker compose run --rm certbot renew --webroot -w /var/www/certbot
docker compose restart nginx
```

## Troubleshooting

| Проблема | Решение |
|---|---|
| `curl /status` 404 | бот не стартовал — `docker compose logs bot`; проверить `.env` |
| Сертификат не выпускается | домен/порт 80 не доступен извне; проверить A-запись и firewall |
| Бот не отвечает на сообщения | webhook в админке не совпадает с `https://домен/command` |
| Изменения кода | `docker compose up -d --build` |