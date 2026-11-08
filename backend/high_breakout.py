"""60-trading-bar intraday-high breakout scanner, with incremental persistent cache.

Strategy: Shanghai/Shenzhen main board; listed >=1000 calendar days; raw close
<=15 RMB; ordinary ST allowed but *ST / S*ST excluded; today's raw high equals
maximum of the latest 60 *actual stock trading bars* (including today).

This uses the existing market-cache branch and 1001 OHLCV replay cache. It
never treats an empty/missing daily quote as a zero-price or a suspension.
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, datetime, timedelta
import logging
import math
from pathlib import Path
import re
import shutil
from time import perf_counter
import unicodedata

from backend.batch_quotes import fetch_batch_quotes
from backend.market_cache import MarketCache
from backend.update import SH_TZ, atomic_json, render_bars
from backend.incremental_update import replay_history, usable_quote
from backend.core import is_main_board

LOG = logging.getLogger('astock')
PRICE_CAP = 15.0
WINDOW = 60
MIN_LISTED_DAYS = 1000


def is_star_st(name: str) -> bool:
    """Exclude *ST/S*ST/ST*; do not exclude ordinary ST or non-ST stocks.

    Full-width asterisk, whitespace and lowercase are normalized. "退市" is
    also excluded because it is not an ordinary ST equity.
    """
    s = unicodedata.normalize('NFKC', str(name)).upper()
    s = re.sub(r'\s+', '', s)
    return bool(re.match(r'^(?:S)?(?:\*ST|ST\*)', s)) or '退市' in s


def qualifies_basic(code: str, name: str, close: float,
                    listed: date, asof: date) -> bool:
    return (is_main_board(code) and not is_star_st(name) and
            math.isfinite(float(close)) and 0 < float(close) <= PRICE_CAP and
            listed <= asof and (asof - listed).days >= MIN_LISTED_DAYS)


def high_breakout(highs: list[list], today_high: float) -> bool:
    """HHV(H,60) includes today, so ties with prior highs qualify."""
    if len(highs) != WINDOW or not math.isfinite(float(today_high)) or today_high <= 0:
        return False
    values = [float(x[1]) for x in highs]
    return abs(values[-1] - today_high) <= max(.011, today_high * .001) and \
           values[-1] >= max(values) - 1e-8


def _history_frame(provider, code: str, asof: date):
    """Fetch raw OHLCV only when a stock lacks a complete high window.

    One historical request gives both OHLCV and the 60 daily highs. Start at
    450 calendar days to absorb long holidays and most suspensions.
    """
    return provider.history(code, (asof - timedelta(days=450)).strftime('%Y%m%d'),
                            asof.strftime('%Y%m%d'), '')


def _quote_from_frame(code: str, fallback_name: str, df, asof: date):
    if df.empty or df.iloc[-1]['日期'].date() != asof:
        return None
    if len(df) < 2:
        raise ValueError('无法计算前收盘价：历史K线不足两根')
    today = df.iloc[-1]
    prev = float(df.iloc[-2]['收盘'])
    close = float(today['收盘'])
    if prev <= 0 or close <= 0:
        raise ValueError('历史行情中前收盘价或今日收盘价无效')
    return {'code':code, 'name':fallback_name, 'date':asof.isoformat(),
            'o':float(today['开盘']), 'h':float(today['最高']),
            'l':float(today['最低']), 'c':close,
            'v':float(today['成交量']), 'prev':prev,
            'change_pct':round((close / prev - 1)*100, 4)}


def _highs_from_raw(df, quote, asof: date, days: set[date]) -> list[list]:
    """Use actual stock trading bars; append verified current batch quote if
    the raw historic endpoint has only published through the previous session.
    """
    bars = [[row['日期'].strftime('%Y-%m-%d'), float(row['最高'])]
            for _, row in df.iterrows() if row['日期'].date() <= asof]
    if not bars:
        raise ValueError('原始最高价历史为空')
    last_day = date.fromisoformat(bars[-1][0])
    if last_day < asof:
        # Only append a *single* missing, completed exchange session. Any
        # earlier missing session requires another reliable history source.
        if any(last_day < d < asof for d in days):
            raise ValueError('历史行情落后多个交易日，不能仅靠今日实时行情补齐')
        bars.append([asof.isoformat(), float(quote['h'])])
    elif last_day > asof:
        raise ValueError('历史最高价出现未来日期')
    if len(bars) < WINDOW or bars[-1][0] != asof.isoformat():
        raise ValueError('最近60根交易日最高价不足')
    highs = bars[-WINDOW:]
    if abs(highs[-1][1] - float(quote['h'])) > max(.011, float(quote['h'])*.001):
        raise ValueError('批量当日最高价和历史最高价不一致')
    return highs


def _highs_from_replay(cache: MarketCache, code: str, q: dict, asof: date,
                       days: set[date]) -> list[list] | None:
    # Reuse downloaded 1001 daily bars from the old RPS simulation where able.
    old = cache.get_history(code)
    if not old or len(old) < WINDOW:
        return None
    bars = [[b['date'], float(b['h'])] for b in old if b['date'] <= asof.isoformat()]
    if not bars:
        return None
    last = date.fromisoformat(bars[-1][0])
    if last < asof and not any(last < d < asof for d in days):
        bars.append([asof.isoformat(), float(q['h'])])
    if len(bars) < WINDOW or bars[-1][0] != asof.isoformat():
        return None
    highs = bars[-WINDOW:]
    if abs(highs[-1][1] - float(q['h'])) > max(.011, float(q['h'])*.001):
        return None
    return highs


def grow_highs(cache: MarketCache, code: str, quote: dict, asof: date,
               days: set[date], provider, raw_frame=None):
    old = cache.get_highs(code)
    target = asof.isoformat()
    if old and old[-1][0] == target:
        # An exact-day cache is immutable for repeated runs (unless the quote
        # conflicts with it, in which case repair instead of hiding conflict).
        if abs(float(old[-1][1]) - float(quote['h'])) <= max(.011, float(quote['h'])*.001):
            return old, 'high_hit'
    if old and old[-1][0] < target and raw_frame is None:
        previous = date.fromisoformat(old[-1][0])
        if not any(previous < d < asof for d in days):
            highs=(old+[[target, float(quote['h'])]])[-WINDOW:]
            cache.put_highs(code, highs, quote)
            return highs, 'high_append'
    # A first-time high backfill is required even if old 61-day QFQ *closes*
    # already exist: raw highs cannot be reconstructed from closing prices.
    if raw_frame is None:
        replay = _highs_from_replay(cache, code, quote, asof, days)
        if replay is not None:
            cache.put_highs(code, replay, quote)
            return replay, 'high_replay_seed'
        raw_frame = _history_frame(provider, code, asof)
    highs = _highs_from_raw(raw_frame, quote, asof, days)
    cache.put_highs(code, highs, quote)
    return highs, 'high_bootstrap' if old is None else 'high_repair'


def run(provider, outdir: Path, cache: MarketCache, asof: date, days: set[date],
        min_coverage: float = .98, quote_fetcher=fetch_batch_quotes,
        bootstrap_workers: int = 2, bootstrap_max_seconds: int = 4500,
        historical_target: bool = False):
    t0 = perf_counter()
    listed = provider.listings()
    universe = {c:(name,d) for c,(name,d) in listed.items()
                if is_main_board(c) and d <= asof and (asof-d).days >= MIN_LISTED_DAYS}
    if len(universe) < 100:
        raise RuntimeError('主板上市满1000天的股票名单异常稀少')
    existing = {c:rec['quote'] for c in universe
                if (rec:=cache.records.get(c)) and usable_quote(rec.get('quote'),asof)}
    missing = [c for c in universe if c not in existing]
    if historical_target:
        # Historical target must never use real-time batch quotes of *today*.
        market_quotes = existing
        LOG.info('60日新高：历史目标日%s；复用同日行情缓存%d只，其余按日期补取',asof,len(existing))
    else:
        obtained = quote_fetcher(missing, asof) if missing else {}
        market_quotes = {**existing,**obtained}
        LOG.info('60日新高：已缓存同日行情%d只；本次批量请求%d只；获得有效报价%d/%d',
                 len(existing),len(missing),len(market_quotes),len(universe))
    t_quotes=perf_counter()
    stats={k:0 for k in ('high_hit','high_append','high_bootstrap','high_replay_seed',
                          'high_repair','over_price','star_st','inactive','error')}
    found=[]
    errors=[]

    def one(code):
        name,ipo=universe[code]
        raw = None
        try:
            q=market_quotes.get(code)
            if not usable_quote(q,asof):
                raw=_history_frame(provider,code,asof)
                q=_quote_from_frame(code,name,raw,asof)
                if q is None:
                    return 'inactive',code,None,None,None
            # An old RPS cache may have previous-day quotes; overwrite only
            # after the new target-date quote has been verified.
            cache.set_quote(code,q)
            current_name=str(q.get('name') or name)
            if is_star_st(current_name):
                return 'star_st',code,None,None,None
            if float(q['c']) > PRICE_CAP:
                return 'over_price',code,None,None,None
            highs,source = grow_highs(cache,code,q,asof,days,provider,raw)
            if len(highs)!=WINDOW:
                raise ValueError('最高价缓存不足60根')
            if not high_breakout(highs,float(q['h'])):
                return source,code,None,None,None
            selected={'code':code, 'name':current_name, 'price':round(float(q['c']),3),
                      'change_pct':round(float(q['change_pct']),3),
                      'listing_date':ipo.isoformat(),
                      'high_today':round(float(q['h']),4),
                      'high_60':round(max(float(x[1]) for x in highs),4),
                      'high_window':WINDOW}
            return source,code,selected,None,None
        except Exception as ex:
            return 'error',code,None,f'{code}: {ex}',None

    codes = sorted(universe)
    for offset in range(0,len(codes),100):
        group=codes[offset:offset+100]
        with ThreadPoolExecutor(max_workers=max(1,min(4,bootstrap_workers))) as executor:
            for future in as_completed([executor.submit(one,c) for c in group]):
                kind,code,row,err,_=future.result()
                stats[kind]+=1
                if row:found.append(row)
                if err:errors.append(err)
        cache.save()
        n=offset+len(group)
        LOG.info('60日新高筛选%d/%d：缓存命中%d、单日追加%d、首次最高价回填%d、'
                 '旧回放复用%d、修复%d、超15元%d、*ST排除%d、停牌%d、错误%d、入选%d',
                 n,len(codes),stats['high_hit'],stats['high_append'],stats['high_bootstrap'],
                 stats['high_replay_seed'],stats['high_repair'],stats['over_price'],
                 stats['star_st'],stats['inactive'],stats['error'],len(found))
        if n<len(codes) and perf_counter()-t0>bootstrap_max_seconds:
            LOG.warning('60日最高价缓存初始化达到时间预算；已保存%d/%d只处理进度，'
                        '下次从持久缓存继续补齐',n,len(codes))
            raise RuntimeError('60日最高价缓存初始化达到单次时间预算；已保存已验证进度，'
                               '请重新运行以继续（本次不发布不完整行情）')
    cache.save()
    t_roll=perf_counter()
    denom=len(universe)-stats['inactive']
    coverage=(denom-stats['error'])/max(1,denom)
    if coverage < min_coverage:
        raise RuntimeError(f'60日最高价数据完整率{coverage:.2%}低于{min_coverage:.0%}：{errors[:5]}')
    if not found:
        raise RuntimeError('没有符合≤15元且60日最高价的股票，保留原网页数据')
    # No artificial 100-stock cap, and no RPS-based order. Stock codes are
    # deterministic, so prev/next navigation follows the visible list.
    found.sort(key=lambda r:r['code'])
    LOG.info('60日新高选股结果：符合条件%d只（包含普通ST，排除*ST；不限制数量）',len(found))
    t_selected=perf_counter()
    staged=[];chart_errors=[]
    counts={k:0 for k in ('replay_hit','replay_append','replay_catchup','replay_new')}
    for i,s in enumerate(found,1):
        try:
            rec=cache.records.get(s['code'],{})
            bars,kind=replay_history(provider,cache,s,rec.get('quote'),asof,days)
            if bars[-1]['date']!=asof.isoformat() or len(bars)<61:
                raise RuntimeError('60日回放K线数量或日期无效')
            counts[kind]+=1
            staged.append((s,{'code':s['code'],'name':s['name'],
                              'asof':asof.isoformat(),'bars':bars,
                              'data_type':'raw_ohlcv','bar_unit':'hand'}))
        except Exception as ex:
            chart_errors.append(f'{s["code"]}: {ex}')
        if i%20==0 or i==len(found):
            LOG.info('60日新高回放缓存%d/%d：直接命中%d、追加%d、补缺%d、首次下载%d、错误%d',
                     i,len(found),counts['replay_hit'],counts['replay_append'],
                     counts['replay_catchup'],counts['replay_new'],len(chart_errors))
        if i<len(found) and perf_counter()-t0>bootstrap_max_seconds:
            cache.save()
            raise RuntimeError('回放历史补齐达到单次时间预算；已保存已验证K线到持久缓存，'
                               '请重新运行以继续（本次不会发布不完整选股）。')
    cache.save()
    if len(staged)/len(found)<min_coverage:
        raise RuntimeError(f'回放K线完整率{len(staged)}/{len(found)}低于{min_coverage:.0%}：{chart_errors[:5]}')
    t_history=perf_counter()
    ready={s['code'] for s,_ in staged}
    public=[{**s,'has_history':s['code'] in ready,
             'history_url':f'data/stocks/{s["code"]}.json' if s['code'] in ready else None}
            for s in found]
    manifest={'schema_version':1,'is_demo':False,'strategy':'high60',
              'asof':asof.isoformat(),
              'generated_at':datetime.now(SH_TZ).isoformat(timespec='seconds'),
              'selection_window':WINDOW,'price_cap':PRICE_CAP,
              'listing_min_days':MIN_LISTED_DAYS,
              'allow_regular_st':True,'exclude_star_st':True,
              'candidate_count':len(universe),'stock_count':len(public),
              'history_ready_count':len(staged),
              'market_coverage':round(coverage,4),
              'selection_rule':'沪深主板；上市≥1000自然日；收盘价≤15元；普通ST允许、*ST排除；当日最高价=最近60个交易日最高价（含当日，未复权）',
              'stocks':public,
              'warnings':(['入选股票不设前100名上限；按代码顺序展示']+
                          ([f'有{len(chart_errors)}只股票的模拟历史尚未就绪'] if chart_errors else []))}
    temp=outdir.parent/(outdir.name+'_stage')
    if temp.exists():shutil.rmtree(temp)
    try:
        (temp/'stocks').mkdir(parents=True)
        for s,doc in staged:
            atomic_json(temp/'stocks'/f'{s["code"]}.json',doc)
        atomic_json(temp/'manifest.json',manifest)
        if outdir.exists():shutil.rmtree(outdir)
        temp.rename(outdir)
    finally:
        if temp.exists():shutil.rmtree(temp)
    cache.save()
    LOG.info('60日新高更新成功：%s，入选%d只，历史就绪%d只',asof,len(found),len(staged))
    LOG.info('耗时(秒)：行情%.1f、最高价缓存%.1f、筛选%.1f、回放%.1f、合计%.1f',
             t_quotes-t0,t_roll-t_quotes,t_selected-t_roll,t_history-t_selected,perf_counter()-t0)
    return manifest
