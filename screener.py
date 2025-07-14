# screener.py

from binance.client import Client
from typing import Optional, List

def find_top_volatile_coins(api_key: str, api_secret: str, testnet: bool, count: int = 5) -> List[str]:
    """
    Binance Futures'taki son 24 saatte en çok yüzde değişimi yaşamış,
    belirtilen sayıda USDT paritesini bulur.

    Args:
        api_key (str): Kullanıcının Binance API anahtarı.
        api_secret (str): Kullanıcının Binance gizli anahtarı.
        testnet (bool): Testnet'e bağlanılıp bağlanılmayacağını belirtir.
        count (int): Döndürülecek en volatil coin sayısı.

    Returns:
        List[str]: En hareketli coinlerin sembol listesini (örn: ['BTCUSDT', 'ETHUSDT'])
                   veya bir hata durumunda boş bir liste döndürür.
    """
    try:
        client = Client(api_key, api_secret, testnet=testnet)
        tickers = client.futures_ticker()
        
        usdt_tickers = [
            ticker for ticker in tickers 
            if ticker['symbol'].endswith('USDT') and 'BUSD' not in ticker['symbol'] and 'USDC' not in ticker['symbol']
        ]
        
        if not usdt_tickers:
            print("Tarayıcı: Uygun USDT paritesi bulunamadı.")
            return []
            
        # Fiyat değişim yüzdesine göre en hareketli coinleri sırala
        # abs() ile hem pozitif hem de negatif yöndeki en büyük değişime bakıyoruz
        sorted_tickers = sorted(usdt_tickers, key=lambda x: abs(float(x['priceChangePercent'])), reverse=True)
        
        # En hareketli 'count' kadar coinin sembolünü al
        top_coins = [ticker['symbol'] for ticker in sorted_tickers[:count]]
        
        return top_coins
        
    except Exception as e:
        print(f"Coin tarayıcı hatası: {e}")
        return []
