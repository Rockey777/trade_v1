"""Persistent, strictly validated rolling market cache (branch: market-cache).

Cache is disposable/rebuildable and is *not* the PWA's published website.
All writes are atomic, and a valid record is saved only after verification.
"""
from __future__ import annotations
import gzip
import json
import math
import os
import threading
from copy import deepcopy
from pathlib import Path

SCHEMA = 1


def read_gz(path: Path):
    if not path.exists():
        return None
    try:
        with gzip.open(path, 'rt', encoding='utf-8') as f:
            return json.load(f)
    except (OSError, UnicodeError, ValueError, TypeError):
        return None


def write_gz(path: Path, value) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + '.tmp')
    try:
        with gzip.open(tmp, 'wt', encoding='utf-8', compresslevel=5) as f:
            json.dump(value, f, ensure_ascii=False, allow_nan=False, separators=(',', ':'))
        os.replace(tmp, path)
    finally:
        tmp.unlink(missing_ok=True)


def valid_roll(obj: dict | None, window=60) -> bool:
    if not isinstance(obj, dict) or not isinstance(obj.get('bars'), list):
        return False
    bars = obj['bars']
    if not bars or len(bars) > window+1:
        return False
    dates = [b[0] for b in bars if isinstance(b, list) and len(b) == 2]
    if len(dates) != len(bars) or sorted(set(dates)) != dates:
        return False
    try:
        return all(math.isfinite(float(b[1])) and float(b[1]) > 0 for b in bars) and float(obj['raw_close']) > 0
    except (ValueError, TypeError, KeyError):
        return False


def valid_highs(obj: dict | None, window=60) -> bool:
    if not isinstance(obj,dict) or not isinstance(obj.get('highs'),list):
        return False
    highs=obj['highs']
    if not highs or len(highs)>window:
        return False
    dates=[p[0] for p in highs if isinstance(p,list) and len(p)==2]
    if len(dates)!=len(highs) or dates!=sorted(set(dates)):
        return False
    try:
        return all(math.isfinite(float(p[1])) and float(p[1])>0 for p in highs)
    except (ValueError,TypeError):
        return False


def valid_snapshot(obj: dict | None) -> bool:
    if not isinstance(obj,dict) or not isinstance(obj.get('quote'),dict):
        return False
    q=obj['quote']
    try:
        return (isinstance(q['date'],str) and len(q['date'])==10 and
                math.isfinite(float(q['c'])) and float(q['c'])>0 and
                math.isfinite(float(q['h'])) and float(q['h'])>0)
    except (KeyError,TypeError,ValueError):
        return False


class MarketCache:
    def __init__(self, root: str | Path, window: int = 60):
        self.root = Path(root)
        self.window = window
        self._lock = threading.RLock()
        obj = read_gz(self.root / 'universe.json.gz')
        self.records = obj.get('records', {}) if isinstance(obj, dict) and obj.get('schema') == SCHEMA and obj.get('window') == window else {}
        self.records = {str(k):v for k,v in self.records.items() if valid_roll(v, window) or valid_highs(v, window) or valid_snapshot(v)}
        self.history_dir = self.root / 'history'

    def save(self):
        with self._lock:
            snapshot=deepcopy(self.records)
        write_gz(self.root / 'universe.json.gz', {'schema':SCHEMA, 'window':self.window, 'records':snapshot})

    def get_roll(self, code: str):
        rec=self.records.get(code)
        return rec if valid_roll(rec,self.window) else None

    def put_roll(self, code: str, bars: list, raw_close: float):
        old=self.records.get(code,{})
        obj = {**old, 'bars':bars[-self.window-1:], 'raw_close':float(raw_close)}
        if not valid_roll(obj,self.window):
            raise ValueError(f'{code}的RPS缓存不合法')
        with self._lock:
            self.records[code] = obj

    def set_quote(self,code: str,quote:dict):
        # Also supports stocks with no historical high window yet. This lets
        # subsequent runs reuse verified point-in-time quotes independently.
        if not valid_snapshot({'quote':quote}):
            raise ValueError(f'{code}当日快照无效')
        with self._lock:
            self.records.setdefault(code,{})['quote'] = quote

    def get_highs(self,code: str):
        rec=self.records.get(code)
        return rec.get('highs') if valid_highs(rec,self.window) else None

    def put_highs(self,code: str,highs:list,quote:dict):
        obj={'highs':highs[-self.window:],'quote':quote}
        if not valid_highs(obj,self.window) or not valid_snapshot(obj):
            raise ValueError(f'{code}的60日最高价缓存不合法')
        with self._lock:
            self.records[code]={**self.records.get(code,{}),**obj}

    def get_history(self, code: str):
        obj = read_gz(self.history_dir / f'{code}.json.gz')
        if not isinstance(obj, dict) or obj.get('code') != code or not isinstance(obj.get('bars'),list):
            return None
        bars = obj['bars']
        if not bars or any(not isinstance(b,dict) or not all(k in b for k in ('date','o','h','l','c','v')) for b in bars):
            return None
        dates=[b['date'] for b in bars]
        if sorted(set(dates)) != dates:
            return None
        return bars

    def put_history(self, code: str, bars: list):
        if not bars or any(not isinstance(b,dict) for b in bars):
            raise ValueError('历史缓存K线无效')
        dates = [b['date'] for b in bars]
        if sorted(set(dates)) != dates:
            raise ValueError('历史缓存日期重复或乱序')
        write_gz(self.history_dir / f'{code}.json.gz', {'schema':SCHEMA,'code':code,'bars':bars[-1001:]})
