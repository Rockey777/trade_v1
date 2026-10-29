"""AKShare adapter, with clear provider failures instead of invented data."""
from __future__ import annotations
import time
from datetime import date
import pandas as pd


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


class AkshareProvider:
    def __init__(self, retries: int = 3, delay: float = .18):
        import akshare as ak
        self.ak = ak
        self.retries = retries
        self.delay = delay

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
                time.sleep(min(3 * (attempt + 1), 9))
        raise RuntimeError(f'{getattr(func,"__name__",func)} 请求失败：{error}')

    def listings(self) -> dict[str, tuple[str, date]]:
        """Get batch Shanghai and Shenzhen listing dates; no per-stock requests."""
        sh = self.call(self.ak.stock_info_sh_name_code, symbol='主板A股')
        sz = self.call(self.ak.stock_info_sz_name_code, symbol='A股列表')
        require_columns(sh, {'证券代码', '上市日期'}, '上交所上市信息')
        require_columns(sz, {'A股代码', 'A股上市日期'}, '深交所上市信息')
        result: dict[str, tuple[str, date]] = {}
        sh_name_col = '证券简称' if '证券简称' in sh else '证券简称A'
        sz_name_col = 'A股简称'
        for df, code_col, name_col, date_col in [
            (sh, '证券代码', sh_name_col, '上市日期'),
            (sz, 'A股代码', sz_name_col, 'A股上市日期'),
        ]:
            for _, row in df.iterrows():
                raw_code = str(row[code_col]).strip()
                if not raw_code or raw_code.lower() == 'nan':
                    continue
                code = raw_code.split('.')[0].zfill(6)
                dt = normalize_list_date(row[date_col])
                if dt:
                    result[code] = (str(row.get(name_col, '')).strip(), dt)
        if len(result) < 1000:
            raise RuntimeError(f'交易所上市信息数量异常：只有 {len(result)} 条')
        return result

    def spot(self) -> pd.DataFrame:
        df = self.call(self.ak.stock_zh_a_spot_em)
        require_columns(df, {'代码', '名称', '最新价', '涨跌幅', '60日涨跌幅'}, 'A股实时行情')
        return df

    def history(self, code: str, start: str, end: str, adjust: str = '') -> pd.DataFrame:
        df = self.call(self.ak.stock_zh_a_hist,
                       symbol=code, period='daily', start_date=start, end_date=end, adjust=adjust)
        require_columns(df, {'日期', '开盘', '最高', '最低', '收盘', '成交量'}, f'{code}历史数据')
        df = df.copy()
        df['日期'] = pd.to_datetime(df['日期'], errors='coerce')
        for c in ['开盘','最高','最低','收盘','成交量']:
            df[c] = pd.to_numeric(df[c], errors='coerce')
        return df.dropna(subset=['日期','开盘','最高','最低','收盘','成交量']).sort_values('日期').drop_duplicates('日期')

    def trading_days(self) -> set[date]:
        """Download calendar once per run (also used to resolve holidays)."""
        if not hasattr(self, '_trading_days_cache'):
            dates = self.call(self.ak.tool_trade_date_hist_sina)
            if 'trade_date' not in dates:
                raise RuntimeError('交易日历接口缺少trade_date')
            self._trading_days_cache = set(pd.to_datetime(dates['trade_date']).dt.date)
        return self._trading_days_cache

    def is_trade_day(self, today: date) -> bool:
        return today in self.trading_days()
