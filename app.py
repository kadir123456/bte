# app.py (Redis Hatası Düzeltmesi)

import os
import threading
from typing import Optional
from flask import (Flask, flash, jsonify, redirect, render_template, request,
                   session, url_for)
from flask_socketio import SocketIO
from trading_bot import TradingBot

# --- 1. UYGULAMA VE WEBSOCKET KURULUMU ---
app = Flask(__name__)
app.secret_key = os.environ.get('FLASK_SECRET_KEY', 'yerel_test_icin_rastgele_bir_anahtar_12345')

# --- DEĞİŞİKLİK BURADA ---
# message_queue=None parametresi ekleyerek Redis'e bağlanma girişimini devre dışı bırakıyoruz.
# Bu, [Errno 111] hatasını kesin olarak çözecektir.
socketio = SocketIO(app, async_mode='eventlet', message_queue=None)


# --- 2. GÜVENLİK AYARLARI ---
ADMIN_USERNAME = os.environ.get('ADMIN_USERNAME', 'admin')
ADMIN_PASSWORD = os.environ.get('ADMIN_PASSWORD', 'admin123')


# --- 3. BOT YÖNETİMİ ---
bot: Optional[TradingBot] = None
bot_thread: Optional[threading.Thread] = None


# --- 4. GERİ BİLDİRİM (CALLBACK) FONKSİYONLARI ---

def log_handler(message: str):
    """Bot'tan gelen log mesajlarını web arayüzüne anında gönderir."""
    socketio.emit('log_message', {'data': message})

def ui_update_handler():
    """Bot'taki önemli bir değişiklikten sonra arayüzün tamamını günceller."""
    if bot:
        position_data = bot.get_current_position_data()
        stats_data = bot.get_stats_data()
        socketio.emit('full_update', {
            'position': position_data,
            'stats': stats_data
        })

def bot_status_handler(is_active: bool, symbol: str):
    """Botun çalışma durumunu (başladı/durdu) arayüze anında bildirir."""
    socketio.emit('bot_status_update', {'status': is_active, 'symbol': symbol})


# --- 5. WEB SAYFALARI (ROUTES) ---

@app.route('/login', methods=['GET', 'POST'])
def login():
    """Kullanıcı giriş sayfasını yönetir ve botu ilk kez oluşturur."""
    global bot
    if request.method == 'POST':
        if request.form['username'] == ADMIN_USERNAME and request.form['password'] == ADMIN_PASSWORD:
            session['logged_in'] = True
            if bot is None:
                try:
                    bot = TradingBot(
                        log_callback=log_handler, 
                        ui_update_callback=ui_update_handler,
                        status_callback=bot_status_handler
                    )
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


# --- 6. WEBSOCKET OLAYLARI (EVENTS) ---

@socketio.on('connect')
def handle_connect():
    print('Client connected to WebSocket')
    if bot:
        bot_status_handler(bot.strategy_active, bot.active_symbol)
        ui_update_handler()

@socketio.on('get_initial_data')
def handle_get_initial_data():
    if bot:
        all_symbols = bot.get_all_usdt_symbols()
        all_trades = bot.get_all_trades_data()
        socketio.emit('initial_data', {
            'symbols': all_symbols,
            'trades': all_trades
        })


# --- 7. KONTROL İÇİN API ENDPOINTS (HTTP) ---

@app.route('/start_bot', methods=['POST'])
def start_bot():
    global bot_thread
    if not session.get('logged_in') or not bot: return jsonify({"status": "error"}), 401
    
    if not bot.strategy_active:
        bot_thread = threading.Thread(target=bot.start_strategy, daemon=True)
        bot_thread.start()
    return jsonify({"status": "success"})

@app.route('/stop_bot', methods=['POST'])
def stop_bot():
    if not session.get('logged_in') or not bot: return jsonify({"status": "error"}), 401
    
    if bot.strategy_active:
        bot.stop_strategy()
    return jsonify({"status": "success"})

# ... (Diğer tüm HTTP route'ları aynı kalır)
@app.route('/manual_trade', methods=['POST'])
def manual_trade():
    if not session.get('logged_in') or not bot: return jsonify({"status": "error"}), 401
    side = request.get_json().get('side')
    if side in ['LONG', 'SHORT']:
        threading.Thread(target=bot.manual_trade, args=(side,)).start()
        return jsonify({"status": "success"})
    return jsonify({"status": "error", "message": "Geçersiz işlem yönü"}), 400

@app.route('/close_position', methods=['POST'])
def close_position():
    if not session.get('logged_in') or not bot: return jsonify({"status": "error"}), 401
    threading.Thread(target=bot.close_current_position, args=(True,)).start()
    return jsonify({"status": "success"})

@app.route('/update_settings', methods=['POST'])
def update_settings():
    if not session.get('logged_in') or not bot: return jsonify({"status": "error"}), 401
    data = request.get_json()
    try:
        bot.set_leverage(int(data.get('leverage')))
        bot.set_quantity(float(data.get('quantity_usd')))
        return jsonify({"status": "success"})
    except (ValueError, TypeError):
        return jsonify({"status": "error", "message": "Geçersiz değerler"}), 400

@app.route('/update_symbol', methods=['POST'])
def update_symbol():
    if not session.get('logged_in') or not bot: return jsonify({"status": "error"}), 401
    new_symbol = request.get_json().get('symbol')
    if new_symbol:
        threading.Thread(target=bot.update_active_symbol, args=(new_symbol,)).start()
        return jsonify({"status": "success"})
    return jsonify({"status": "error", "message": "Geçersiz sembol"}), 400


if __name__ == '__main__':
    print("Starting Flask-SocketIO server for local development...")
    socketio.run(app, debug=True, host='0.0.0.0', port=5001)
