"""AKShare market-data adapter. Tencent daily history first, Eastmoney fallback.

Both providers use independently operated upstreams. We validate the schema and
requested date range before accepting a response; we never fabricate candles.
"""
from __future__ import annotations
import logging
import re
import threading
import time
from collections import Counter
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

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


def parse_baostock_basic(rows: pd.DataFrame) -> dict[str, tuple[str, date]]:
    """Validate independent BaoStock stock-basic records (not current spot quotes).

    BaoStock's ``type=1`` denotes stock and ``status=1`` denotes listed.
    Exclude all funds, indexes, delisted issues, and malformed IPO dates.
    """
    require_columns(rows, {'code', 'code_name', 'ipoDate', 'type', 'status'},
                    'BaoStock证券基本资料')
    result: dict[str, tuple[str, date]] = {}
    for _, r in rows.iterrows():
        full_code = str(r['code']).strip().lower()
        # Exact prefixed symbol, not split('.'). Include main-board ONLY.
        if not re.fullmatch(r'(?:sh\.(?:600|601|603|605)|sz\.(?:000|001|002))\d{3}', full_code):
            continue
        if str(r['type']).strip() != '1' or str(r['status']).strip() != '1':
            continue
        code = full_code.split('.')[1]
        name = str(r['code_name']).strip()
        if not name or name.lower() == 'nan':
            continue
        ipo = normalize_list_date(r['ipoDate'])
        if ipo is not None:
            result[code] = (name, ipo)
    return result


