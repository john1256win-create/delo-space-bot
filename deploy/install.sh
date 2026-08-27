#!/usr/bin/env bash
# install.sh — полная установка 1C Info Bot на Linux-сервер (Docker + Nginx + Let's Encrypt)
#
# Использование:
#   export DOMAIN=bot.example.com          # обязательно
#   export EMAIL=you@example.com           # для Let's Encrypt
#   bash install.sh
#
# Перед запуском: заполнить ../.env (BOT_ID, CTS_URL, SECRET_KEY, ITS_USERNAME, ITS_PASSWORD).
# Домен должен указывать на публичный IP этого сервера (A-запись).

set -euo pipefail

# ── конфиг ─────────────────────────────────────────────
if [ -z "${DOMAIN:-}" ]; then
    echo "❌ Задай DOMAIN: export DOMAIN=bot.example.com"
    exit 1
fi
if [ -z "${EMAIL:-}" ]; then
    echo "❌ Задай EMAIL для Let's Encrypt: export EMAIL=you@example.com"
    exit 1
fi
cd "$(dirname "$0")"
COMPOSE="docker compose"   # для старых версий: заменить на "docker-compose"

echo "🚀 Деплой 1C Info Bot на домен: $DOMAIN"

# ── 0. Проверка зависимостей ────────────────────────────
command -v docker >/dev/null || { echo "❌ Нет docker"; exit 1; }
$COMPOSE version >/dev/null 2>&1 || { echo "❌ Нет docker compose"; exit 1; }

# ── 1. Подставляем домен в nginx.conf ──────────────────
sed -i.bak "s/bot\.example\.com/$DOMAIN/g" nginx/nginx.conf
echo "✅ Домен подставлен в nginx.conf"

# ── 2. Первый запуск без HTTPS (только HTTP+ACME) ──────
echo "→> Запускаю Nginx + бота (первичный, для ACME)..."
$COMPOSE up -d --build bot nginx

# ── 3. Выпуск сертификата Let's Encrypt ────────────────
echo "→> Выпускаю сертификат Let's Encrypt для $DOMAIN..."
$COMPOSE run --rm certbot certonly \
    --webroot -w /var/www/certbot \
    -d "$DOMAIN" \
    --email "$EMAIL" \
    --agree-tos --no-eff-email

# ── 4. Перезапуск Nginx с HTTPS ────────────────────────
echo "→> Перезапускаю Nginx с HTTPS..."
$COMPOSE restart nginx

# ── 5. Финальная проверка ──────────────────────────────
echo ""
echo "✅ Деплой завершён!"
echo "   Webhook бота: https://$DOMAIN/command"
echo "   Статус:        https://$DOMAIN/status"
echo ""
echo "   Укажите админу Delo Space webhook: https://$DOMAIN/command"
echo ""
echo "   Проверка:"
echo "     curl https://$DOMAIN/status"
echo "     docker compose logs -f bot"