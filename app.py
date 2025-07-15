import os
import threading
import time
from flask import Flask, render_template, request, session, redirect, url_for, jsonify
from flask_socketio import SocketIO

app = Flask(__name__)
app.secret_key = os.environ.get('FLASK_SECRET_KEY', 'gizli_anahtar')
socketio = SocketIO(app, async_mode='eventlet')

class TradingBot:
    def __init__(self, log_callback=None, status_callback=None):
        self.strategy_active = False
        self.log_callback = log_callback
        self.status_callback = status_callback
        self._stop_event = threading.Event()

    def start_strategy(self):
        if self.strategy_active:
            self.log("Bot zaten çalışıyor.")
            return
        self.strategy_active = True
        self.log("Bot başlatıldı.")
        self.status_update()

        while not self._stop_event.is_set():
            self.log("Bot çalışıyor... (örnek işlem döngüsü)")
            time.sleep(3)

        self.strategy_active = False
        self.log("Bot durduruldu.")
        self.status_update()

    def stop_strategy(self):
        if not self.strategy_active:
            self.log("Bot zaten durdu.")
            return
        self._stop_event.set()

    def log(self, message):
        print(message)
        if self.log_callback:
            self.log_callback(message)

    def status_update(self):
        if self.status_callback:
            self.status_callback(self.strategy_active, "BTCUSDT")

bot = TradingBot()
bot_thread = None

@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        if request.form.get('username') == 'admin' and request.form.get('password') == 'admin123':
            session['logged_in'] = True
            return redirect(url_for('dashboard'))
        else:
            return "Hatalı kullanıcı adı veya şifre", 401
    return '''
        <form method="post">
            Kullanıcı: <input name="username"><br>
            Şifre: <input name="password" type="password"><br>
            <input type="submit" value="Giriş">
        </form>
    '''

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

@app.route('/start_bot', methods=['POST'])
def start_bot():
    global bot_thread
    if not session.get('logged_in'):
        return jsonify({"status": "error", "message": "Yetkisiz"}), 401

    if not bot.strategy_active:
        bot._stop_event.clear()
        bot_thread = threading.Thread(target=bot.start_strategy, daemon=True)
        bot_thread.start()
        return jsonify({"status": "success", "message": "Bot başlatıldı"})
    else:
        return jsonify({"status": "error", "message": "Bot zaten çalışıyor"})

@app.route('/stop_bot', methods=['POST'])
def stop_bot():
    if not session.get('logged_in'):
        return jsonify({"status": "error", "message": "Yetkisiz"}), 401

    if bot.strategy_active:
        bot.stop_strategy()
        return jsonify({"status": "success", "message": "Bot durduruldu"})
    else:
        return jsonify({"status": "error", "message": "Bot zaten durdu"})

@socketio.on('connect')
def on_connect():
    print("Client bağlandı")
    bot.status_update()

def send_log(message):
    socketio.emit('log_message', {'data': message})

def send_status(is_active, symbol):
    socketio.emit('bot_status_update', {'status': is_active, 'symbol': symbol})

bot.log_callback = send_log
bot.status_callback = send_status

if __name__ == '__main__':
    socketio.run(app, host='0.0.0.0', port=5000)