def validate_listings(result: dict[str, tuple[str, date]], source: str) -> None:
    """Reject obviously incomplete listing feeds rather than silently biasing RPS."""
    sh = sum(k.startswith('6') for k in result)
    sz = len(result) - sh
    if len(result) < 2000 or sh < 500 or sz < 500:
        raise RuntimeError(f'{source} 主板上市记录数量异常：总计{len(result)}、沪市{sh}、深市{sz}')


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
                 history_attempts: int = 2, baostock=None):
        if ak is None:
            import akshare as ak
        self.ak = ak
        self._baostock = baostock
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

    def _exchange_listings(self) -> dict[str, tuple[str, date]]:
        # Exchange endpoints sometimes return blank/non-JSON bodies to GitHub
        # runners. A failure must not prevent an independent feed being tried.
        sh = self.call(self.ak.stock_info_sh_name_code, symbol='主板A股')
        sz = self.call(self.ak.stock_info_sz_name_code, symbol='A股列表')
        require_columns(sh, {'证券代码', '上市日期'}, '上交所上市信息')
        require_columns(sz, {'A股代码', 'A股上市日期'}, '深交所上市信息')
        result: dict[str, tuple[str, date]] = {}
        sh_name_col = '证券简称' if '证券简称' in sh else '证券简称A'
        for df, code_col, name_col, date_col in [
            (sh, '证券代码', sh_name_col, '上市日期'),
            (sz, 'A股代码', 'A股简称', 'A股上市日期')]:
            require_columns(df, {code_col, name_col, date_col}, '交易所上市数据')
            for _, row in df.iterrows():
                raw_code = str(row[code_col]).strip()
                if not raw_code or raw_code.lower() == 'nan':
                    continue
                code = raw_code.split('.')[0].zfill(6)
                if not re.fullmatch(r'(?:600|601|603|605|000|001|002)\d{3}', code):
                    continue
                dt = normalize_list_date(row[date_col])
                name = str(row[name_col]).strip()
                if dt and name and name.lower() != 'nan':
                    result[code] = (name, dt)
        validate_listings(result, '交易所')
        return result

    def _baostock_listings(self) -> dict[str, tuple[str, date]]:
        # Import lazily so unit tests and installations not needing fallback do
        # not open any BaoStock connections. BaoStock's basic-data feed uses an
        # independent provider and supplies true IPO dates (not guessed dates).
        bs = self._baostock
        if bs is None:
            import baostock as bs
        logged_in = False
        try:
            login = bs.login()
            if str(login.error_code) != '0':
                raise RuntimeError(f'BaoStock登录失败：{login.error_code} {login.error_msg}')
            logged_in = True
            rs = bs.query_stock_basic()
            if str(rs.error_code) != '0':
                raise RuntimeError(f'BaoStock证券基本资料请求失败：{rs.error_code} {rs.error_msg}')
            records = []
            while rs.next():
                records.append(rs.get_row_data())
            if not records:
                raise RuntimeError('BaoStock证券基本资料返回空表')
            rows = pd.DataFrame(records, columns=rs.fields)
            result = parse_baostock_basic(rows)
            validate_listings(result, 'BaoStock')
            return result
        finally:
            if logged_in:
                try:
                    bs.logout()
                except Exception:
                    LOG.warning('BaoStock登出异常，忽略')

    def listings(self) -> dict[str, tuple[str, date]]:
        try:
            result = self._exchange_listings()
            LOG.info('股票基础资料：交易所接口成功，沪深主板%d只', len(result))
            return result
        except Exception as exc:
            LOG.warning('交易所股票名单接口不可用：%s；自动尝试独立的BaoStock接口', exc)
        try:
            result = self._baostock_listings()
            LOG.info('股票基础资料：BaoStock备用接口成功，沪深主板%d只', len(result))
            return result
        except Exception as exc:
            raise RuntimeError(
                '股票基础资料主备接口全部失败；无法可靠核验上市1000天，拒绝生成选股名单。'
                f' BaoStock错误：{exc}') from exc

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

    def _baostock_trading_calendar(self, now: datetime) -> tuple[set[date], set[date]]:
        """Independent recent trading calendar, including explicit non-trading days.

        Missing today in a response is not the same as a confirmed exchange holiday.
        """
        bs = self._baostock
        if bs is None:
            import baostock as bs
        connected = False
        try:
            result = bs.login()
            if str(result.error_code) != '0':
                raise RuntimeError(f'BaoStock登录失败：{result.error_code} {result.error_msg}')
            connected = True
            start = (now.date() - timedelta(days=65)).isoformat()
            rs = bs.query_trade_dates(start_date=start, end_date=now.date().isoformat())
            if str(rs.error_code) != '0':
                raise RuntimeError(f'BaoStock交易日历失败：{rs.error_code} {rs.error_msg}')
            entries = []
            while rs.next():
                entries.append(rs.get_row_data())
            all_dates, traded = set(), set()
            keys = list(rs.fields)
            if 'calendar_date' not in keys or 'is_trading_day' not in keys:
                raise RuntimeError(f'BaoStock交易日历列错误: {keys}')
            day_col, status_col = keys.index('calendar_date'), keys.index('is_trading_day')
            for row in entries:
                try:
                    day = date.fromisoformat(str(row[day_col]))
                except (ValueError, IndexError, TypeError):
                    continue
                all_dates.add(day)
                if str(row[status_col]).strip() == '1':
                    traded.add(day)
            if not all_dates:
                raise RuntimeError('BaoStock交易日历为空')
            return all_dates, traded
        finally:
            if connected:
                try:
                    bs.logout()
                except Exception:
                    LOG.warning('BaoStock交易日历登出失败，忽略')

    def trading_days(self, now: datetime | None = None) -> set[date]:
        """Two-source calendar with explicit protection against a stale current day.

        A stale Sina feed previously made the updater silently publish yesterday
        even after 18:00. BaoStock's date-labelled calendar is consulted to
        determine whether *today* was a trading day. If neither feed can verify
        a missing weekday after 18:00, fail rather than silently treating the
        previous day as the latest completed session.
        """
        if hasattr(self, '_trading_days_cache') and now is None:
            return self._trading_days_cache
        local = (now or datetime.now(ZoneInfo('Asia/Shanghai'))).astimezone(ZoneInfo('Asia/Shanghai'))
        today = local.date()
        sina = set()
        errors = []
        try:
            df = self.call(self.ak.tool_trade_date_hist_sina)
            if 'trade_date' not in df:
                raise RuntimeError('新浪交易日历缺少trade_date')
            sina = set(pd.to_datetime(df['trade_date'], errors='coerce').dt.date.dropna())
        except Exception as exc:
            errors.append(f'新浪: {exc}')
            LOG.warning('新浪交易日历读取失败，继续BaoStock: %s', exc)

        bao_verified = False
        try:
            bao_all, bao_traded = self._baostock_trading_calendar(local)
            if today in bao_all:
                bao_verified = True
                combined = sina | bao_traded
                # Remove erroneous positive today from Sina if BaoStock
                # explicitly says today is a non-trading date.
                if today not in bao_traded:
                    combined.discard(today)
                sina = combined
                LOG.info('交易日历交叉校验：新浪最新=%s，BaoStock确认今日%s，最终最新交易日=%s',
                         max(sina).isoformat() if sina else '无',
                         '交易' if today in bao_traded else '休市',
                         max(sina).isoformat() if sina else '无')
            else:
                errors.append(f'BaoStock未提供今日{today}的交易状态（最近返回{max(bao_all)}）')
        except Exception as exc:
            errors.append(f'BaoStock: {exc}')
            LOG.warning('BaoStock交易日历读取失败: %s', exc)

        if not sina:
            raise RuntimeError('两个交易日历均不可用：'+'; '.join(errors))
        # A weekday past market-close with no explicit holiday validation is
        # ambiguous: refuse to mark yesterday fresh when today may have traded.
        if local.weekday() < 5 and local.hour >= 18 and today not in sina and not bao_verified:
            raise RuntimeError(f'北京时间18点后无法确认{today}是否为交易日，拒绝静默回退；'+'; '.join(errors))
        if (today - max(sina)).days > 15:
            raise RuntimeError(f'交易日历严重滞后：最新{max(sina)}、今天{today}；'+'; '.join(errors))
        if now is None:
            self._trading_days_cache = sina
        return sina

    def is_trade_day(self, today: date) -> bool:
        return today in self.trading_days()
