#!/bin/bash
# Скрипт розгортання Trade Bot на Linux CPU-сервері (без GPU)
set -e

echo "=== Trade Bot CPU Server Deploy ==="

# 1. Встановлення Ollama (якщо не встановлено)
if ! command -v ollama &> /dev/null; then
    echo "[1/5] Встановлення Ollama..."
    curl -fsSL https://ollama.com/install.sh | sh
    systemctl enable ollama
    systemctl start ollama
    sleep 3
else
    echo "[1/5] Ollama вже встановлена."
    systemctl start ollama 2>/dev/null || true
fi

# 2. Завантаження моделі
echo "[2/5] Завантаження моделі hermes3:8b-llama3.1-q2_K (~3.2GB)..."
ollama pull hermes3:8b-llama3.1-q2_K
echo "Модель готова."

# 3. Встановлення Docker (якщо не встановлено)
if ! command -v docker &> /dev/null; then
    echo "[3/5] Встановлення Docker..."
    curl -fsSL https://get.docker.com | sh
    usermod -aG docker $USER
    systemctl enable docker
    systemctl start docker
else
    echo "[3/5] Docker вже встановлений."
fi

# 4. Створення папок для даних
echo "[4/5] Підготовка директорій..."
mkdir -p data logs

# 5. Запуск бота
echo "[5/5] Запуск Trade Bot..."
docker compose up -d --build

echo ""
echo "=== Готово! ==="
echo "Dashboard: http://$(hostname -I | awk '{print $1}'):8080"
echo "Логи:      docker compose logs -f tradebot"
echo "Зупинка:   docker compose down"
