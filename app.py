# app.py

import os
import threading
import time
from queue import Queue

from flask import (Flask, flash, jsonify, redirect, render_template, request,
                   session, url_for)

from trading_bot import TradingBot
import database # Veritabanı istatistikleri için eklendi

# --- UYGULAMA KURULUMU ---
app = Flask(__name__)
app.secret_key = os.environ.get('FLASK_SECRET_KEY', 'yerel_test_icin_rastgele_bir_anahtar_12345')

# --- GÜVENLİK ---
ADMIN_USERNAME = os.environ.get('ADMIN_USERNAME', 'admin')
ADMIN_PASSWORD = os.environ.get('ADMIN_PASSWORD', 'admin123')

# --- BOT YÖNETİMİ ---
bot: Optional[TradingBot] = None
log_queue = Queue()
bot_thread: Optional[threading.Thread] = None

def log_handler(message):
    """Bot motorundan gelen logları arayüz için sıraya (queue) alır."""
    if log_queue.qsize() < 1000: # Kuyruğun aşırı büyümesini engelle
        log_queue.put(message)

# --- WEB SAYFALARI (ROUTES) ---

@app.route('/login', methods=['GET', 'POST'])
def login():
    global bot
    if request.method == 'POST':
        if request.form['username'] == ADMIN_USERNAME and request.form['password'] == ADMIN_PASSWORD:
            session['logged_in'] = True
            if bot is None:
                try:
                    # Bot objesi sadece ilk başarılı girişte bir kez oluşturulur
                    bot = TradingBot(log_callback=log_handler)
                except ValueError as e:
                    flash(str(e))
                    return render_template('login.html')
            return redirect(url_for('dashboard'))
        else:
            flash('Geçersiz kullanıcı adı veya şifre')
    return render_template('login.html')

@app.route('/')
def index():
    if not session.get('logged_in'):
        return redirect(url_for('login'))
    return redirect(url_for('dashboard'))

@app.route('/dashboard')
def dashboard():
    if not session.get('logged_in'):
        return redirect(url_for('login'))
    return render_template('dashboard.html')

@app.route('/logout')
def logout():
    session.pop('logged_in', None)
    flash('Başarıyla çıkış yaptınız.')
    return redirect(url_for('login'))

# --- API ENDPOINTS (JavaScript tarafından çağrılır) ---

@app.route('/start_bot', methods=['POST'])
def start_bot():
    global bot_thread
    if not session.get('logged_in'): return jsonify({"status": "error", "message": "Yetkisiz"}), 401
    if bot and not bot.strategy_active:
        if bot_thread and bot_thread.is_alive():
            return jsonify({"status": "error", "message": "Bot zaten çalışıyor."})
        # Botun ana strateji döngüsünü ayrı bir thread'de başlatır
        bot_thread = threading.Thread(target=bot.run_strategy, daemon=True)
        bot_thread.start()
        return jsonify({"status": "success", "message": "Bot başlatıldı."})
    return jsonify({"status": "error", "message": "Bot zaten aktif veya başlatılamadı."})

@app.route('/stop_bot', methods=['POST'])
def stop_bot():
    if not session.get('logged_in'): return jsonify({"status": "error", "message": "Yetkisiz"}), 401
    if bot:
        bot.stop_strategy_loop()
        return jsonify({"status": "success", "message": "Bot durduruluyor..."})
    return jsonify({"status": "error", "message": "Bot bulunamadı."})

@app.route('/get_status')
def get_status():
    if not session.get('logged_in'): return jsonify({"status": "error", "message": "Yetkisiz"}), 401
    
    logs = []
    while not log_queue.empty():
        logs.append(log_queue.get())
    
    if bot:
        return jsonify({
            "logs": logs,
            "bot_status": bot.strategy_active,
            "active_symbol": bot.active_symbol,
            "position": bot.get_current_position_data(),
            "stats": database.calculate_stats(),
            "all_trades": database.get_all_trades()
        })
    return jsonify({"logs": logs, "bot_status": False, "active_symbol": "N/A", "position": None, "stats": database.calculate_stats(), "all_trades": database.get_all_trades()})


@app.route('/get_all_symbols')
def get_all_symbols():
    if not session.get('logged_in'): return jsonify({"status": "error", "message": "Yetkisiz"}), 401
    if bot:
        symbols = bot.get_all_usdt_symbols()
        return jsonify({"symbols": symbols})
    return jsonify({"symbols": []})

@app.route('/update_symbol', methods=['POST'])
def update_symbol():
    if not session.get('logged_in'): return jsonify({"status": "error", "message": "Yetkisiz"}), 401
    data = request.get_json()
    new_symbol = data.get('symbol')
    if bot and new_symbol:
        bot.update_active_symbol(new_symbol)
        return jsonify({"status": "success", "message": f"Sembol {new_symbol} olarak güncellendi."})
    return jsonify({"status": "error", "message": "Sembol güncellenemedi."}), 400

@app.route('/manual_trade', methods=['POST'])
def manual_trade():
    if not session.get('logged_in'): return jsonify({"status": "error", "message": "Yetkisiz"}), 401
    data = request.get_json()
    side = data.get('side')
    if bot and side in ['LONG', 'SHORT']:
        threading.Thread(target=bot.manual_trade, args=(side,), daemon=True).start()
        return jsonify({"status": "success", "message": f"Manuel {side} işlemi tetiklendi."})
    return jsonify({"status": "error", "message": "Geçersiz işlem yönü."}), 400

@app.route('/close_position', methods=['POST'])
def close_position():
    if not session.get('logged_in'): return jsonify({"status": "error", "message": "Yetkisiz"}), 401
    if bot:
        threading.Thread(target=bot.close_current_position, args=(True,), daemon=True).start()
        return jsonify({"status": "success", "message": "Pozisyon kapatma emri gönderildi."})
    return jsonify({"status": "error", "message": "Bot bulunamadı."})

@app.route('/update_settings', methods=['POST'])
def update_settings():
    if not session.get('logged_in'): return jsonify({"status": "error", "message": "Yetkisiz"}), 401
    data = request.get_json()
    if bot:
        try:
            leverage = int(data.get('leverage'))
            quantity_usd = float(data.get('quantity_usd'))
            bot.set_leverage(leverage, bot.active_symbol)
            bot.set_quantity(quantity_usd)
            return jsonify({"status": "success", "message": "Ayarlar güncellendi."})
        except (ValueError, TypeError, KeyError):
            return jsonify({"status": "error", "message": "Geçersiz değerler."}), 400
    return jsonify({"status": "error", "message": "Bot aktif değil."}), 400

if __name__ == '__main__':
    # Bu kısım sadece yerel testler içindir. Render gunicorn kullanır.
    # 'host' ve 'port' Render tarafından yönetilecektir.
    app.run(debug=False)
