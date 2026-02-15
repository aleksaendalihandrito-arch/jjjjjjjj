import requests
import time
import os  # ВАЖНО: добавляем эту строку!
from datetime import datetime, date
import threading
import atexit
import sys

# Настройки - ТЕПЕРЬ БЕРЕМ ТОКЕН ИЗ ПЕРЕМЕННОЙ ОКРУЖЕНИЯ
TELEGRAM_BOT_TOKEN = os.environ.get('TELEGRAM_BOT_TOKEN')  # Берем значение из Render

# Проверяем, что токен получен
if not TELEGRAM_BOT_TOKEN:
    print("КРИТИЧЕСКАЯ ОШИБКА: Не найден TELEGRAM_BOT_TOKEN в переменных окружения!", flush=True)
    sys.exit(1)  # Останавливаем бота, если нет токена

PRICE_INCREASE_THRESHOLD = 1.5
PRICE_DECREASE_THRESHOLD = -50
TIME_WINDOW = 60 * 5
MAX_ALERTS_PER_DAY = 3

REQUEST_TIMEOUT = 10
MAX_RETRIES = 3
RETRY_DELAY = 2

# База данных пользователей
users = {
    '5296533274': {
        'active': True,
        'daily_alerts': {
            'date': date.today(),
            'counts': {}
        }
    }
}

historical_data = {}

def log_message(msg):
    print(msg, flush=True)
    sys.stdout.flush()

def make_request_with_retry(url, params=None, timeout=REQUEST_TIMEOUT, max_retries=MAX_RETRIES):
    for attempt in range(max_retries):
        try:
            response = requests.get(url, params=params, timeout=timeout)
            if response.status_code == 200:
                return response
        except:
            pass
        if attempt < max_retries - 1:
            time.sleep(RETRY_DELAY * (attempt + 1))
    return None

def generate_links(symbol):
    clean_symbol = symbol.replace('USDT', '').replace('1000', '')
    return {
        'coinglass': f"https://www.coinglass.com/pro/futures/LiquidationHeatMapModel3?coin={clean_symbol}&type=pair",
        'tradingview': f"https://www.tradingview.com/chart/?symbol=BINANCE:{symbol}",
        'binance': f"https://www.binance.com/ru/trade/{symbol}",
        'bybit': f"https://www.bybit.com/trade/usdt/{symbol}"
    }

def send_telegram_notification(chat_id, message, symbol, exchange):
    if chat_id not in users or not users[chat_id]['active']:
        return False
        
    links = generate_links(symbol)
    message_with_links = (
        f"{message}\n\n"
        f"🔗 <b>Ссылки:</b>\n"
        f"• <a href='{links['coinglass']}'>Coinglass</a>\n"
        f"• <a href='{links['tradingview']}'>TradingView</a>\n"
        f"• <a href='{links['binance']}'>Binance</a>\n"
        f"• <a href='{links['bybit']}'>Bybit</a>"
    )

    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {
        'chat_id': chat_id,
        'text': message_with_links,
        'parse_mode': 'HTML',
        'disable_web_page_preview': False
    }
    try:
        response = requests.post(url, json=payload, timeout=REQUEST_TIMEOUT)
        log_message(f"Отправлено уведомление пользователю {chat_id}: {response.status_code}")
        return True
    except Exception as e:
        log_message(f"Ошибка отправки пользователю {chat_id}: {e}")
        return False

def calculate_change(old, new):
    if old == 0:
        return 0.0
    return ((new - old) / old) * 100

def fetch_binance_symbols():
    url = "https://api.binance.com/api/v3/exchangeInfo"
    response = make_request_with_retry(url, timeout=15)
    if response:
        try:
            data = response.json()
            symbols = []
            for symbol_info in data['symbols']:
                if symbol_info['quoteAsset'] == 'USDT' and symbol_info['status'] == 'TRADING':
                    symbols.append(symbol_info['symbol'])
            log_message(f"Binance: получено {len(symbols)} символов")
            return symbols[:50]  # Ограничим для теста
        except Exception as e:
            log_message(f"Ошибка парсинга Binance: {e}")
    return []

def fetch_bybit_symbols():
    url = "https://api.bybit.com/v5/market/instruments-info"
    params = {"category": "linear"}
    response = make_request_with_retry(url, params)
    if response:
        try:
            data = response.json()
            if data['retCode'] == 0:
                symbols = [item['symbol'] for item in data['result']['list']]
                log_message(f"Bybit: получено {len(symbols)} символов")
                return symbols[:50]  # Ограничим для теста
        except Exception as e:
            log_message(f"Ошибка парсинга Bybit: {e}")
    return []

def fetch_binance_ticker(symbol):
    url = "https://api.binance.com/api/v3/ticker/24hr"
    params = {"symbol": symbol}
    response = make_request_with_retry(url, params)
    if response:
        try:
            data = response.json()
            if 'code' in data and data['code'] == -1121:
                return None
            return {
                'symbol': data['symbol'],
                'lastPrice': float(data['lastPrice']),
                'priceChangePercent': float(data['priceChangePercent'])
            }
        except Exception as e:
            log_message(f"Ошибка тикера Binance {symbol}: {e}")
    return None

