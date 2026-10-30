"""V2.2: unified current/historical incremental bootstrap + persistent RPS/replay cache.

Run from repository root: python -m backend.incremental_update --output web/data
An incomplete/unverifiable snapshot NEVER overwrites the published manifest.
"""
from __future__ import annotations
import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, date, timedelta
import json
import logging
import math
from pathlib import Path
import shutil
from time import perf_counter
from backend.batch_quotes import fetch_batch_quotes
from backend.market_cache import MarketCache
from backend.update import SH_TZ, atomic_json, choose_asof, render_bars
from backend.core import eligible, is_main_board, is_st, ranking, compute_return
from backend.providers import AkshareProvider

LOG=logging.getLogger('astock')


def qfq_bootstrap(provider,code: str,asof: date,window: int,quote: dict):
    """Bootstrap *one* series, only once per stock or after detected rights event.

    QFQ is anchored to the latest raw close; never concatenate raw closes into a
    QFQ series unless the issuer's previous raw close matches quote prev close.
    """
    start=(asof-timedelta(days=max(500,window*3))).strftime('%Y%m%d')
    frame=provider.history(code,start,asof.strftime('%Y%m%d'),'qfq')
    if frame.iloc[-1]['日期'].date()!=asof or len(frame)<window+1:
        raise RuntimeError('前复权历史不足61根，或缺少本交易日')
    adj=float(frame.iloc[-1]['收盘'])
    if abs(adj-quote['c'])>max(.04,.006*quote['c']):
        raise RuntimeError(f'前复权最新收盘{adj}与批量报价{quote["c"]}不一致')
    return [[d.strftime('%Y-%m-%d'),round(float(v),6)] for d,v in zip(frame['日期'],frame['收盘'])][-window-1:]


def usable_quote(quote: dict | None, asof: date) -> bool:
    return (isinstance(quote,dict) and quote.get('date')==asof.isoformat() and
            all(math.isfinite(float(quote.get(k,0))) and float(quote.get(k,0))>0 for k in ('c','prev','o','h','l')) and
            math.isfinite(float(quote.get('v',-1))) and float(quote.get('v',-1))>=0)


def grow_roll(cache: MarketCache,code: str,quote: dict,asof: date,days:set[date],provider):
    old=cache.get_roll(code)
    if old and old['bars'][-1][0]==asof.isoformat():
        # Same-day manual run uses saved verified close (idempotent).
        return old['bars'],'cache_same_day'
    prev_day=date.fromisoformat(old['bars'][-1][0]) if old else None
    missing_sessions=prev_day and any(prev_day<d<asof for d in days)
    previous_raw=float(old['raw_close']) if old else None
    split=old is not None and abs(previous_raw-quote['prev'])>max(.045,.005*previous_raw)
    if old and len(old['bars'])==cache.window+1 and not missing_sessions and not split and prev_day<asof:
        bars=(old['bars']+[[asof.isoformat(),quote['c']]])[-cache.window-1:]
        source='append_one'
    else:
        bars=qfq_bootstrap(provider,code,asof,cache.window,quote)
        source='bootstrap' if old is None else 'repair'
    cache.put_roll(code,bars,quote['c'])
    return bars,source


def replay_history(provider,cache:MarketCache,stock:dict,quote:dict|None,asof:date,days:set[date]):
    code=stock['code']
    old=cache.get_history(code)
    asof_text=asof.isoformat()
    if old and old[-1]['date']==asof_text:
        return old,'replay_hit'
    if old and old[-1]['date']<asof_text:
        last=date.fromisoformat(old[-1]['date'])
        missed=[d for d in days if last<d<=asof]
        if len(missed)==1 and usable_quote(quote,asof):
            # For a normal one-day increment, OHLCV comes from the same batch quote.
            bar={k:quote[k] for k in ('date','o','h','l','c','v')}
            if abs(bar['c']-stock['price'])<=max(.04,.005*stock['price']):
                merged=(old+[bar])[-1001:]
                cache.put_history(code,merged)
                return merged,'replay_append'
        if missed and len(missed)<=15:
            # Catch up only missed trading days, not the entire 1001-bar history.
            start=(last+timedelta(days=1)).strftime('%Y%m%d')
            df=provider.history(code,start,asof.strftime('%Y%m%d'),'')
            new=render_bars(df)
            if new and new[-1]['date']==asof_text:
                merged=(old+[b for b in new if b['date']>old[-1]['date']])[-1001:]
                if merged[-1]['date']==asof_text:
                    cache.put_history(code,merged)
                    return merged,'replay_catchup'
    # Newly ranked or stale/damaged history: full history only for this stock.
    start=(asof-timedelta(days=7*365)).strftime('%Y%m%d')
    df=provider.history(code,start,asof.strftime('%Y%m%d'),'')
    bars=render_bars(df)[-1001:]
    if len(bars)<61 or bars[-1]['date']!=asof_text:
        raise RuntimeError(f'{code}历史K线不足或未更新到{asof_text}')
    if abs(bars[-1]['c']-stock['price'])>max(.04,.006*stock['price']):
        raise RuntimeError(f'{code}最新报价和历史日K线价格不一致')
    cache.put_history(code,bars)
    return bars,'replay_new'


