import os
import configparser
import time
import pandas as pd
import asyncio
import threading
from typing import Callable, Optional, List, Dict, Any
from binance.client import Client
from binance.enums import *
from binance.exceptions import BinanceAPIException
from binance import BinanceSocketManager
import strategy as strategy_kadir_v2
import strategy_scalper
import database
import pandas_ta as ta


class TradingBot:
    """
    Binance'e WebSocket ile bağlanarak anlık veri işleyen,
    Flask-SocketIO ile web arayüzüne anlık güncellemeler gönderen ticaret botu.
    """

    def __init__(self, log_callback: Optional[Callable] = None, 
                 ui_update_callback: Optional[Callable] = None,
                 status_callback: Optional[Callable] = None):
        self.log_callback = log_callback
        self.ui_update_callback = ui_update_callback
        self.status_callback = status_callback

        # ✅ Eksik olan log fonksiyonu eklendi
        self._log = self.log_callback if self.log_callback else lambda msg: print(msg)

        self.config = self._load_config()

        api_key = os.environ.get('BINANCE_API_KEY')
        api_secret = os.environ.get('BINANCE_API_SECRET')

        if not api_key or not api_secret:
            self._log_and_raise("HATA: API anahtarları ortam değişkenlerinde bulunamadı.")

        self.is_testnet = self.config.getboolean('BINANCE', 'testnet', fallback=False)
        self.client = Client(api_key, api_secret, testnet=self.is_testnet)

        self.strategy_active: bool = False
        self.active_symbol = self.config['TRADING']['symbol']
        self.active_strategy_name = self.config['TRADING']['active_strategy']
        self.quantity_usd = float(self.config['TRADING']['quantity_usd'])
        self.leverage = int(self.config['TRADING']['leverage'])

        self.bm = BinanceSocketManager(self.client)
        self.kline_socket = None
        self.user_socket = None
        self.loop = None

        self._log("WebSocket Uyumlu Bot objesi başarıyla oluşturuldu.")

    # --- Strateji Başlatma ve Durdurma ---
    def start_strategy(self):
        if self.strategy_active:
            self._log("Strateji zaten çalışıyor.")
            return
        self.strategy_active = True
        self._log(f"WebSocket Stratejisi ({self.active_symbol}) başlatılıyor...")
        if self.status_callback:
            self.status_callback(True, self.active_symbol)
        self.loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self.loop)
        self.loop.run_until_complete(self.listen_to_streams())
        self._log("Strateji dinleme döngüsü sonlandı.")

    def stop_strategy(self):
        if not self.strategy_active:
            self._log("Strateji zaten durdurulmuş.")
            return
        self.strategy_active = False
        if self.loop and self.loop.is_running():
            self.loop.call_soon_threadsafe(self.loop.stop)
        self._log("WebSocket dinleyicileri durduruldu.")
        if self.status_callback:
            self.status_callback(False, self.active_symbol)

    # --- WebSocket Dinleyicileri ---
    async def listen_to_streams(self):
        self._log(f"'{self.active_symbol}' için veri akışı dinleniyor...")
        strategy_config = self.config[f"STRATEGY_{self.active_strategy_name}"]
        timeframe = strategy_config['timeframe']

        self.kline_socket = self.bm.kline_socket(self.active_symbol, interval=timeframe)
        self.user_socket = self.bm.user_socket()

        async with self.kline_socket as k_stream, self.user_socket as u_stream:
            while self.strategy_active:
                try:
                    kline_msg_task = asyncio.create_task(k_stream.recv())
                    user_msg_task = asyncio.create_task(u_stream.recv())

                    done, pending = await asyncio.wait(
                        [kline_msg_task, user_msg_task],
                        return_when=asyncio.FIRST_COMPLETED
                    )

                    for task in pending: 
                        task.cancel()

                    if kline_msg_task in done:
                        await self._process_kline_message(kline_msg_task.result())

                    if user_msg_task in done:
                        await self._process_user_message(user_msg_task.result())

                except Exception as e:
                    self._log(f"STREAM HATASI: {e}")
                    await asyncio.sleep(5)

    async def _process_kline_message(self, msg: Dict[str, Any]):
        if msg.get('e') == 'error':
            self._log(f"KLINE SOCKET HATASI: {msg.get('m')}")
            return
        if msg.get('k', {}).get('x'):
            self._log(f"Yeni mum kapandı: {self.active_symbol}")
            df = self._get_market_data(self.active_symbol, msg['k']['i'])
            if df is None or df.empty: 
                return
            signal, atr_value = self.get_active_strategy_signal(df)
            self._log(f"[{self.active_symbol}] Sinyal: {signal}")
            open_positions = self.get_open_positions()
            if not any(p['symbol'] == self.active_symbol for p in open_positions):
                if signal == 'LONG': 
                    self._open_position('BUY', atr_value)
                elif signal == 'SHORT': 
                    self._open_position('SELL', atr_value)

    async def _process_user_message(self, msg: Dict[str, Any]):
        event_type = msg.get('e')
        if event_type == 'ACCOUNT_UPDATE':
            self._log("Hesap güncellemesi alındı, arayüz güncelleniyor.")
            if self.ui_update_callback: 
                self.ui_update_callback()

        elif event_type == 'ORDER_TRADE_UPDATE':
            order_data = msg.get('o', {})
            order_status = order_data.get('X')

            if order_status in ['FILLED', 'CANCELED', 'EXPIRED']:
                self._log(f"Emir durumu güncellemesi: {order_data.get('s')} - {order_status}")

                if float(order_data.get('rp', 0)) != 0:
                    self._log(f"POZİSYON KAPANDI: {order_data.get('s')} | PNL: {order_data.get('rp')} USDT")
                    trade_to_log = {
                        'symbol': order_data.get('s'),
                        'id': order_data.get('i'),
                        'side': order_data.get('S'),
                        'realizedPnl': order_data.get('rp'),
                        'time': order_data.get('T')
                    }
                    database.add_trade(trade_to_log)

                if self.ui_update_callback: 
                    self.ui_update_callback()

    # --- Pozisyon Açma / Kapama ---
    def _open_position(self, side: str, atr: float):
        try:
            self.set_leverage(self.leverage)
            quantity = self._calculate_quantity(self.active_symbol)
            if quantity <= 0: 
                return
            self._log(f"POZİSYON AÇILIYOR: {side} {quantity} {self.active_symbol}")
            self.client.futures_create_order(
                symbol=self.active_symbol, side=side, type=ORDER_TYPE_MARKET, quantity=quantity
            )
            time.sleep(1)
            self._set_tp_sl(side, atr)
            if self.ui_update_callback: 
                self.ui_update_callback()
        except Exception as e:
            self._log(f"HATA: Pozisyon açılamadı: {e}")

    def _set_tp_sl(self, side: str, atr: float):
        try:
            position = self.get_position_info(self.active_symbol)
            if not position: 
                return
            entry_price = float(position.get('entryPrice', 0))
            if entry_price == 0:
                self._log("UYARI: Giriş fiyatı alınamadı, TP/SL ayarlanamıyor.")
                return

            rm_mode = self.config['TRADING']['risk_management_mode']
            strategy_config = self.config[f"STRATEGY_{self.active_strategy_name}"]
            tp_price, sl_price = None, None

            sl_multiplier = float(strategy_config['atr_multiplier_sl'])
            tp_multiplier = float(strategy_config.get('atr_multiplier_tp', sl_multiplier * 2))

            if side == 'BUY':
                close_side = 'SELL'
                sl_price = entry_price - (atr * sl_multiplier)
                tp_price = entry_price + (atr * tp_multiplier)
            else:
                close_side = 'BUY'
                sl_price = entry_price + (atr * sl_multiplier)
                tp_price = entry_price - (atr * tp_multiplier)

            batch_orders = []
            if tp_price: 
                batch_orders.append({'symbol': self.active_symbol, 'side': close_side, 'type': 'TAKE_PROFIT_MARKET', 'stopPrice': f"{tp_price:.5f}", 'closePosition': True})
            if sl_price: 
                batch_orders.append({'symbol': self.active_symbol, 'side': close_side, 'type': 'STOP_MARKET', 'stopPrice': f"{sl_price:.5f}", 'closePosition': True})
            if batch_orders:
                self.client.futures_create_batch_order(batchOrders=batch_orders)
                self._log(f"✅ TP ({tp_price:.4f}) ve SL ({sl_price:.4f}) emirleri ayarlandı.")
        except Exception as e:
            self._log(f"HATA: TP/SL ayarlanamadı: {e}")

    def _close_position_and_log(self, reason: str):
        try:
            position = self.get_position_info(self.active_symbol)
            if not position:
                self._log("Kapatılacak pozisyon bulunamadı.")
                return
            pos_amount = float(position.get('positionAmt', 0))
            if pos_amount == 0: 
                return
            self.client.futures_cancel_all_open_orders(symbol=self.active_symbol)
            side = 'SELL' if pos_amount > 0 else 'BUY'
            self.client.futures_create_order(
                symbol=self.active_symbol, side=side, type=ORDER_TYPE_MARKET, quantity=abs(pos_amount)
            )
            self._log(f"POZİSYON KAPATMA EMRİ GÖNDERİLDİ ({reason}).")
        except Exception as e:
            self._log(f"HATA: Pozisyon kapatılamadı: {e}")

    # --- Manuel Ayarlar ---
    def manual_trade(self, side: str):
        if self.strategy_active:
            self._log("Strateji çalışırken manuel işlem yapılamaz. Lütfen önce durdurun.")
            return
        df = self._get_market_data(self.active_symbol, "1m", 20)
        if df is None: 
            return
        latest_atr = ta.atr(df['high'], df['low'], df['close'], length=14).iloc[-1]
        self._open_position('BUY' if side == 'LONG' else 'SELL', latest_atr if pd.notna(latest_atr) else 0)

    def close_current_position(self, manual: bool = False):
        self._close_position_and_log("Manuel kapatma" if manual else "Stratejik kapatma")

    def update_active_symbol(self, new_symbol: str):
        if self.active_symbol == new_symbol: 
            return
        self.active_symbol = new_symbol
        self._log(f"Aktif sembol {self.active_symbol} olarak değiştirildi.")
        self.config.set('TRADING', 'symbol', self.active_symbol)
        with open('config.ini', 'w') as configfile: 
            self.config.write(configfile)
        if self.strategy_active:
            self._log("Strateji yeni sembolle yeniden başlatılıyor...")
            self.stop_strategy()
            time.sleep(2)
            threading.Thread(target=self.start_strategy, daemon=True).start()
        elif self.status_callback:
            self.status_callback(False, self.active_symbol)

    def set_leverage(self, leverage: int):
        self.leverage = leverage
        self.config.set('TRADING', 'leverage', str(leverage))
        with open('config.ini', 'w') as configfile: 
            self.config.write(configfile)
        self._log(f"✅ Kaldıraç {leverage}x olarak ayarlandı.")

    def set_quantity(self, quantity_usd: float):
        self.quantity_usd = quantity_usd
        self.config.set('TRADING', 'quantity_usd', str(quantity_usd))
        with open('config.ini', 'w') as configfile: 
            self.config.write(configfile)
        self._log(f"✅ İşlem miktarı {quantity_usd} USD olarak ayarlandı.")

    # --- Yardımcı Fonksiyonlar ---
    def get_open_positions(self) -> List[Dict[str, Any]]:
        try:
            return [p for p in self.client.futures_account()['positions'] if float(p.get('positionAmt', 0)) != 0]
        except Exception: 
            return []

    def get_position_info(self, symbol: str) -> Optional[Dict[str, Any]]:
        try:
            return next((p for p in self.client.futures_position_information() if p.get('symbol') == symbol), None)
        except Exception: 
            return None

    def get_current_position_data(self) -> Optional[dict]:
        try:
            position = self.get_position_info(self.active_symbol)
            if not position or float(position.get('positionAmt', 0)) == 0: 
                return None
            tp_sl_orders = self.client.futures_get_open_orders(symbol=self.active_symbol)
            tp_order = next((o for o in tp_sl_orders if o['origType'] == 'TAKE_PROFIT_MARKET'), None)
            sl_order = next((o for o in tp_sl_orders if o['origType'] == 'STOP_MARKET'), None)

            pnl = float(position.get('unrealizedProfit', 0))
            entry_price = float(position.get('entryPrice', 0))
            mark_price = float(position.get('markPrice', '0'))
            leverage = int(position.get('leverage', 1))
            position_amt = float(position.get('positionAmt', 0))
            initial_margin = float(position.get('initialMargin', 0))
            if initial_margin == 0 and leverage > 0:
                initial_margin = (abs(position_amt) * entry_price) / leverage
            roi = (pnl / (initial_margin + 1e-9)) * 100

            return {
                "symbol": position.get('symbol'),
                "quantity": position_amt,
                "entry_price": f"{entry_price:.5f}",
                "mark_price": f"{mark_price:.5f}",
                "pnl_usdt": f"{pnl:.2f}",
                "roi_percent": f"{roi:.2f}",
                "sl_price": sl_order['stopPrice'] if sl_order else "N/A",
                "tp_price": tp_order['stopPrice'] if tp_order else "N/A",
                "leverage": leverage
            }
        except Exception as e:
            self._log(f"Pozisyon verisi alınırken hata: {e}")
            return None

    def get_stats_data(self) -> Dict[str, Any]:
        return database.calculate_stats()

    def get_all_trades_data(self) -> List[tuple]:
        return database.get_all_trades()

    def get_all_usdt_symbols(self) -> List[str]:
        try:
            info = self.client.futures_exchange_info()
            return sorted([s['symbol'] for s in info['symbols'] if s['symbol'].endswith('USDT') and 'BUSD' not in s['symbol']])
        except Exception as e:
            self._log(f"API HATASI: Sembol listesi çekilemedi: {e}")
            return []

    def get_active_strategy_signal(self, df: pd.DataFrame) -> tuple:
        if self.active_strategy_name == 'Scalper':
            return strategy_scalper.get_signal(df, self.config['STRATEGY_Scalper'])
        else:
            return strategy_kadir_v2.get_signal(df, self.config['STRATEGY_KadirV2'])

    def _get_market_data(self, symbol: str, timeframe: str, limit: int = 200) -> Optional[pd.DataFrame]:
        try:
            klines = self.client.futures_klines(symbol=symbol, interval=timeframe, limit=limit)
            df = pd.DataFrame(klines, columns=[
                'timestamp', 'open', 'high', 'low', 'close', 'volume',
                'close_time', 'quote_asset_volume', 'number_of_trades',
                'taker_buy_base_asset_volume', 'taker_buy_quote_asset_volume', 'ignore'])
            df[['open', 'high', 'low', 'close', 'volume']] = df[['open', 'high', 'low', 'close', 'volume']].apply(pd.to_numeric, errors='coerce')
            return df
        except Exception as e:
            self._log(f"HATA: Piyasa verileri çekilemedi ({symbol}): {e}")
            return None

    def _calculate_quantity(self, symbol: str) -> float:
        try:
            ticker = self.client.futures_ticker(symbol=symbol)
            price = float(ticker['lastPrice'])
            if price <= 0: 
                return 0.0
            return round(self.quantity_usd / price, 4)
        except Exception as e:
            self._log(f"HATA: Miktar hesaplanamadı: {e}")
            return 0.0

    def _load_config(self) -> configparser.ConfigParser:
        parser = configparser.ConfigParser()
        parser.read('config.ini', encoding='utf-8')
        return parser

    def _log_and_raise(self, message: str):
        self._log(message)
        raise ValueError(message)