def fetch_bybit_ticker(symbol):
    url = "https://api.bybit.com/v5/market/tickers"
    params = {"category": "linear", "symbol": symbol}
    response = make_request_with_retry(url, params)
    if response:
        try:
            data = response.json()
            if data['retCode'] == 0 and data['result']['list']:
                ticker = data['result']['list'][0]
                return {
                    'symbol': ticker['symbol'],
                    'lastPrice': float(ticker['lastPrice']),
                    'priceChangePercent': float(ticker['price24hPcnt']) * 100
                }
        except Exception as e:
            log_message(f"Ошибка тикера Bybit {symbol}: {e}")
    return None

def handle_telegram_updates():
    last_update_id = 0
    while True:
        try:
            url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/getUpdates"
            params = {'timeout': 30, 'offset': last_update_id + 1}
            response = requests.get(url, params=params, timeout=35)
            data = response.json()
            if data['ok']:
                for update in data['result']:
                    last_update_id = update['update_id']
                    if 'message' not in update:
                        continue
                    message = update['message']
                    chat_id = str(message['chat']['id'])
                    text = message.get('text', '').strip().lower()
                    
                    if text == '/start':
                        if chat_id not in users:
                            users[chat_id] = {
                                'active': True,
                                'daily_alerts': {
                                    'date': date.today(),
                                    'counts': {}
                                }
                            }
                            log_message(f"Новый пользователь: {chat_id}")
                            send_telegram_notification(chat_id, "✅ Вы подписались на уведомления!", "", "")
                    
                    elif text == '/stop':
                        if chat_id in users:
                            del users[chat_id]
                            log_message(f"Пользователь {chat_id} удален")
                    
                    elif text == '/help':
                        help_text = "🤖 <b>Команды:</b>\n/start - подписаться\n/stop - отписаться\n/help - помощь"
                        send_telegram_notification(chat_id, help_text, "", "")
            time.sleep(1)
        except Exception as e:
            log_message(f"Ошибка в обработке: {e}")
            time.sleep(5)

def monitor_exchange(exchange_name, fetch_symbols_func, fetch_ticker_func):
    log_message(f"Запуск мониторинга {exchange_name}...")
    symbols = fetch_symbols_func()
    if not symbols:
        log_message(f"Нет символов с {exchange_name}")
        return

    while True:
        try:
            for symbol in symbols:
                ticker_data = fetch_ticker_func(symbol)
                if ticker_data:
                    current_price = ticker_data['lastPrice']
                    timestamp = int(datetime.now().timestamp())
                    key = f"{exchange_name}_{symbol}"
                    
                    if key not in historical_data:
                        historical_data[key] = {'price': []}
                    
                    historical_data[key]['price'].append({'value': current_price, 'timestamp': timestamp})
                    historical_data[key]['price'] = [x for x in historical_data[key]['price']
                                                     if timestamp - x['timestamp'] <= TIME_WINDOW]
                    
                    if len(historical_data[key]['price']) > 1:
                        old_price = historical_data[key]['price'][0]['value']
                        price_change = calculate_change(old_price, current_price)
                        
                        if abs(price_change) >= PRICE_INCREASE_THRESHOLD:
                            direction = "📈 Рост" if price_change > 0 else "📉 Падение"
                            for chat_id in users:
                                if users[chat_id]['active']:
                                    msg = (f"{direction} <b>{symbol}</b> ({exchange_name})\n"
                                           f"Изменение: {price_change:.2f}%\n"
                                           f"Цена: {current_price:.8f}")
                                    send_telegram_notification(chat_id, msg, symbol, exchange_name)
            
            log_message(f"{exchange_name}: проверка завершена")
            time.sleep(10)
        except Exception as e:
            log_message(f"Ошибка в {exchange_name}: {e}")
            time.sleep(30)

def main():
    log_message("=" * 50)
    log_message(f"ЗАПУСК БОТА")
    log_message(f"Токен загружен: {'Да' if TELEGRAM_BOT_TOKEN else 'НЕТ!'}")
    log_message("=" * 50)
    
    # Запускаем обработчик сообщений
    update_thread = threading.Thread(target=handle_telegram_updates, daemon=True)
    update_thread.start()
    
    # Отправляем сообщение о запуске
    for chat_id in users:
        send_telegram_notification(chat_id, "🔍 <b>Бот запущен!</b>", "", "")
    
    # Запускаем мониторинг бирж
    binance_thread = threading.Thread(
        target=monitor_exchange,
        args=("Binance", fetch_binance_symbols, fetch_binance_ticker),
        daemon=True
    )
    
    bybit_thread = threading.Thread(
        target=monitor_exchange,
        args=("Bybit", fetch_bybit_symbols, fetch_bybit_ticker),
        daemon=True
    )
    
    binance_thread.start()
    bybit_thread.start()
    
    # Держим бот запущенным
    while True:
        time.sleep(60)
        log_message(f"Бот работает... Пользователей: {len(users)}")

if __name__ == "__main__":
    main()