def run(provider,outdir:Path,cache:MarketCache,asof:date,days:set[date],
        rps_window:int=60,min_coverage:float=.98,quote_fetcher=fetch_batch_quotes,
        bootstrap_workers:int=2, bootstrap_max_seconds:int=4500,
        historical_target:bool=False):
    t0=perf_counter()
    listings=provider.listings()
    universe={c:(n,d) for c,(n,d) in listings.items() if is_main_board(c) and d<=asof and (asof-d).days>=1000}
    if len(universe)<100:
        raise RuntimeError(f'股票基础名单过少：{len(universe)}')
    already=sum(1 for c in universe if cache.get_roll(c) and cache.get_roll(c)['bars'][-1][0]==asof.isoformat())
    LOG.info('RPS缓存：已有目标日完整数据%d/%d；本地缓存总量%d',already,len(universe),len(cache.records))
    if historical_target:
        # The Tencent bulk quote service is a *current* quote feed; asking it for
        # yesterday would reject every row and then issue thousands of needless
        # repairs. Reuse target-day cache and query exact historical closes only
        # for symbols that are still missing. There is no invented bulk historical
        # endpoint in this code path.
        quotes={c:(cache.get_roll(c) or {}).get('quote') for c in universe}
        LOG.info('历史交易日%s：直接进入持久缓存增量流程；'
                 '不调用只支持实时快照的批量接口；目标日已有缓存%d/%d',
                 asof,already,len(universe))
    elif already>=len(universe)*min_coverage:
        quotes={c:(cache.get_roll(c) or {}).get('quote') for c in universe}
        LOG.info('缓存已覆盖目标交易日%s，跳过全市场行情下载',asof)
    else:
        quotes=quote_fetcher(list(universe),asof)
    t_quotes=perf_counter()
    stats={'append_one':0,'bootstrap':0,'repair':0,'cache_same_day':0,
           'inactive':0,'unavailable':0,'error':0}
    errors=[]
    # Verify missing daily quote with *raw* history to distinguish suspension
    # from a network failure; never assume missing == suspension.
    def one(code):
        try:
            q=quotes.get(code)
            if not usable_quote(q,asof):
                # Restore an already-complete same-day cache even if quote is missing.
                old=cache.get_roll(code)
                if old and old['bars'][-1][0]==asof.isoformat() and old.get('quote'):
                    return code,'cache_same_day',old.get('quote'),None,old['bars']
                # On an unavailable batch row, query only this missing stock.
                df=provider.history(code,(asof-timedelta(days=40)).strftime('%Y%m%d'),asof.strftime('%Y%m%d'),'')
                if df.iloc[-1]['日期'].date()!=asof:
                    return code,'inactive',None,None,None
                # Use actual raw daily bar, with previous close from past raw day.
                if len(df)<2: raise RuntimeError('缺少昨日收盘价')
                last=df.iloc[-1]; prev=float(df.iloc[-2]['收盘'])
                if prev<=0:raise RuntimeError('昨日收盘价无效')
                q={'code':code,'name':universe[code][0], 'date':asof.isoformat(),
                   'o':float(last['开盘']),'h':float(last['最高']),'l':float(last['最低']),
                   'c':float(last['收盘']),'v':float(last['成交量']),'prev':prev,
                   'change_pct':round((float(last['收盘'])/prev-1)*100,4)}
            bars,how=grow_roll(cache,code,q,asof,days,provider)
            return code,how,q,None,bars
        except Exception as exc:
            return code,'error',None,str(exc),None

    # Process 100 stocks at a time, saving checkpoints. If initial full-market
    # history is too slow, stop *before* the job timeout so GitHub Actions can
    # still commit verified partial data to market-cache for the next run.
    codes=sorted(universe)
    for offset in range(0,len(codes),100):
        group=codes[offset:offset+100]
        with ThreadPoolExecutor(max_workers=max(1,min(4,bootstrap_workers))) as pool:
            futures=[pool.submit(one,c) for c in group]
            for f in as_completed(futures):
                code,kind,q,err,bars=f.result()
                stats[kind]=stats.get(kind,0)+1
                if err:errors.append(f'{code}: {err}')
                if q is not None and bars is not None:
                    cache.set_quote(code,q)
        cache.save()
        n=offset+len(group)
        LOG.info('全市场RPS缓存%d/%d：增量%d、首次%d、修复%d、命中%d、停牌%d、错误%d',
                 n,len(universe),stats['append_one'],stats['bootstrap'],stats['repair'],
                 stats['cache_same_day'],stats['inactive'],len(errors))
        if n<len(codes) and perf_counter()-t0>bootstrap_max_seconds:
            LOG.warning('时间预算已用尽，已保存%d/%d只股票的验证进度；'
                        '下次将跳过已有目标日缓存，只补缺失股票', n, len(codes))
            raise RuntimeError('增量缓存构建达到单次时间预算；已保存部分进度，'
                               '请再次运行以续建（本次未发布不完整行情）。')
    cache.save()
    t_roll=perf_counter()
    denom=len(universe)-stats['inactive']
    coverage=(denom-stats['error'])/max(1,denom)
    if coverage<min_coverage:
        raise RuntimeError(f'全市场数据覆盖率{coverage:.2%} < {min_coverage:.0%}; 异常示例：{errors[:4]}')
    rows=[];eligible_count=0
    for code,(list_name,listed) in universe.items():
        rec=cache.get_roll(code)
        if not rec or rec['bars'][-1][0]!=asof.isoformat() or len(rec['bars'])<rps_window+1:continue
        q=rec.get('quote')
        if not usable_quote(q,asof):continue
        name=q['name'] or list_name
        if eligible(code,name,float(q['c']),listed,asof):
            eligible_count+=1
            ret=compute_return([float(x[1]) for x in rec['bars']],rps_window)
            rows.append({'code':code,'name':name,'price':round(q['c'],3),
                         'change_pct':round(q['change_pct'],3),'listing_date':listed.isoformat(),
                         'rps_source':'rolling_qfq_61','return_pct':round(ret,5)})
    if len(rows)<100:
        raise RuntimeError(f'价格≤15元且RPS有效股票仅{len(rows)}只，拒绝发布')
    top,selected=ranking(rows,rps_window,100)
    if len(selected)!=100:raise RuntimeError('RPS排名未产生完整100只')
    t_rank=perf_counter()
    staged=[];chart_errors=[]
    counts={'replay_hit':0,'replay_append':0,'replay_catchup':0,'replay_new':0}
    for i,s in enumerate(selected,1):
        try:
            bars,kind=replay_history(provider,cache,s,cache.get_roll(s['code']).get('quote'),asof,days)
            if bars[-1]['date']!=asof.isoformat() or len(bars)<61:raise RuntimeError('回放数据日期或长度有误')
            counts[kind]+=1
            staged.append((s,{'code':s['code'],'name':s['name'],'asof':asof.isoformat(),
                              'bars':bars,'data_type':'raw_ohlcv','bar_unit':'hand'}))
        except Exception as exc:chart_errors.append(f'{s["code"]}: {exc}')
        if i%20==0:LOG.info('历史K线缓存%d/100：已有命中%d，单日追加%d，缺口补齐%d，首次下载%d',
                           i,counts['replay_hit'],counts['replay_append'],counts['replay_catchup'],counts['replay_new'])
    if len(staged)/100<min_coverage:
        raise RuntimeError(f'回放K线完整率{len(staged)}/100不足{min_coverage:.0%}，样例{chart_errors[:4]}')
    t_history=perf_counter()
    completed={s['code'] for s,_ in staged}
    public=[{**s,'has_history':s['code'] in completed,
             'history_url':f'data/stocks/{s["code"]}.json' if s['code'] in completed else None} for s in selected]
    manifest={'schema_version':1,'is_demo':False,'asof':asof.isoformat(),
              'generated_at':datetime.now(SH_TZ).isoformat(timespec='seconds'),
              'rps_window':rps_window,'rps_universe':len(rows),'rps_coverage':round(coverage,4),
              'rps_method':f'61个前复权收盘价滚动计算RPS{rps_window}，全市场百分位排名',
              'candidate_count':eligible_count,'top_before_filter_count':100,
              'stock_count':100,'history_ready_count':len(staged),'stocks':public,
              'warnings':(['增量缓存由行情数据验证生成，不进行缺失值猜测']+
                          ([f'{len(chart_errors)}只历史回放未就绪'] if chart_errors else []))}
    # Assemble completely outside website before publishing a new manifest.
    temp=outdir.parent/(outdir.name+'_stage')
    if temp.exists():shutil.rmtree(temp)
    try:
        (temp/'stocks').mkdir(parents=True)
        for s,doc in staged: atomic_json(temp/'stocks'/f'{s["code"]}.json',doc)
        atomic_json(temp/'manifest.json',manifest)
        if outdir.exists():shutil.rmtree(outdir)
        temp.rename(outdir)
    finally:
        if temp.exists():shutil.rmtree(temp)
    cache.save()
    LOG.info('更新成功%s: 股票100只，缓存新增%d只、当日追加%d只、复权/缺口修复%d只；历史回放增量%d只、全量%d只',
             asof,stats['bootstrap'],stats['append_one'],stats['repair'],counts['replay_append'],counts['replay_new'])
    LOG.info('耗时（秒）：批量报价%.1f，61日缓存%.1f，RPS计算%.1f，回放缓存%.1f，合计%.1f',
             t_quotes-t0,t_roll-t_quotes,t_rank-t_roll,t_history-t_rank,perf_counter()-t0)
    return manifest


