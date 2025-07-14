import os
import threading
from queue import Queue
import time
from flask import (Flask, flash, jsonify, redirect, render_template, request,
                   session, url_for)

# Diğer Python dosyalarımızdan ilgili sınıfları ve fonksiyonları import ediyoruz
from trading_bot import TradingBot
import database

# --- UYGULAMA KURULUMU ---
app = Flask(__name__)
# Bu anahtarı Render.com'da Environment Variable olarak ayarlayacaksınız
# Web uygulamasının oturum (session) güvenliği için kullanılır
app.secret_key = os.environ.get('FLASK_SECRET_KEY', 'yerel_test_icin_rastgele_bir_anahtar_12345')

# --- GÜVENLİK ---
# Bu bilgileri Render.com'da Environment Variable olarak ayarlayacaksınız
ADMIN_USERNAME = os.environ.get('ADMIN_USERNAME', 'admin')
ADMIN_PASSWORD = os.environ.get('ADMIN_PASSWORD', 'admin123')

# --- BOT YÖNETİMİ ---
# Bu global değişkenler, web sunucusunun bot objesini ve loglarını hafızada tutmasını sağlar
bot: TradingBot = None
log_queue = Queue()

def ui_callback_handler(message_type, data=None):
    """Bot motorundan gelen logları ve güncellemeleri arayüz için sıraya (queue) alır."""
    # Bu fonksiyon, trading_bot tarafından çağrılacak ve verileri arayüze iletecek
    log_queue.put({"type": message_type, "data": data})

# --- WEB SAYFALARI (ROUTES) ---

@app.route('/login', methods=['GET', 'POST'])
def login():
    """Kullanıcı giriş sayfasını yönetir."""
    if request.method == 'POST':
        if request.form['username'] == ADMIN_USERNAME and request.form['password'] == ADMIN_PASSWORD:
            session['logged_in'] = True
            global bot
            # Bot objesi sadece ilk başarılı girişte bir kez oluşturulur
            if bot is None:
                try:
                    # DÜZELTME: Doğru argüman adını kullan: ui_update_callback
                    bot = TradingBot(ui_update_callback=ui_callback_handler)
                except ValueError as e:
                    flash(str(e))
                    return render_template('login.html')
            return redirect(url_for('dashboard'))
        else:
            flash('Geçersiz kullanıcı adı veya şifre')
    return render_template('login.html')

@app.route('/')
def index():
    """Ana sayfayı giriş sayfasına yönlendirir."""
    if not session.get('logged_in'):
        return redirect(url_for('login'))
    return redirect(url_for('dashboard'))

@app.route('/dashboard')
def dashboard():
    """Ana kontrol panelini gösterir. Giriş yapılmamışsa login'e yönlendirir."""
    if not session.get('logged_in'):
        return redirect(url_for('login'))
    return render_template('dashboard.html')

@app.route('/logout')
def logout():
    """Kullanıcı oturumunu sonlandırır."""
    session.pop('logged_in', None)
    return redirect(url_for('login'))

# --- KONTROL İÇİN API ENDPOINTS (JavaScript tarafından çağrılır) ---

@app.route('/start_bot', methods=['POST'])
def start_bot():
    """Arayüzden gelen 'Başlat' komutunu işler."""
    if not session.get('logged_in'): return jsonify({"status": "error", "message": "Yetkisiz"}), 401
    if bot and not bot.strategy_active:
        bot.start_strategy_loop()
    return jsonify({"status": "success"})

@app.route('/stop_bot', methods=['POST'])
def stop_bot():
    """Arayüzden gelen 'Durdur' komutunu işler."""
    if not session.get('logged_in'): return jsonify({"status": "error", "message": "Yetkisiz"}), 401
    if bot:
        bot.stop_strategy_loop()
    return jsonify({"status": "success"})

@app.route('/get_status')
def get_status():
    """Arayüzü beslemek için tüm anlık verileri döndürür."""
    if not session.get('logged_in'): return jsonify({"status": "error"}), 401
    
    updates = []
    while not log_queue.empty():
        updates.append(log_queue.get())
    
    if bot:
        return jsonify({
            "updates": updates,
            "bot_status": bot.strategy_active,
            "open_positions": bot.get_current_position_data(),
            "stats": database.calculate_stats()
        })
    return jsonify({"updates": updates, "bot_status": False, "open_positions": [], "stats": database.calculate_stats()})

@app.route('/get_trade_history')
def get_trade_history():
    """Veritabanındaki tüm geçmiş işlemleri döndürür."""
    if not session.get('logged_in'): return jsonify({"status": "error"}), 401
    trades = database.get_all_trades()
    return jsonify({"history": trades})

@app.route('/update_settings', methods=['POST'])
def update_settings():
    """Arayüzden gelen tüm ayarları günceller."""
    if not session.get('logged_in'): return jsonify({"status": "error"}), 401
    data = request.get_json()
    if bot:
        try:
            bot.update_settings(data)
            return jsonify({"status": "success", "message": "Ayarlar başarıyla güncellendi."})
        except (ValueError, TypeError) as e:
            return jsonify({"status": "error", "message": f"Geçersiz değerler: {e}"}), 400
    return jsonify({"status": "error", "message": "Bot aktif değil."}), 400

@app.route('/update_tradeable_symbols', methods=['POST'])
def update_tradeable_symbols():
    """İşlem yapılacak coin listesini günceller."""
    if not session.get('logged_in'): return jsonify({"status": "error"}), 401
    data = request.get_json()
    symbols_str = data.get('symbols')
    if bot and isinstance(symbols_str, str):
        bot.update_tradeable_symbols(symbols_str)
        return jsonify({"status": "success"})
    return jsonify({"status": "error"}), 400

@app.route('/close_all_positions', methods=['POST'])
def close_all_positions():
    """Tüm açık pozisyonları kapatma komutunu işler."""
    if not session.get('logged_in'): return jsonify({"status": "error"}), 401
    if bot:
        threading.Thread(target=bot.close_all_positions, daemon=True).start()
    return jsonify({"status": "success"})

if __name__ == '__main__':
    # Bu kısım sadece yerel testler içindir. 
    # Render.com gibi sunucular bu bloğu çalıştırmaz, onun yerine 'gunicorn' kullanır.
    app.run(debug=False, host='0.0.0.0', port=8080)
