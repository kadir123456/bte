import os
import configparser
import time
import pandas as pd
from binance.client import Client
from binance.enums import *
import strategy as strategy_kadir_v2
import strategy_scalper
import database
import screener
from typing import Callable, Optional, List, Dict
from requests.exceptions import RequestException
import threading
import math

class TradingBot:
    """
    Web arayüzü ile kontrol edilen, çoklu işlem ve gelişmiş risk yönetimi 
    yeteneğine sahip, sunucuda 7/24 çalışmak üzere tasarlanmış V3 ticaret motoru.
    """
    def __init__(self, ui_update_callback: Optional[Callable] = None) -> None:
        self.ui_update_callback = ui_update_callback
        self.config = self._load_config()
        
        # GÜVENLİK: API anahtarlarını ortam değişkenlerinden (sunucudan) oku
        self.api_key = os.environ.get('BINANCE_API_KEY')
        self.api_secret = os.environ.get('BINANCE_API_SECRET')
        
        if not self.api_key or not self.api_secret:
            self._log("HATA: Sunucu ortam değişkenlerinde API anahtarları bulunamadı!")
            raise ValueError("API anahtarları eksik.")
            
        self.is_testnet = 'testnet' in self.config['BINANCE']['api_url']
        self.client = Client(self.api_key, self.api_secret, testnet=self.is_testnet)
        
        # Botun durumunu ve ayarlarını tutan değişkenler
        self.running: bool = True
        self.strategy_active: bool = False
        
        # V3 Ayarları
        self.leverage = self.config['TRADING'].getint('leverage', 10)
        self.margin_type = self.config['TRADING'].get('margin_type', 'ISOLATED')
        self.quantity_per_trade_usd = self.config['TRADING'].getfloat('quantity_per_trade_usd', 20)
        self.max_concurrent_trades = self.config['TRADING'].getint('max_concurrent_trades', 3)
        self.tradeable_symbols_config = self.config['TRADING'].get('tradeable_symbols', 'XRPUSDT')
        
        # Risk Yönetimi Ayarları
        self.profit_strategy = self.config['RISK_MANAGEMENT'].get('profit_strategy', 'trailing_stop')
        self.fixed_take_profit_pct = self.config['RISK_MANAGEMENT'].getfloat('fixed_take_profit_pct', 2.0) / 100
        self.fixed_stop_loss_pct = self.config['RISK_MANAGEMENT'].getfloat('fixed_stop_loss_pct', 1.0) / 100
        self.trailing_stop_trigger_pct = self.config['RISK_MANAGEMENT'].getfloat('trailing_stop_trigger_pct', 1.5) / 100
        self.trailing_stop_distance_pct = self.config['RISK_MANAGEMENT'].getfloat('trailing_stop_distance_pct', 0.5) / 100

        # Aktif pozisyonları ve iz süren stopları takip etmek için sözlük (dictionary)
        self.open_positions: Dict[str, Dict] = {}
        
        self._log("Bot objesi başarıyla oluşturuldu.")
        # Arka planda anlık veri akışını başlat
        threading.Thread(target=self._stream_position_data, daemon=True).start()

    def _load_config(self) -> configparser.ConfigParser:
        parser = configparser.ConfigParser()
        parser.read('config.ini', encoding='utf-8')
        return parser

    def _log(self, message: str) -> None:
        log_message = f"{time.strftime('%H:%M:%S')} - {message}"
        print(log_message)
        if self.ui_update_callback:
            self.ui_update_callback("log", log_message)

    def get_all_usdt_symbols(self) -> List[str]:
        try:
            info = self.client.futures_exchange_info()
            return sorted([s['symbol'] for s in info['symbols'] if s['symbol'].endswith('USDT') and 'BUSD' not in s['symbol']])
        except Exception as e:
            self._log(f"HATA: Sembol listesi çekilemedi: {e}"); return []

    def get_current_position_data(self) -> List[dict]:
        """Tüm açık pozisyonların anlık verilerini arayüz için hazırlar."""
        try:
            all_positions = self.client.futures_account()['positions']
            open_positions_data = []
            for position in all_positions:
                if float(position['positionAmt']) != 0:
                    pnl = float(position['unrealizedProfit'])
                    roi = (pnl / (float(position['initialMargin']) + 1e-9)) * 100
                    pos_data = {
                        "symbol": position['symbol'], "quantity": position['positionAmt'],
                        "entry_price": position['entryPrice'], "mark_price": position['markPrice'],
                        "pnl_usdt": f"{pnl:.2f}", "roi_percent": f"{roi:.2f}%"
                    }
                    open_positions_data.append(pos_data)
            return open_positions_data
        except Exception:
            return []

    def _get_market_data(self, symbol: str, timeframe: str) -> Optional[pd.DataFrame]:
        try:
            klines = self.client.futures_klines(symbol=symbol, interval=timeframe, limit=200)
            df = pd.DataFrame(klines, columns=['timestamp', 'open', 'high', 'low', 'close', 'volume', 'close_time', 'quote_asset_volume', 'number_of_trades', 'taker_buy_base_asset_volume', 'taker_buy_quote_asset_volume', 'ignore'])
            numeric_cols = ['open', 'high', 'low', 'close', 'volume']
            df[numeric_cols] = df[numeric_cols].apply(pd.to_numeric, errors='coerce')
            return df
        except Exception as e:
            self._log(f"HATA: Piyasa verileri çekilemedi ({symbol}): {e}"); return None

    def calculate_quantity(self, symbol: str) -> Optional[float]:
        trade_usd = self.quantity_per_trade_usd
        try:
            price_info = self.client.futures_mark_price(symbol=symbol)
            current_price = float(price_info['markPrice'])
            
            if trade_usd < 5.1:
                 self._log(f"UYARI: İşlem büyüklüğü ({trade_usd:.2f}$) çok düşük. Minimum ~5 USDT olmalıdır."); return None

            quantity = trade_usd / current_price
            
            info = self.client.futures_exchange_info()
            symbol_info = next(item for item in info['symbols'] if item['symbol'] == symbol)
            
            min_qty, step_size = 0.0, 0.1
            for f in symbol_info['filters']:
                if f['filterType'] == 'LOT_SIZE':
                    min_qty = float(f['minQty']); step_size = float(f['stepSize']); break
            
            if quantity < min_qty:
                self._log(f"UYARI: Hesaplanan miktar ({quantity:.4f}) {symbol} için minimum ({min_qty})'dan daha az."); return None

            precision = int(round(-math.log(step_size, 10), 0)) if step_size > 0 else 0
            return round(quantity, precision)
        except Exception as e:
            self._log(f"HATA: Miktar hesaplanamadı ({symbol}): {e}"); return None
            
    def open_position(self, symbol: str, signal: str, atr: float, quantity: float) -> None:
        side = SIDE_BUY if signal == 'LONG' else SIDE_SELL
        try:
            self.client.futures_change_leverage(symbol=symbol, leverage=self.leverage)
            self.client.futures_change_margin_type(symbol=symbol, marginType=self.margin_type)
            self._log(f"[{symbol}] Pozisyon açılıyor: {signal}, {self.margin_type}, {self.leverage}x")
            
            self.client.futures_create_order(symbol=symbol, side=side, type=ORDER_TYPE_MARKET, quantity=quantity)
            
            time.sleep(1)
            position = next((p for p in self.client.futures_account()['positions'] if p['symbol'] == symbol), None)
            entry_price = float(position['entryPrice']) if position else 0
            if entry_price == 0: self._log("HATA: Giriş fiyatı alınamadı, TP/SL ayarlanamıyor."); return

            self._log(f"[{symbol}] POZİSYON AÇILDI - Giriş: {entry_price}")
            self.open_positions[symbol] = {'entry_price': entry_price, 'quantity': quantity}

            if self.profit_strategy == 'fixed_roi':
                if signal == 'LONG':
                    tp_price = entry_price * (1 + self.fixed_take_profit_pct)
                    sl_price = entry_price * (1 - self.fixed_stop_loss_pct)
                else:
                    tp_price = entry_price * (1 - self.fixed_take_profit_pct)
                    sl_price = entry_price * (1 + self.fixed_stop_loss_pct)
                self._log(f"[{symbol}] Sabit Hedefler: TP: {tp_price:.4f}, SL: {sl_price:.4f}")
            else: # ATR Modu
                strategy_config = self.config["STRATEGY_KadirV2"] # Varsayılan olarak bunu kullanıyoruz
                atr_multiplier_sl = float(strategy_config['atr_multiplier_sl'])
                sl_distance = atr * atr_multiplier_sl
                if signal == 'LONG':
                    sl_price = entry_price - sl_distance
                else:
                    sl_price = entry_price + sl_distance
                tp_price = None 
                self._log(f"[{symbol}] Dinamik Hedefler: SL: {sl_price:.4f} (TP iz süren stop ile yönetilecek)")
            
            info = self.client.futures_exchange_info()
            symbol_info = next(item for item in info['symbols'] if item['symbol'] == symbol)
            price_precision = int(symbol_info['pricePrecision'])
            sl_price = round(sl_price, price_precision)

            close_side = SIDE_SELL if signal == 'LONG' else SIDE_BUY
            if tp_price:
                tp_price = round(tp_price, price_precision)
                self.client.futures_create_order(symbol=symbol, side=close_side, type=FUTURE_ORDER_TYPE_TAKE_PROFIT_MARKET, stopPrice=tp_price, closePosition=True)
            
            self.client.futures_create_order(symbol=symbol, side=close_side, type=FUTURE_ORDER_TYPE_STOP_MARKET, stopPrice=sl_price, closePosition=True)
            self._log(f"[{symbol}] Koruma emirleri başarıyla yerleştirildi.")
        except Exception as e:
            self._log(f"HATA: Pozisyon açma hatası ({symbol}) - {e}")

    def check_and_update_pnl(self, symbol: str, entry_price: float, quantity: float):
        try:
            closing_trade = next((t for t in reversed(self.client.futures_account_trades(symbol=symbol, limit=5)) if float(t['realizedPnl']) != 0), None)
            if closing_trade:
                closing_trade['symbol'] = symbol
                closing_trade['entryPrice'] = entry_price
                closing_trade['qty'] = quantity
                database.add_trade(closing_trade)
                pnl = float(closing_trade['realizedPnl'])
                self._log(f"KAPALI İŞLEM: {symbol} - {'✅ KÂR' if pnl > 0 else '❌ ZARAR'}: {pnl:.2f} USDT.")
                if self.ui_update_callback:
                    self.ui_update_callback("history_update", None)
        except Exception as e:
            self._log(f"HATA: PNL kontrol edilemedi ({symbol}): {e}")

    def close_all_positions(self):
        self._log("!!! Tüm pozisyonları kapatma talebi alındı !!!")
        try:
            positions = self.client.futures_position_information()
            for pos in positions:
                if float(pos['positionAmt']) != 0:
                    symbol = pos['symbol']
                    pos_amount = float(pos['positionAmt'])
                    side = SIDE_SELL if pos_amount > 0 else SIDE_BUY
                    self.client.futures_cancel_all_open_orders(symbol=symbol)
                    self.client.futures_create_order(symbol=symbol, side=side, type=ORDER_TYPE_MARKET, quantity=abs(pos_amount))
                    self._log(f"✅ {symbol} pozisyonu kapatıldı.")
        except Exception as e:
            self._log(f"HATA: Tüm pozisyonlar kapatılırken hata oluştu: {e}")

    def update_settings(self, settings: dict):
        try:
            self.leverage = int(settings.get('leverage', self.leverage))
            self.quantity_per_trade_usd = float(settings.get('quantity_per_trade_usd', self.quantity_per_trade_usd))
            self.max_concurrent_trades = int(settings.get('max_concurrent_trades', self.max_concurrent_trades))
            self.margin_type = settings.get('margin_type', self.margin_type).upper()
            self.profit_strategy = settings.get('profit_strategy', self.profit_strategy)
            self.fixed_take_profit_pct = float(settings.get('fixed_take_profit_pct', self.fixed_roi_tp * 100)) / 100
            self.fixed_stop_loss_pct = float(settings.get('fixed_stop_loss_pct', (self.fixed_roi_tp / 2) * 100)) / 100
            self.tradeable_symbols_config = settings.get('tradeable_symbols', self.tradeable_symbols_config)
            self._log("✅ Ayarlar başarıyla güncellendi.")
        except Exception as e:
            self._log(f"❌ HATA: Ayarlar güncellenirken hata oluştu: {e}")

    def _handle_trailing_stop(self, position: dict):
        symbol = position['symbol']
        pos_data = self.open_positions.get(symbol)
        if not pos_data: return

        current_pnl_roi = (float(position['unrealizedProfit']) / (float(position['initialMargin']) + 1e-9))
        current_mark_price = float(position['markPrice'])
        side = "LONG" if float(position['positionAmt']) > 0 else "SHORT"

        if not pos_data.get('trailing_active') and current_pnl_roi >= self.trailing_stop_trigger_pct:
            self.client.futures_cancel_all_open_orders(symbol=symbol)
            new_sl_price = 0
            if side == "LONG":
                new_sl_price = float(position['entryPrice']) * 1.001
            else:
                new_sl_price = float(position['entryPrice']) * 0.999
            
            info = self.client.futures_exchange_info()
            symbol_info = next(item for item in info['symbols'] if item['symbol'] == symbol)
            price_precision = int(symbol_info['pricePrecision'])
            new_sl_price = round(new_sl_price, price_precision)
            
            self.client.futures_create_order(symbol=symbol, side=SIDE_SELL if side == "LONG" else SIDE_BUY, type=FUTURE_ORDER_TYPE_STOP_MARKET, stopPrice=new_sl_price, closePosition=True)
            self.open_positions[symbol]['trailing_active'] = True
            self.open_positions[symbol]['trailing_sl'] = new_sl_price
            self._log(f"[{symbol}] ✅ KÂRA GEÇİLDİ! İz Süren Stop {new_sl_price} fiyatında aktif edildi.")

        elif pos_data.get('trailing_active'):
            new_trailing_sl = 0
            if side == "LONG" and current_mark_price > pos_data.get('highest_price', 0):
                new_trailing_sl = current_mark_price * (1 - self.trailing_stop_distance_pct)
                self.open_positions[symbol]['highest_price'] = current_mark_price
            elif side == "SHORT" and current_mark_price < pos_data.get('lowest_price', float('inf')):
                new_trailing_sl = current_mark_price * (1 + self.trailing_stop_distance_pct)
                self.open_positions[symbol]['lowest_price'] = current_mark_price

            if new_trailing_sl != 0 and ( (side == "LONG" and new_trailing_sl > pos_data['trailing_sl']) or (side == "SHORT" and new_trailing_sl < pos_data['trailing_sl']) ):
                self.client.futures_cancel_all_open_orders(symbol=symbol)
                self.client.futures_create_order(symbol=symbol, side=SIDE_SELL if side == "LONG" else SIDE_BUY, type=FUTURE_ORDER_TYPE_STOP_MARKET, stopPrice=new_trailing_sl, closePosition=True)
                self.open_positions[symbol]['trailing_sl'] = new_trailing_sl
                self._log(f"[{symbol}] 📈 KÂR ARTIYOR! İz Süren Stop {new_trailing_sl} seviyesine güncellendi.")

    def run_strategy(self):
        self.strategy_active = True
        self._log(f"Otomatik strateji motoru V3 çalıştırıldı.")
        
        while self.strategy_active:
            try:
                current_positions = {p['symbol']: p for p in self.client.futures_account()['positions'] if float(p['positionAmt']) != 0}
                
                closed_symbols = set(self.open_positions.keys()) - set(current_positions.keys())
                for symbol in closed_symbols:
                    self._log(f"Pozisyon kapandı: {symbol}")
                    pos_info = self.open_positions.pop(symbol, {})
                    self.check_and_update_pnl(symbol, pos_info.get('entry_price', 0), pos_info.get('quantity', 0))
                
                self.open_positions = current_positions

                if self.profit_strategy == 'trailing_stop':
                    for symbol, position in self.open_positions.items():
                        if symbol not in self.open_positions: self.open_positions[symbol] = {}
                        self._handle_trailing_stop(position)

                if len(self.open_positions) >= self.max_concurrent_trades:
                    time.sleep(20); continue

                if self.tradeable_symbols_config.upper() == 'AUTO':
                    symbols_to_scan = screener.find_potential_coins(self.api_key, self.api_secret, self.is_testnet, self.config['STRATEGY_KadirV2'])
                else:
                    symbols_to_scan = [s.strip() for s in self.tradeable_symbols_config.split(',')]

                for symbol in symbols_to_scan:
                    if not self.strategy_active: break
                    if symbol in self.open_positions: continue

                    strategy_config = self.config['STRATEGY_KadirV2']
                    timeframe = strategy_config['timeframe']
                    df = self._get_market_data(symbol, timeframe)
                    if df is None or df.empty: continue

                    signal, atr_value = strategy_kadir_v2.get_signal(df, strategy_config)
                    if signal in ['LONG', 'SHORT']:
                        self._log(f"🔥 {symbol} için {signal} sinyali bulundu! İşlem açılıyor...")
                        quantity = self.calculate_quantity(symbol)
                        if quantity:
                            self.open_position(symbol, signal, atr_value, quantity)
                            time.sleep(5)
                            if len(self.open_positions) >= self.max_concurrent_trades: break
                
                time.sleep(60)
            except Exception as e:
                self._log(f"ANA DÖNGÜ HATASI: {type(e).__name__} - {e}")
                time.sleep(60)
        
        self._log("Otomatik strateji motoru durduruldu.")
    
    def start_strategy_loop(self):
        if not self.strategy_active:
            self.strategy_active = True
            threading.Thread(target=self.run_strategy, daemon=True).start()

    def stop_strategy_loop(self):
        self.strategy_active = False

    def stop_all(self):
        self.running = False
        self.strategy_active = False