def main():
    p=argparse.ArgumentParser(description='v2.2: 白天/晚间统一增量缓存与可续建的历史初始化')
    p.add_argument('--output',default='web/data')
    p.add_argument('--cache-dir',default='.market-cache-state')
    p.add_argument('--rps-window',type=int,default=60,choices=[60])
    p.add_argument('--date',default=None)
    p.add_argument('--min-coverage',type=float,default=.98)
    p.add_argument('--workers',type=int,default=2)
    p.add_argument('--bootstrap-max-minutes',type=int,default=75)
    args=p.parse_args()
    logging.basicConfig(level=logging.INFO,format='%(asctime)s %(levelname)s %(message)s')
    provider=AkshareProvider(delay=.15)
    now=datetime.now(SH_TZ)
    days=provider.trading_days()
    asof=date.fromisoformat(args.date) if args.date else choose_asof(days,now)
    if asof>now.date():raise RuntimeError('不允许选择未来交易日')
    if asof not in days:raise RuntimeError(f'{asof}不是确认的A股交易日')
    if asof==now.date() and now.hour<18:raise RuntimeError('今日收盘前不能更新今日行情')
    LOG.info('目标日=%s；北京时间=%s；持久缓存=%s',asof,now.isoformat(timespec='seconds'),args.cache_dir)
    cache=MarketCache(args.cache_dir,args.rps_window)
    historical_target=asof!=now.date()
    LOG.info('更新模式=%s；已加载RPS历史缓存%d只；无缓存时按100只分组建立并保存断点',
             '历史交易日增量/初始化' if historical_target else '当日批量报价增量/初始化',
             len(cache.records))
    try:
        # Always enter the SAME persistent cache pipeline. In particular, a
        # daytime T-1 cold start must not invoke legacy_update() and discard all
        # completed download work. Historical T-1 quotes are fetched from actual
        # dated OHLCV, not from the current-day batch spot endpoint.
        run(provider,Path(args.output),cache,asof,days,args.rps_window,args.min_coverage,
            bootstrap_workers=args.workers,
            bootstrap_max_seconds=args.bootstrap_max_minutes*60,
            historical_target=historical_target)
    finally:
        cache.save()  # preserve verified partial progress even if bootstrap fails

if __name__=='__main__':main()
