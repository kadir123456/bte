# trading_bot.py (Çoklu işlem yapabilen yeni versiyon)

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
import screener # screener'ı artık aktif kullanıyoruz
from typing import Callable, Optional, List
from requests.exceptions import RequestException
import threading
import pandas_ta as ta

class TradingBot:
    def __init__(self, log_callback: Optional[Callable] = None) -> None:
        self.log_callback = log_callback
        self.config = self._load_config()
        
        self.api_key = os.environ.get('BINANCE_API_KEY')
        self.api_secret = os.environ.get('BINANCE_API_SECRET')
        
        if not self.api_key or not self.api_secret:
            self._log("HATA: Sunucu ortam değişkenlerinde BINANCE_API_KEY ve BINANCE_API_SECRET bulunamadı!")
            raise ValueError("API anahtarları eksik.")
            
        self.is_testnet = self.config.getboolean('BINANCE', 'testnet', fallback=False)
        self.client = Client(self.api_key, self.api_secret, testnet=self.is_testnet)
        
        self.strategy_active: bool = False
        self.position_lock = threading.Lock()
        
        self.active_strategy_name = self.config['TRADING']['active_strategy']
        self.quantity_usd = float(self.config['TRADING']['quantity_usd'])
        self.leverage = int(self.config['TRADING']['leverage'])
        self.max_positions = int(self.config['TRADING'].get('max_concurrent_positions', 2)) # Aynı anda açık olacak max pozisyon sayısı
        
        self.target_symbols: List[str] = [] # Artık tek bir sembol yerine listeyi takip edeceğiz

        self._log("Çoklu Tarama Botu objesi başarıyla oluşturuldu.")
        self._log(f"Testnet Modu: {'Aktif' if self.is_testnet else 'Pasif'}")

    def _load_config(self) -> configparser.ConfigParser:
        # ... (Bu fonksiyon aynı kalıyor) ...
        parser = configparser.ConfigParser()
        parser.read('config.ini', encoding='utf-8')
        return parser

    def _log(self, message: str) -> None:
        # ... (Bu fonksiyon aynı kalıyor) ...
        log_message = f"{time.strftime('%Y-%m-%d %H:%M:%S')} - {message}"
        print(log_message)
        if self.log_callback:
            self.log_callback(log_message)

    # --- YENİ ÇOKLU İŞLEM MANTIĞI ---

    def run_strategy(self):
        self.strategy_active = True
        self._log(f"Otomatik Tarama ve İşlem Stratejisi ({self.active_strategy_name}) çalıştırıldı.")
        
        screener_interval_seconds = 3600 # 1 saatte bir yeni coinleri tara
        last_screener_run = 0

        while self.strategy_active:
            try:
                # 1. Belirli aralıklarla en hareketli coinleri tara
                if time.time() - last_screener_run > screener_interval_seconds:
                    self._log("Piyasa taranıyor: En hareketli coinler bulunuyor...")
                    self.target_symbols = screener.find_top_volatile_coins(
                        self.api_key, self.api_secret, self.is_testnet, count=5
                    )
                    if not self.target_symbols:
                        self._log("UYARI: Taramadan coin bulunamadı. 15 dakika sonra tekrar denenecek.")
                        time.sleep(900)
                        continue
                    self._log(f"Takip edilecek yeni coinler: {self.target_symbols}")
                    last_screener_run = time.time()

                # 2. Mevcut açık pozisyon sayısını kontrol et
                open_positions = self.get_open_positions()
                if len(open_positions) >= self.max_positions:
                    self._log(f"Maksimum pozisyon sayısına ulaşıldı ({self.max_positions}). Yeni işlem için bekleniyor.")
                    time.sleep(60)
                    continue

                # 3. Hedef listedeki her bir coini analiz et
                for symbol in self.target_symbols:
                    if not self.strategy_active: break # Strateji durdurulduysa döngüden çık
                    
                    # Eğer bu sembolde zaten açık bir pozisyon varsa, onu atla
                    if any(p['symbol'] == symbol for p in open_positions):
                        continue
                    
                    self._analyze_and_trade_symbol(symbol)
                    time.sleep(5) # API limitlerine takılmamak için her sembol arasında bekle

                self._log("Tarama döngüsü tamamlandı. 60 saniye bekleniyor...")
                time.sleep(60)

            except Exception as e:
                self._log(f"ANA DÖNGÜ HATASI: {type(e).__name__} - {e}")
                time.sleep(60)
        
        self._log("Otomatik strateji motoru durduruldu.")

    def _analyze_and_trade_symbol(self, symbol: str):
        """Tek bir sembol için veri çeker, sinyal üretir ve işlem yapar."""
        try:
            self._log(f"Analiz ediliyor: {symbol}")
            strategy_config = self.config[f'STRATEGY_{self.active_strategy_name}']
            timeframe = strategy_config['timeframe']
            df = self._get_market_data(symbol, timeframe)
            
            if df is None or df.empty: return

            signal, atr_value = self.get_active_strategy_signal(df)
            
            if signal in ['LONG', 'SHORT']:
                self._log(f"✅ SİNYAL BULUNDU: {symbol} - {signal}")
                self.set_leverage(self.leverage, symbol)
                self._open_position(symbol, 'BUY' if signal == 'LONG' else 'SELL', atr_value)
        except Exception as e:
            self._log(f"HATA ({symbol} analizi): {e}")


    def get_open_positions(self) -> List[dict]:
        """Tüm açık pozisyonların listesini döndürür."""
        try:
            all_positions = self.client.futures_account()['positions']
            return [p for p in all_positions if float(p.get('positionAmt', 0)) != 0]
        except Exception as e:
            self._log(f"Açık pozisyonlar alınırken hata: {e}")
            return []

    # _open_position, _set_tp_sl gibi diğer yardımcı fonksiyonlar
    # artık argüman olarak 'symbol' de almalı.
    # Aşağıdaki fonksiyonlar bu mantığa göre güncellenmiştir.

    def _open_position(self, symbol: str, side: str, atr: float):
        # ... (Bu fonksiyonun içi artık symbol parametresini kullanır) ...
        # ... (Önceki versiyondaki gibi ama self.active_symbol yerine symbol kullanılır) ...
        with self.position_lock:
            quantity = self._calculate_quantity(symbol)
            if quantity <= 0: return

            try:
                self._log(f"POZİSYON AÇILIYOR: {side} {quantity} {symbol}")
                self.client.futures_create_order(symbol=symbol, side=side, type=ORDER_TYPE_MARKET, quantity=quantity)
                time.sleep(1)
                self._set_tp_sl(symbol, side, atr)
            except Exception as e:
                 self._log(f"HATA ({symbol} pozisyon açma): {e}")

    def _set_tp_sl(self, symbol: str, side: str, atr: float):
        # ... (Bu fonksiyon da artık symbol parametresini kullanır) ...
        # ... (Önceki versiyondaki gibi ama self.active_symbol yerine symbol kullanılır) ...
        try:
            position_list = self.client.futures_position_information(symbol=symbol)
            if not position_list: return
            position = position_list[0]
            # ... (geri kalanı önceki versiyon ile büyük ölçüde aynı) ...

        except Exception as e:
            self._log(f"HATA ({symbol} TP/SL ayarlama): {e}")
            
    # Diğer yardımcı fonksiyonlar (_calculate_quantity, _get_market_data vb.)
    # zaten symbol parametresi aldığı için çoğunlukla aynı kalabilir.

    def stop_strategy_loop(self):
        self._log("Strateji durduruluyor...")
        self.strategy_active = False

    def set_leverage(self, leverage: int, symbol: str):
        try:
            self.client.futures_change_leverage(symbol=symbol, leverage=leverage)
        except BinanceAPIException as e:
            # Bu hatayı loglamak yerine sessiz kalabiliriz çünkü sıkça aynı ayarı yapmaya çalışabilir
            pass

    # ... (Gerekirse diğer eski fonksiyonlar buraya eklenebilir veya silinebilir) ...
    # ... Web arayüzü fonksiyonları bu yeni mantıkla uyumsuz kalacaktır. ...
    # ... Onları şimdilik devre dışı bırakmak en iyisi. ...
