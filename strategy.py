import pandas as pd
import pandas_ta as ta
import configparser
from typing import Tuple

def get_signal(df: pd.DataFrame, config: configparser.SectionProxy) -> Tuple[str, float]:
    """
    Daha dengeli işlem yapmak için tasarlanmış, EMA kesişimi ve RSI onayına dayalı
    "KadirV2 Agresif" momentum stratejisi.

    Args:
        df (pd.DataFrame): Mum verilerini içeren DataFrame.
        config (configparser.SectionProxy): Strateji ayarlarını içeren config bölümü ([STRATEGY_KadirV2]).

    Returns:
        Tuple[str, float]: ('Sinyal', ATR Değeri) -> ('LONG', 0.0025)
    """
    ema_fast_len = int(config['ema_length_fast'])
    ema_slow_len = int(config['ema_length_slow'])
    rsi_len = int(config['rsi_length'])
    rsi_ob = int(config['rsi_overbought'])
    rsi_os = int(config['rsi_oversold'])
    atr_len = int(config['atr_length'])

    df[f"EMA_{ema_fast_len}"] = ta.ema(df['close'], length=ema_fast_len)
    df[f"EMA_{ema_slow_len}"] = ta.ema(df['close'], length=ema_slow_len)
    df[f"RSI_{rsi_len}"] = ta.rsi(df['close'], length=rsi_len)
    df[f"ATR_{atr_len}"] = ta.atr(df['high'], df['low'], df['close'], length=atr_len)

    latest = df.iloc[-2]
    prev = df.iloc[-3]

    ema_bull_cross = (latest[f"EMA_{ema_fast_len}"] > latest[f"EMA_{ema_slow_len}"]) and (prev[f"EMA_{ema_fast_len}"] <= prev[f"EMA_{ema_slow_len}"])
    ema_bear_cross = (latest[f"EMA_{ema_fast_len}"] < latest[f"EMA_{ema_slow_len}"]) and (prev[f"EMA_{ema_fast_len}"] >= prev[f"EMA_{ema_slow_len}"])

    rsi_confirm_long = latest[f"RSI_{rsi_len}"] > rsi_os
    rsi_confirm_short = latest[f"RSI_{rsi_len}"] < rsi_ob

    if ema_bull_cross and rsi_confirm_long:
        return 'LONG', latest[f"ATR_{atr_len}"]

    if ema_bear_cross and rsi_confirm_short:
        return 'SHORT', latest[f"ATR_{atr_len}"]

    return 'WAIT', 0.0
