"""AKShare market-data adapter. Tencent daily history first, Eastmoney fallback.

Both providers use independently operated upstreams. We validate the schema and
requested date range before accepting a response; we never fabricate candles.
"""
from __future__ import annotations
import logging
import threading
import time
from collections import Counter
from datetime import date

import pandas as pd

LOG = logging.getLogger('astock')


def require_columns(df: pd.DataFrame, needed: set[str], source: str) -> None:
    missing = needed - set(df.columns)
    if missing:
        raise RuntimeError(f'{source} 缺少必要字段: {sorted(missing)}; 当前字段={list(df.columns)}')


def normalize_list_date(value) -> date | None:
    if value is None or pd.isna(value):
        return None
    raw = str(value).strip().removesuffix('.0')
    if len(raw) == 8 and raw.isdigit():
        dt = pd.to_datetime(raw, format='%Y%m%d', errors='coerce')
    else:
        dt = pd.to_datetime(value, errors='coerce')
    return None if pd.isna(dt) else dt.date()


def normalize_history(df: pd.DataFrame, code: str, source: str,
                      start: str, end: str) -> pd.DataFrame:
    """Coerce EM's Chinese and Tencent's English candles to one strict format.

    Tencent's older AKShare releases expose traded *hands* as ``amount``;
    newer versions expose ``volume`` separately. The app's display uses the
    original per-source traded-volume unit; price and RPS never depend on it.
    """
    if not isinstance(df, pd.DataFrame) or df.empty:
        raise RuntimeError(f'{source}返回空表')
    df = df.copy()
    if '日期' not in df and 'date' not in df and df.index.name in ('date','日期'):
        df = df.reset_index()
    mapping = {'date':'日期','open':'开盘','close':'收盘','high':'最高',
               'low':'最低','volume':'成交量'}
    if '成交量' not in df and 'volume' not in df and 'amount' in df:
        mapping['amount']='成交量' # legacy TX: amount=手；not RMB turnover
    df = df.rename(columns=mapping)
    require_columns(df, {'日期','开盘','最高','最低','收盘','成交量'}, f'{source}:{code}')
    df['日期'] = pd.to_datetime(df['日期'], errors='coerce')
    for col in ('开盘','最高','最低','收盘','成交量'):
        df[col] = pd.to_numeric(df[col], errors='coerce')
    df = df.dropna(subset=['日期','开盘','最高','最低','收盘','成交量'])
    df = df[(df['日期']>=pd.to_datetime(start,format='%Y%m%d')) &
            (df['日期']<=pd.to_datetime(end,format='%Y%m%d'))]
    df = df[(df['收盘']>0) & (df['开盘']>0) & (df['最高']>0) &
            (df['最低']>0) & (df['成交量']>=0)]
    df = df.sort_values('日期').drop_duplicates('日期', keep='last')
    if df.empty:
        raise RuntimeError(f'{source}:{code}没有{start}-{end}范围内的有效K线')
    return df[['日期','开盘','最高','最低','收盘','成交量']].reset_index(drop=True)


class AkshareProvider:
    def __init__(self, retries: int = 3, delay: float = .18, ak=None,
                 history_attempts: int = 2):
        if ak is None:
            import akshare as ak
        self.ak = ak
        self.retries = retries
        self.delay = delay
        self.history_attempts = max(1,history_attempts)
        self._history_stats = Counter()
        self._history_lock = threading.Lock()

    def call(self, func, **kwargs):
        error = None
        for attempt in range(self.retries):
            try:
                result = func(**kwargs)
                if result is None or result.empty:
                    raise RuntimeError('接口返回空表')
                if self.delay:
                    time.sleep(self.delay)
                return result
            except Exception as exc:
                error = exc
                if attempt+1 < self.retries:
                    time.sleep(min(3*(attempt+1),9))
        raise RuntimeError(f'{getattr(func,"__name__",func)} 请求失败：{error}')

    def listings(self) -> dict[str, tuple[str, date]]:
        sh = self.call(self.ak.stock_info_sh_name_code, symbol='主板A股')
        sz = self.call(self.ak.stock_info_sz_name_code, symbol='A股列表')
        require_columns(sh, {'证券代码','上市日期'}, '上交所上市信息')
        require_columns(sz, {'A股代码','A股上市日期'}, '深交所上市信息')
        result: dict[str, tuple[str, date]] = {}
        sh_name_col = '证券简称' if '证券简称' in sh else '证券简称A'
        for df, code_col, name_col, date_col in [
            (sh,'证券代码',sh_name_col,'上市日期'),
            (sz,'A股代码','A股简称','A股上市日期')]:
            for _, row in df.iterrows():
                raw_code = str(row[code_col]).strip()
                if not raw_code or raw_code.lower() == 'nan':
                    continue
                code = raw_code.split('.')[0].zfill(6)
                dt = normalize_list_date(row[date_col])
                if dt:
                    result[code] = (str(row.get(name_col,'')).strip(),dt)
        if len(result) < 1000:
            raise RuntimeError(f'交易所上市信息数量异常：只有 {len(result)} 条')
        return result

    def spot(self) -> pd.DataFrame:
        df = self.call(self.ak.stock_zh_a_spot_em)
        require_columns(df, {'代码','名称','最新价','涨跌幅','60日涨跌幅'},'A股实时行情')
        return df

    def history(self, code: str, start: str, end: str, adjust: str = '') -> pd.DataFrame:
        """Use Tencent's independent upstream, then Eastmoney if Tencent fails.

        Each provider is retried briefly. Do not hammer an upstream with long
        repeated retries when it has already started disconnecting requests.
        """
        symbol = ('sh' if code.startswith('6') else 'sz') + code
        sources = (
            ('腾讯', self.ak.stock_zh_a_hist_tx,
             {'symbol':symbol, 'start_date':start,'end_date':end,'adjust':adjust}),
            ('东方财富', self.ak.stock_zh_a_hist,
             {'symbol':code,'period':'daily','start_date':start,'end_date':end,'adjust':adjust}),
        )
        errors = []
        for source,func,kwargs in sources:
            for attempt in range(self.history_attempts):
                try:
                    df = func(**kwargs)
                    normalized = normalize_history(df,code,source,start,end)
                    if self.delay:
                        time.sleep(self.delay)
                    with self._history_lock:
                        self._history_stats[source] += 1
                    return normalized
                except Exception as exc:
                    errors.append(f'{source}: {str(exc)[:170]}')
                    if attempt+1 < self.history_attempts:
                        time.sleep(.75*(attempt+1))
            with self._history_lock:
                self._history_stats[f'{source}失败'] += 1
        with self._history_lock:
            self._history_stats['全部失败'] += 1
        raise RuntimeError(f'{code} 历史日K线双源均失败；'+'; '.join(errors[-2:]))

    def history_stats(self) -> dict:
        with self._history_lock:
            return dict(self._history_stats)

    def trading_days(self) -> set[date]:
        if not hasattr(self,'_trading_days_cache'):
            dates = self.call(self.ak.tool_trade_date_hist_sina)
            if 'trade_date' not in dates:
                raise RuntimeError('交易日历接口缺少trade_date')
            self._trading_days_cache = set(pd.to_datetime(dates['trade_date']).dt.date)
        return self._trading_days_cache

    def is_trade_day(self, today: date) -> bool:
        return today in self.trading_days()
