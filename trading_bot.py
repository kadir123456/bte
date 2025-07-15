# trading_bot.py (Sadece Hata Teşhisi İçin)

import os
import configparser
from binance.client import Client
from binance.exceptions import BinanceAPIException

class TradingBot:
    """
    Bu, botun sadece bir teşhis versiyonudur.
    Tek amacı, Binance API bağlantısını test etmek ve ham hata mesajını loglara yazdırmaktır.
    """
    def __init__(self, log_callback=None, ui_update_callback=None, status_callback=None):
        # Callback fonksiyonları bu testte kullanılmayacak ama uyumluluk için duruyor.
        self.log_callback = print 
        
        self._log("--- TEŞHİS MODU BAŞLATILDI ---")
        
        # 1. Ayarları ve anahtarları yükle
        self.config = self._load_config()
        api_key = os.environ.get('BINANCE_API_KEY')
        api_secret = os.environ.get('BINANCE_API_SECRET')
        
        if not api_key or not api_secret:
            self._log("!!! HATA: API anahtarları ortam değişkenlerinde bulunamadı.")
            return

        self._log("API anahtarları başarıyla yüklendi.")
        
        # 2. Binance istemcisini oluştur
        self.client = Client(api_key, api_secret)
        self._log("Binance istemcisi oluşturuldu.")

        # 3. Bağlantıyı test et ve sonucu logla
        self.test_api_connection()

    def _log(self, message: str):
        """Mesajları hem konsola hem de arayüze (eğer bağlıysa) gönderir."""
        log_message = f"[TEŞHİS] {message}"
        print(log_message)
        if self.log_callback:
            try:
                self.log_callback(log_message)
            except Exception:
                pass # Arayüz bağlı değilse hata vermesini engelle

    def _load_config(self) -> configparser.ConfigParser:
        """Config dosyasını okur."""
        parser = configparser.ConfigParser()
        parser.read('config.ini', encoding='utf-8')
        return parser

    def test_api_connection(self):
        """
        API bağlantısını iki farklı yöntemle test eder:
        1. Hesap Bilgileri (Genel Erişim)
        2. Sembol Listesi (Piyasa Verisi Erişimi)
        """
        self._log("--- TEST 1: Hesap Bilgileri Çekme ---")
        try:
            # Bu komut, anahtarların geçerli olup olmadığını ve izinleri test eder.
            account_info = self.client.futures_account()
            if account_info:
                self._log("✅ BAŞARILI: Hesap bilgileri çekilebildi.")
                self._log(f"✅ Toplam Bakiye: {account_info.get('totalWalletBalance')}")
            else:
                self._log("!!! UYARI: Hesap bilgisi çekildi ama içerik boş geldi.")

        except BinanceAPIException as e:
            self._log(f"!!! BAŞARISIZ: Binance API Hatası Alındı !!!")
            self._log(f"    -> Hata Kodu: {e.code}")
            self._log(f"    -> Hata Mesajı: {e.message}")
        except Exception as e:
            self._log(f"!!! BAŞARISIZ: Genel bir hata oluştu: {e}")

        self._log("--- TEST 2: Sembol Listesi Çekme ---")
        try:
            # Bu komut, piyasa verilerine erişim iznini test eder.
            info = self.client.futures_exchange_info()
            symbols = [s['symbol'] for s in info['symbols'] if s['symbol'].endswith('USDT')]
            if symbols:
                self._log(f"✅ BAŞARILI: {len(symbols)} adet sembol listesi çekildi.")
            else:
                self._log("!!! UYARI: Sembol listesi çekildi ama içerik boş geldi.")

        except BinanceAPIException as e:
            self._log(f"!!! BAŞARISIZ: Binance API Hatası Alındı !!!")
            self._log(f"    -> Hata Kodu: {e.code}")
            self._log(f"    -> Hata Mesajı: {e.message}")
        except Exception as e:
            self._log(f"!!! BAŞARISIZ: Genel bir hata oluştu: {e}")
            
        self._log("--- TEŞHİS MODU TAMAMLANDI ---")

    # Bu teşhis botunda diğer fonksiyonlara ihtiyacımız yok.
    # Onları geçici olarak devre dışı bırakıyoruz.
    def start_strategy(self): self._log("Teşhis modunda strateji başlatılamaz.")
    def stop_strategy(self): pass
    def get_all_usdt_symbols(self): return []
```

#### 2. Adım: Projeyi Yeniden Dağıtın
* Yukarıdaki kodu projenizdeki `trading_bot.py` dosyasına yapıştırın.
* Değişikliği kaydedip GitHub'a yollayın.
* Render.com'da "Manual Deploy" > **"Clear build cache & deploy"** ile servisi temiz bir şekilde yeniden başlatın.

#### 3. Adım: Logları Paylaşın
* Uygulama başladıktan sonra, Render loglarında **`[TEŞHİS]`** ile başlayan satırlar göreceksiniz.
* Bu satırlar, bize Binance'in verdiği hatanın kodunu ve mesajını net bir şekilde gösterecektir.
* Lütfen bu **`[TEŞHİS]`** ile başlayan logların bir ekran görüntüsünü veya metnini benimle paylaşın.

Bu son adımdan sonra sorunun ne olduğunu net olarak görecek ve nihai çözümü uygulayabileceğ
