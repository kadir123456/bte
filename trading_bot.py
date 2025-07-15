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
    Binance Futures için WebSocket ile canlı veri alan,
    pozisyon açan, TP-SL ayarlayan, pozisyon kapatan tam fonksiyonel bot.
    """

    def __init__(self, log_callback: Optional[Callable] = None, 
                 ui_update_callback: Optional[Callable] = None,
                 status_callback: Optional[Callable] = None):
        self.log_callback = log_callback
        self.ui_update_callback = ui_update_callback
        self.status_callback = status_callback

        self._log = self.log_callback if self.log_callback else print

        self.config = self._load_config()

        api_key = os.environ.get('BINANCE_API_KEY')
        api_secret = os.environ.get('BINANCE_API_SECRET')

        if not api_key or not api_secret:
            self._log("HATA: Binance API anahtarları ortam değişkenlerinde bulunamadı!")
            raise RuntimeError("API key ve secret gereklidir.")

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

        self._log("TradingBot objesi başarıyla oluşturuldu.")

    def start_strategy(self):
        if self.strategy_active:
            self._log("Strateji zaten çalışıyor.")
            return
        self.strategy_active = True
        self._log(f"Strateji ({self.active_symbol}) başlatılıyor...")
        if self.status_callback:
            self.status_callback(True, self.active_symbol)
        self.loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self.loop)
        self.loop.run_until_complete(self._listen_to_streams())
        self._log("Strateji durduruldu.")

    def stop_strategy(self):
        if not self.strategy_active:
            self._log("Strateji zaten durdurulmuş.")
            return
        self.strategy_active = False
        if self.loop and self.loop.is_running():
            self.loop.call_soon_threadsafe(self.loop.stop)
        self._log("Strateji durduruldu.")
        if self.status_callback:
            self.status_callback(False, self.active_symbol)

    async def _listen_to_streams(self):
        self._log(f"Veri akışı dinleniyor: {self.active_symbol}")
        strat_conf = self.config[f"STRATEGY_{self.active_strategy_name}"]
        timeframe = strat_conf['timeframe']

        self.kline_socket = self.bm.kline_socket(self.active_symbol, interval=timeframe)
        self.user_socket = self.bm.user_socket()

        async with self.kline_socket as k_stream, self.user_socket as u_stream:
            while self.strategy_active:
                try:
                    k_task = asyncio.create_task(k_stream.recv())
                    u_task = asyncio.create_task(u_stream.recv())

                    done, pending = await asyncio.wait(
                        [k_task, u_task], return_when=asyncio.FIRST_COMPLETED
                    )

                    for task in pending: task.cancel()

                    if k_task in done:
                        await self._handle_kline_msg(k_task.result())

                    if u_task in done:
                        await self._handle_user_msg(u_task.result())

                except Exception as e:
                    self._log(f"STREAM HATASI: {e}")
                    await asyncio.sleep(5)

    async def _handle_kline_msg(self, msg: Dict[str, Any]):
        if msg.get('e') == 'error':
            self._log(f"KLINE HATASI: {msg.get('m')}")
            return
        if msg.get('k', {}).get('x'):  # Mum kapanmış mı kontrolü
            self._log(f"Yeni mum kapandı: {self.active_symbol}")
            df = self._get_market_data(self.active_symbol, msg['k']['i'])
            if df is None or df.empty: return
            signal, atr = self.get_active_strategy_signal(df)
            self._log(f"[{self.active_symbol}] Sinyal: {signal}")
            open_positions = self.get_open_positions()
            if not any(p['symbol'] == self.active_symbol for p in open_positions):
                if signal == 'LONG': self._open_position('BUY', atr)
                elif signal == 'SHORT': self._open_position('SELL', atr)

    async def _handle_user_msg(self, msg: Dict[str, Any]):
        event = msg.get('e')
        if event == 'ACCOUNT_UPDATE':
            self._log("Hesap güncellemesi alındı, arayüz güncelleniyor.")
            if self.ui_update_callback: self.ui_update_callback()
        elif event == 'ORDER_TRADE_UPDATE':
            order = msg.get('o', {})
            status = order.get('X')
            if status in ['FILLED', 'CANCELED', 'EXPIRED']:
                self._log(f"Emir durumu güncellemesi: {order.get('s')} - {status}")
                if float(order.get('rp', 0)) != 0:
                    self._log(f"Pozisyon kapandı: {order.get('s')} | PNL: {order.get('rp')} USDT")
                    database.add_trade({
                        'symbol': order.get('s'),
                        'id': order.get('i'),
                        'side': order.get('S'),
                        'realizedPnl': order.get('rp'),
                        'time': order.get('T')
                    })
                if self.ui_update_callback: self.ui_update_callback()

    def _open_position(self, side: str, atr: float):
        try:
            self.set_leverage(self.leverage)
            qty = self._calculate_quantity(self.active_symbol)
            if qty <= 0:
                self._log("HATA: Miktar sıfır veya geçersiz, pozisyon açılmadı.")
                return
            self._log(f"Pozisyon açılıyor: {side} {qty} {self.active_symbol}")

            order = self.client.futures_create_order(
                symbol=self.active_symbol,
                side=side,
                type=ORDER_TYPE_MARKET,
                quantity=qty
            )
            self._log(f"Pozisyon açma emri gönderildi: {order}")

            time.sleep(1)  # TP/SL emirleri için kısa bekleme
            self._set_tp_sl(side, atr)

            if self.ui_update_callback:
                self.ui_update_callback()

        except Exception as e:
            self._log(f"Pozisyon açılamadı: {e}")

    def _set_tp_sl(self, side: str, atr: float):
        try:
            position = self.get_position_info(self.active_symbol)
            if not position:
                self._log("UYARI: Pozisyon bilgisi alınamadı, TP/SL ayarlanamadı.")
                return

            entry_price = float(position.get('entryPrice', 0))
            if entry_price == 0:
                self._log("UYARI: Giriş fiyatı alınamadı, TP/SL ayarlanamıyor.")
                return

            strat_conf = self.config[f"STRATEGY_{self.active_strategy_name}"]
            sl_mult = float(strat_conf.get('atr_multiplier_sl', 1.0))
            tp_mult = float(strat_conf.get('atr_multiplier_tp', sl_mult * 2))

            if side == 'BUY':
                tp_price = entry_price + atr * tp_mult
                sl_price = entry_price - atr * sl_mult
                close_side = 'SELL'
            else:
                tp_price = entry_price - atr * tp_mult
                sl_price = entry_price + atr * sl_mult
                close_side = 'BUY'

            batch_orders = [
                {
                    "symbol": self.active_symbol,
                    "side": close_side,
                    "type": "TAKE_PROFIT_MARKET",
                    "stopPrice": f"{tp_price:.5f}",
                    "closePosition": True,
                    "timeInForce": "GTC"
                },
                {
                    "symbol": self.active_symbol,
                    "side": close_side,
                    "type": "STOP_MARKET",
                    "stopPrice": f"{sl_price:.5f}",
                    "closePosition": True,
                    "timeInForce": "GTC"
                }
            ]

            result = self.client.futures_create_batch_order(batchOrders=batch_orders)
            self._log(f"TP ve SL emirleri başarıyla ayarlandı: {result}")

        except Exception as e:
            self._log(f"TP/SL ayarlanırken hata: {e}")

    def _close_position_and_log(self, reason: str):
        try:
            position = self.get_position_info(self.active_symbol)
            if not position:
                self._log("Pozisyon bulunamadı, kapatma işlemi yapılmadı.")
                return

            pos_amt = float(position.get('positionAmt', 0))
            if pos_amt == 0:
                self._log("Pozisyon miktarı sıfır, kapatma yapılmadı.")
                return

            # Açık emirleri iptal et
            self.client.futures_cancel_all_open_orders(symbol=self.active_symbol)
            self._log("Açık tüm emirler iptal edildi.")

            close_side = 'SELL' if pos_amt > 0 else 'BUY'
            qty = abs(pos_amt)

            order = self.client.futures_create_order(
                symbol=self.active_symbol,
                side=close_side,
                type=ORDER_TYPE_MARKET,
                quantity=qty
            )

            self._log(f"Pozisyon kapatma emri gönderildi ({reason}): {order}")

        except Exception as e:
            self._log(f"Pozisyon kapatılırken hata: {e}")

    # Diğer yardımcı fonksiyonlar:

    def manual_trade(self, side: str):
        if self.strategy_active:
            self._log("Strateji çalışıyorken manuel işlem yapılamaz.")
            return
        df = self._get_market_data(self.active_symbol, "1m", 20)
        if df is None: return
        latest_atr = ta.atr(df['high'], df['low'], df['close'], length=14).iloc[-1]
        self._open_position('BUY' if side == 'LONG' else 'SELL', latest_atr if pd.notna(latest_atr) else 0)

    def close_current_position(self, manual: bool = False):
        self._close_position_and_log("Manuel kapatma" if manual else "Stratejik kapatma")

    def update_active_symbol(self, new_symbol: str):
        if self.active_symbol == new_symbol: return
        self.active_symbol = new_symbol
        self._log(f"Aktif sembol {self.active_symbol} olarak değiştirildi.")
        self.config.set('TRADING', 'symbol', self.active_symbol)
        with open('config.ini', 'w') as f: self.config.write(f)
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
        with open('config.ini', 'w') as f: self.config.write(f)
        self._log(f"Kaldıraç {leverage}x olarak ayarlandı.")

    def set_quantity(self, quantity_usd: float):
        self.quantity_usd = quantity_usd
        self.config.set('TRADING', 'quantity_usd', str(quantity_usd))
        with open('config.ini', 'w') as f: self.config.write(f)
        self._log(f"İşlem miktarı {quantity_usd} USD olarak ayarlandı.")

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
            self._log(f"Piyasa verisi alınamadı ({symbol}): {e}")
            return None

    def _calculate_quantity(self, symbol: str) -> float:
        try:
            ticker = self.client.futures_ticker(symbol=symbol)
            price = float(ticker['lastPrice'])
            if price <= 0: return 0.0
            qty = round(self.quantity_usd / price, 3)
            return qty
        except Exception as e:
            self._log(f"Miktar hesaplanamadı: {e}")
            return 0.0

    def _load_config(self) -> configparser.ConfigParser:
        parser = configparser.ConfigParser()
        parser.read('config.ini', encoding='utf-8')
        return parser

    def _log(self, message: str):
        if self.log_callback:
            self.log_callback(message)
        else:
            print(message)
