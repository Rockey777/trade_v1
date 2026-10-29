"""Pure rules for A-share screening and RPS, independent of market-data providers."""
from __future__ import annotations
from dataclasses import dataclass
from datetime import date
import math
import re
import unicodedata

MAIN_BOARD = re.compile(r'^(?:600|601|603|605|000|001|002)\d{3}$')


def is_main_board(code: str) -> bool:
    return bool(MAIN_BOARD.fullmatch(str(code).zfill(6)))


def is_st(name: str) -> bool:
    normalized = unicodedata.normalize('NFKC', str(name)).upper().replace(' ', '')
    return bool(re.search(r'^S?\*?ST|退市', normalized))


def eligible(code: str, name: str, price: float, listing_date: date, asof: date) -> bool:
    return (is_main_board(code) and not is_st(name)
            and isinstance(price, (int, float)) and math.isfinite(price)
            and 0 < price <= 15.0 and listing_date <= asof
            and (asof - listing_date).days >= 1000)


def ranking(rows: list[dict], window: int = 60, take: int = 100) -> tuple[list[dict], list[dict]]:
    """Return RPS top 100 BEFORE +/-5% move filter, and survivors.

    Ties get the same rank (competition rank). RPS=100*(N-rank)/(N-1).
    """
    sorted_rows = sorted(rows, key=lambda x: (-x['return_pct'], x['code']))
    n = len(sorted_rows)
    if n < 2:
        raise ValueError('计算RPS至少需要2只有效股票')
    previous_return, previous_rank = None, None
    for idx, item in enumerate(sorted_rows):
        if previous_return is None or item['return_pct'] != previous_return:
            previous_rank = idx + 1
        item['rank'] = previous_rank
        item['rps'] = round(100 * (n - previous_rank) / (n - 1), 2)
        item['rps_window'] = window
        previous_return = item['return_pct']
    top = sorted_rows[:take]
    survivors = [r for r in top if -5 <= r['change_pct'] <= 5]
    return top, survivors


def compute_return(closes: list[float], days: int) -> float:
    if len(closes) <= days:
        raise ValueError('K线数量不足以计算RPS')
    old, now = closes[-days-1], closes[-1]
    if old <= 0 or now <= 0 or not math.isfinite(old) or not math.isfinite(now):
        raise ValueError('历史收盘价无效')
    return (now / old - 1) * 100
