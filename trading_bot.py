# trading_bot.py (En Basit, Tek Sembol Çalışan Versiyon)

import os
import configparser
import time
import pandas as pd
from binance.client import Client
from binance.enums import *
from binance.exceptions import BinanceAPIException
import strategy as strategy_kadir_v2
import strategy_scalper
import database
from typing import Callable, Optional, List
from requests.exceptions import RequestException
import threading
import pandas_ta as ta

class TradingBot:
    def __init__(self, log_callback: Optional[Callable] = None) -> None:
        self.log_callback = log_callback
        self.config = self._load_config()
        
        api_key = os.environ.get('BINANCE_API_KEY')
        api_secret = os.environ.get('BINANCE_API_SECRET')
        
        if not api_key or not api_secret:
            self._log("HATA: Sunucu ortam değişkenlerinde BINANCE_API_KEY ve BINANCE_API_SECRET bulunamadı!")
            raise ValueError("API anahtarları eksik.")
            
        self.is_testnet = self.config.getboolean('BINANCE', 'testnet', fallback=False)
        self.client = Client(api_key, api_secret, testnet=self.is_testnet)
        
        self.strategy_active: bool = False
        self.position_lock = threading.Lock()
        self.active_symbol = self.config['TRADING']['symbol']
        self.active_strategy_name = self.config['TRADING']['active_strategy']
        self.quantity_usd = float(self.config['TRADING']['quantity_usd'])
        
        self._log("Basit Bot objesi başarıyla oluşturuldu.")
        self._log(f"Testnet Modu: {'Aktif' if self.is_testnet else 'Pasif'}")

    def _load_config(self) -> configparser.ConfigParser:
        parser = configparser.ConfigParser()
        parser.read('config.ini', encoding='utf-8')
        return parser

    def _log(self, message: str) -> None:
        log_message = f"{time.strftime('%Y-%m-%d %H:%M:%S')} - {message}"
        print(log_message)
        if self.log_callback:
            self.log_callback(log_message)

    def get_all_usdt_symbols(self) -> List[str]:
        try:
            info = self.client.futures_exchange_info()
            symbols = [s['symbol'] for s in info['symbols'] if s['symbol'].endswith('USDT') and 'BUSD' not in s['symbol']]
            return sorted(symbols)
        except Exception as e:
            self._log(f"API HATASI: Sembol listesi çekilemedi: {e}")
            return []

    def update_active_symbol(self, new_symbol: str):
        if self.strategy_active:
            self._log("UYARI: Strateji çalışırken sembol değiştirilemez. Lütfen önce stratejiyi durdurun.")
            return
        self.active_symbol = new_symbol.upper()
        self.config.set('TRADING', 'symbol', self.active_symbol)
        with open('config.ini', 'w') as configfile:
            self.config.write(configfile)
        self._log(f"✅ Aktif sembol {self.active_symbol} olarak ayarlandı ve kaydedildi.")

    def get_current_position_data(self) -> Optional[dict]:
        try:
            all_positions = self.client.futures_account()['positions']
            position = next((p for p in all_positions if p.get('symbol') == self.active_symbol and float(p.get('positionAmt', 0)) != 0), None)
            
            if not position: return None

            tp_sl_orders = self.client.futures_get_open_orders(symbol=position.get('symbol'))
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
                "symbol": position.get('symbol', 'N/A'), "quantity": position_amt, "entry_price": f"{entry_price:.5f}",
                "mark_price": f"{mark_price:.5f}", "pnl_usdt": f"{pnl:.2f}", "roi_percent": f"{roi:.2f}",
                "sl_price": sl_order['stopPrice'] if sl_order else "N/A", "tp_price": tp_order['stopPrice'] if tp_order else "N/A",
                "leverage": leverage
            }
        except Exception as e:
            self._log(f"Pozisyon verisi alınırken hata: {e}")
            return None

    def run_strategy(self):
        self.strategy_active = True
        self._log(f"Otomatik strateji ({self.active_strategy_name}) çalıştırıldı. Sembol: {self.active_symbol}")
        
        while self.strategy_active:
            try:
                strategy_config = self.config[f"STRATEGY_{self.active_strategy_name}"]
                timeframe = strategy_config['timeframe']
                df = self._get_market_data(self.active_symbol, timeframe)
                
                if df is None or df.empty:
                    time.sleep(15); continue

                signal, atr_value = self.get_active_strategy_signal(df)
                self._log(f"[{self.active_symbol} | {self.active_strategy_name}] Sinyal: {signal}")

                self._manage_position(signal, atr_value)
                
                time.sleep(30)
            except Exception as e:
                self._log(f"ANA DÖNGÜ HATASI: {type(e).__name__} - {e}")
                time.sleep(60)
        
        self._log("Otomatik strateji motoru durduruldu.")

    # ... Diğer tüm yardımcı fonksiyonlar (stop_strategy_loop, _manage_position, _open_position vb.) önceki stabil versiyondaki gibi kalabilir.
    # Bu fonksiyonları da ekleyerek kodu tamamlıyorum.
    
    def stop_strategy_loop(self):
        self._log("Strateji durduruluyor...")
        self.strategy_active = False

    def get_active_strategy_signal(self, df: pd.DataFrame) -> tuple:
        # ... (Bu fonksiyon aynı)
        strategy_config_name = f"STRATEGY_{self.active_strategy_name}"
        if self.active_strategy_name == 'Scalper':
            return strategy_scalper.get_signal(df, self.config[strategy_config_name])
        else:
            return strategy_kadir_v2.get_signal(df, self.config[strategy_config_name])

    def _get_market_data(self, symbol: str, timeframe: str, limit: int = 200) -> Optional[pd.DataFrame]:
        # ... (Bu fonksiyon aynı)
        try:
            klines = self.client.futures_klines(symbol=symbol, interval=timeframe, limit=limit)
            df = pd.DataFrame(klines, columns=['timestamp', 'open', 'high', 'low', 'close', 'volume', 'close_time', 'quote_asset_volume', 'number_of_trades', 'taker_buy_base_asset_volume', 'taker_buy_quote_asset_volume', 'ignore'])
            numeric_cols = ['open', 'high', 'low', 'close', 'volume']
            df[numeric_cols] = df[numeric_cols].apply(pd.to_numeric, errors='coerce')
            return df
        except Exception as e:
            self._log(f"HATA: Piyasa verileri çekilemedi ({symbol}): {e}"); return None

    def _calculate_quantity(self, symbol: str) -> float:
        # ... (Bu fonksiyon aynı)
        try:
            ticker = self.client.futures_ticker(symbol=symbol)
            price = float(ticker['lastPrice'])
            if price <= 0: return 0.0
            return round(self.quantity_usd / price, 4)
        except Exception as e:
            self._log(f"HATA: Miktar hesaplanamadı: {e}")
            return 0.0

    def _manage_position(self, signal: str, atr_value: float):
        # ... (Bu fonksiyon aynı)
        with self.position_lock:
            try:
                pos_info_list = self.client.futures_position_information(symbol=self.active_symbol)
                if not pos_info_list:
                    self._log(f"UYARI: {self.active_symbol} için pozisyon bilgisi alınamadı.")
                    return
                position = pos_info_list[0]
                pos_amount = float(position.get('positionAmt', 0))
            except Exception as e:
                self._log(f"HATA: Pozisyon bilgisi alınamadı: {e}")
                return

            if signal == 'LONG' and pos_amount == 0: self._open_position('BUY', atr_value)
            elif signal == 'SHORT' and pos_amount == 0: self._open_position('SELL', atr_value)
            elif signal == 'SHORT' and pos_amount > 0:
                self._close_position_and_log("Sinyal değişti, LONG kapatılıyor.")
                self._open_position('SELL', atr_value)
            elif signal == 'LONG' and pos_amount < 0:
                self._close_position_and_log("Sinyal değişti, SHORT kapatılıyor.")
                self._open_position('BUY', atr_value)

    def _open_position(self, side: str, atr: float):
        # ... (Bu fonksiyon aynı)
        quantity = self._calculate_quantity(self.active_symbol)
        if quantity <= 0: return
        try:
            self._log(f"POZİSYON AÇILIYOR: {side} {quantity} {self.active_symbol}")
            self.client.futures_create_order(symbol=self.active_symbol, side=side, type=ORDER_TYPE_MARKET, quantity=quantity)
            time.sleep(1)
            self._set_tp_sl(side, atr)
        except Exception as e:
            self._log(f"HATA: Pozisyon açılamadı: {e}")

    def _set_tp_sl(self, side: str, atr: float):
        # ... (Bu fonksiyon aynı)
        try:
            position_list = self.client.futures_position_information(symbol=self.active_symbol)
            if not position_list: return
            position = position_list[0]
            entry_price = float(position.get('entryPrice', 0))
            if entry_price == 0: return

            # ... (TP/SL hesaplama mantığı aynı)
            rm_mode = self.config['TRADING']['risk_management_mode']
            tp_price, sl_price = None, None
            if side == 'BUY':
                close_side = 'SELL'
                if rm_mode == 'atr':
                    strategy_config = self.config[f'STRATEGY_{self.active_strategy_name}']
                    sl_multiplier = float(strategy_config['atr_multiplier_sl'])
                    tp_multiplier = float(strategy_config.get('atr_multiplier_tp', sl_multiplier * 2))
                    sl_price = entry_price - (atr * sl_multiplier)
                    tp_price = entry_price + (atr * tp_multiplier)
            else: # SELL
                close_side = 'BUY'
                if rm_mode == 'atr':
                    strategy_config = self.config[f'STRATEGY_{self.active_strategy_name}']
                    sl_multiplier = float(strategy_config['atr_multiplier_sl'])
                    tp_multiplier = float(strategy_config.get('atr_multiplier_tp', sl_multiplier * 2))
                    sl_price = entry_price + (atr * sl_multiplier)
                    tp_price = entry_price - (atr * tp_multiplier)
            
            batch_orders = []
            if tp_price: batch_orders.append({'symbol': self.active_symbol, 'side': close_side, 'type': 'TAKE_PROFIT_MARKET', 'stopPrice': f"{tp_price:.5f}", 'closePosition': True})
            if sl_price: batch_orders.append({'symbol': self.active_symbol, 'side': close_side, 'type': 'STOP_MARKET', 'stopPrice': f"{sl_price:.5f}", 'closePosition': True})

            if batch_orders:
                self.client.futures_create_batch_order(batchOrders=batch_orders)
                self._log(f"✅ TP/SL emirleri ayarlandı.")
        except Exception as e:
            self._log(f"HATA: TP/SL ayarlanamadı: {e}")

    def _close_position_and_log(self, reason: str):
        # ... (Bu fonksiyon aynı)
        with self.position_lock:
            try:
                self.client.futures_cancel_all_open_orders(symbol=self.active_symbol)
                position_list = self.client.futures_position_information(symbol=self.active_symbol)
                if not position_list: return
                position = position_list[0]
                pos_amount = float(position.get('positionAmt', 0))
                if pos_amount == 0: return

                side = 'SELL' if pos_amount > 0 else 'BUY'
                self.client.futures_create_order(symbol=self.active_symbol, side=side, type=ORDER_TYPE_MARKET, quantity=abs(pos_amount))
                self._log(f"POZİSYON KAPATILDI ({reason}).")
                time.sleep(2)
                trades = self.client.futures_account_trades(symbol=self.active_symbol, limit=5)
                last_trade = next((t for t in reversed(trades) if float(t.get('realizedPnl', 0)) != 0), None)
                if last_trade:
                    database.add_trade(last_trade)
                    self._log(f"💾 İşlem geçmişe kaydedildi. PNL: {last_trade['realizedPnl']} USDT")
            except Exception as e:
                self._log(f"HATA: Pozisyon kapatılamadı: {e}")

    def set_leverage(self, leverage: int, symbol: str):
        # ... (Bu fonksiyon aynı)
        try:
            self.client.futures_change_leverage(symbol=symbol, leverage=leverage)
            self._log(f"✅ {symbol} için kaldıraç {leverage}x olarak ayarlandı.")
        except BinanceAPIException as e:
            self._log(f"HATA: Kaldıraç ayarlanamadı: {e.message}")

    def set_quantity(self, quantity_usd: float):
        # ... (Bu fonksiyon aynı)
        self.quantity_usd = quantity_usd
        self._log(f"✅ İşlem miktarı {quantity_usd} USD olarak ayarlandı.")

    def manual_trade(self, side: str):
        # ... (Bu fonksiyon aynı)
        if side not in ['LONG', 'SHORT']: return
        self._log(f"MANUEL İŞLEM: {side} sinyali alındı.")
        df = self._get_market_data(self.active_symbol, "1m", 20)
        if df is None: return
        latest_atr = df['high'].sub(df['low']).rolling(14).mean().iloc[-1]
        self._open_position('BUY' if side == 'LONG' else 'SELL', latest_atr if pd.notna(latest_atr) else 0)

    def close_current_position(self, manual: bool = False):
        # ... (Bu fonksiyon aynı)
        self._close_position_and_log("Manuel kapatma" if manual else "Stratejik kapatma")
