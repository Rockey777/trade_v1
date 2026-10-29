"""Scheduled snapshot generator: real data only, failures leave published site untouched."""
from __future__ import annotations
import argparse
import json
import logging
import math
import os
from datetime import datetime, timedelta, date
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from zoneinfo import ZoneInfo
import pandas as pd
from backend.core import eligible, ranking, compute_return, is_st, is_main_board
from backend.providers import AkshareProvider

LOG = logging.getLogger('astock')
SH_TZ = ZoneInfo('Asia/Shanghai')


def finite(value) -> float | None:
    try:
        v = float(value)
        return v if math.isfinite(v) else None
    except (TypeError, ValueError):
        return None


def atomic_json(path: Path, obj: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + '.tmp')
    with temp.open('w', encoding='utf8') as f:
        json.dump(obj, f, ensure_ascii=False, separators=(',', ':'), allow_nan=False)
    os.replace(temp, path)


def render_bars(df: pd.DataFrame) -> list[dict]:
    bars = []
    for _, r in df.iterrows():
        o,h,l,c,v = (float(r[k]) for k in ('开盘','最高','最低','收盘','成交量'))
        if min(o,h,l,c) <= 0 or v < 0 or l > min(o,c) or h < max(o,c):
            continue
        bars.append({'date':r['日期'].strftime('%Y-%m-%d'), 'o':round(o,4), 'h':round(h,4),
                     'l':round(l,4), 'c':round(c,4), 'v':round(v,2)})
    return bars


def choose_asof(trading_days: set[date], now: datetime) -> date:
    """Use today's completed close only at/after 18:00 Beijing time.

    Before that, return the most recent *previous* exchange trading date.
    On holidays/weekends return the most recent trading date regardless of hour.
    """
    local = now.astimezone(SH_TZ)
    cutoff_passed = (local.hour >= 18)
    eligible_days = [d for d in trading_days
                     if d < local.date() or (d == local.date() and cutoff_passed)]
    if not eligible_days:
        raise RuntimeError('交易日历中不存在已完成的交易日，请检查交易日历接口')
    return max(eligible_days)


def historical_candidates(provider, listings: dict, asof: date, rps_window: int,
                          min_coverage: float, workers: int = 3) -> list[dict]:
    """Point-in-time prices + RPS for a prior date. Never use LIVE spot fields.

    First acquire raw closes (exact asof price and day-on-day change), then use
    QFQ historical closes for RPS; source is slow because AKShare does not offer
    a full-market historical snapshot with both prior-close and 60d return.
    """
    listed = [(code, name, day) for code, (name, day) in listings.items()
              if day <= asof and (asof-day).days >= 1000
              and not is_st(name) and is_main_board(code)]
    LOG.info('历史日期%s：需检查主板老股%d只的历史收盘价（首次回退会比晚间快照慢）',asof,len(listed))
    if len(listed) < 20:
        raise RuntimeError('交易所主板上市记录异常稀少，拒绝生成历史快照')

    # 45 calendar days are sufficient for previous-day comparison even near
    # extended holidays; no adjusted price is used for the <= 15 RMB filter.
    raw_start=(asof-timedelta(days=45)).strftime('%Y%m%d')
    end=asof.strftime('%Y%m%d')
    qfq_start=(asof-timedelta(days=max(160, int(rps_window*1.9)+30))).strftime('%Y%m%d')

    def fetch(item):
        code,name,day=item
        try:
            raw=provider.history(code, raw_start, end, '')
            if len(raw)<2 or raw.iloc[-1]['日期'].date()!=asof:
                return ('inactive',code,'本交易日没有完整日K线',None)
            close=float(raw.iloc[-1]['收盘'])
            prior=float(raw.iloc[-2]['收盘'])
            if close <= 0 or prior <=0 or not math.isfinite(close) or not math.isfinite(prior):
                return ('invalid',code,'无效收盘价',None)
            if close>15:
                return ('not_eligible',code,'当日收盘价高于15元',None)
            qfq=provider.history(code,qfq_start,end,'qfq')
            if qfq.iloc[-1]['日期'].date()!=asof:
                return ('invalid',code,'前复权行情缺少目标交易日',None)
            ret=compute_return(qfq['收盘'].tolist(),rps_window)
            return ('ok',code,None,{
                'code':code,'name':name,'price':round(close,3),
                'change_pct':round((close/prior-1)*100,3),
                'listing_date':day.isoformat(),
                'rps_source':'qfq_history', 'return_pct':round(ret,5)})
        except Exception as exc:
            return ('error',code,str(exc),None)

    rows=[]; errors=[]; inactive=0; too_expensive=0
    # Concurrency makes the one-off previous-day bootstrap usable. Keep workers
    # low to reduce requests per second and respect the upstream provider.
    with ThreadPoolExecutor(max_workers=max(1,min(6,workers))) as pool:
        futures={pool.submit(fetch, item):item[0] for item in listed}
        for num, future in enumerate(as_completed(futures),1):
            kind,code,reason,item=future.result()
            if kind=='ok':rows.append(item)
            elif kind=='inactive':inactive+=1
            elif kind=='not_eligible':too_expensive+=1
            else:errors.append(f'{code}: {reason}')
            if num%100==0 or num==len(listed):
                LOG.info('历史日期行情检查%d/%d，符合价格条件且RPS有效%d，非交易/停牌%d，请求/数据错误%d；来源统计=%s',
                         num,len(listed),len(rows),inactive,len(errors),
                         provider.history_stats() if hasattr(provider,'history_stats') else {})

    # Inactive equities are not considered API failures. Reject substantial
    # actual request/data errors rather than silently ranking a partial market.
    effective = len(listed)-inactive
    coverage = 1-len(errors)/max(1,effective)
    if coverage<min_coverage:
        raise RuntimeError(f'历史市场行情接口完整率{coverage:.1%}低于{min_coverage:.0%}：{errors[:5]}')
    LOG.info('历史快照%s：价格<=15且RPS完整%d只，价格超限%d只，暂停交易%d只',
             asof,len(rows),too_expensive,inactive)
    return rows


def update(provider, outdir: Path, asof: date, rps_window: int = 60,
           min_coverage: float = .98, allow_old: bool = False, skip_holiday: bool = True,
           historical: bool = False, historical_workers: int = 3) -> dict:
    if skip_holiday and not provider.is_trade_day(asof):
        raise RuntimeError(f'{asof} 不是A股交易日：拒绝发布，保留当前网站数据')
    if not historical and not allow_old and datetime.now(SH_TZ).date() == asof and datetime.now(SH_TZ).hour < 18:
        raise RuntimeError('当日18:00前不可使用盘中实时行情；请通过自动日期选择回退到上一交易日')
    listings = provider.listings()
    if historical:
        # Historical asof must be computed from historical bars. Current live
        # 60-day performance and price belong to a different point in time.
        rows=historical_candidates(provider,listings,asof,rps_window,
                                   min_coverage,historical_workers)
        candidates_count=len(rows)
        coverage=1.0  # historical_candidates checks API reliability separately
    else:
        spot=provider.spot()
        spot_codes={str(c).zfill(6) for c in spot['代码']}
        missing_listings=len([c for c in spot_codes if c not in listings])
        LOG.info('现价股票%d，上交所/深交所上市记录%d，未匹配上市记录%d',len(spot),len(listings),missing_listings)
        likely=[str(r['代码']).zfill(6) for _,r in spot.iterrows()
                if is_main_board(str(r['代码']).zfill(6))]
        matching=sum(c in listings for c in likely)/max(1,len(likely))
        if matching<.96:
            raise RuntimeError(f'交易所上市日期匹配覆盖率异常：{matching:.1%}，拒绝生成RPS')
        candidates=[]
        for _,item in spot.iterrows():
            code=str(item['代码']).strip().zfill(6)
            if code not in listings:continue
            name=str(item['名称']).strip()
            listed=listings[code][1]
            price,change=finite(item['最新价']),finite(item['涨跌幅'])
            if price is None or change is None:continue
            if eligible(code,name,price,listed,asof):
                candidates.append({'code':code,'name':name,'price':round(price,3),
                                   'change_pct':round(change,3),'listing_date':listed.isoformat(),
                                   'rps_source':'provider_60d' if rps_window==60 else 'qfq_history',
                                   'provider_return':finite(item.get('60日涨跌幅'))})
        LOG.info('满足主板/上市1000天/非ST/不高于15元的股票%d',len(candidates))
        if len(candidates)<20:
            raise RuntimeError('候选股票异常稀少，拒绝发布')

        asof_key=asof.strftime('%Y%m%d')
        rows=[];failed=[]
        for index,c in enumerate(candidates,1):
            try:
                if rps_window==60:
                    ret=c['provider_return']
                    if ret is None:raise ValueError('缺失60日涨幅')
                else:
                    adjusted=provider.history(c['code'],(asof-timedelta(days=max(520,rps_window*2))).strftime('%Y%m%d'),asof_key,'qfq')
                    if adjusted.iloc[-1]['日期'].date()!=asof:raise ValueError('当日无日线数据')
                    ret=compute_return(adjusted['收盘'].tolist(),rps_window)
                rows.append({k:v for k,v in c.items() if k!='provider_return'} | {'return_pct':round(ret,5)})
            except Exception as exc:
                failed.append(f"{c['code']}: {exc}")
            if rps_window!=60 and index%50==0:
                LOG.info('RPS历史计算：%d/%d',index,len(candidates))
        candidates_count=len(candidates)
        coverage=len(rows)/len(candidates)
        LOG.info('RPS覆盖%d/%d(%.1f%%)',len(rows),len(candidates),coverage*100)
        if coverage<min_coverage:
            raise RuntimeError(f'RPS数据覆盖率{coverage:.1%}低于阈值{min_coverage:.0%}；失败样例{failed[:5]}')
    if len(rows)<20:
        raise RuntimeError('RPS样本异常稀少，拒绝发布')
    top,selected=ranking(rows,rps_window)
    if not selected:
        raise RuntimeError('前100名过滤后为空，未生成数据')
    history_start=(asof-timedelta(days=7*365)).strftime('%Y%m%d')
    asof_key=asof.strftime('%Y%m%d')
    staged=[];chart_failed=[]
    for i,stock in enumerate(selected,1):
        try:
            df=provider.history(stock['code'],history_start,asof_key,'')
            bars=render_bars(df)
            if len(bars)<120 or bars[-1]['date']!=asof.isoformat():
                raise ValueError('最近日线缺失或可用历史不足120根')
            if abs(bars[-1]['c']-stock['price'])>max(.03,.004*stock['price']):
                raise ValueError(f"快照收盘价{stock['price']}与历史日线{bars[-1]['c']}不一致")
            bars=bars[-1001:]
            if len(bars)<=60:raise ValueError('缺少60日回放区间')
            doc={'code':stock['code'],'name':stock['name'],'asof':asof.isoformat(),
                 'bars':bars,'data_type':'raw_ohlcv','bar_unit':'hand'}
            staged.append((stock,doc))
        except Exception as exc:
            chart_failed.append(f"{stock['code']}: {exc}")
        if i%10==0:
            LOG.info('回放历史下载：%d/%d，成功%d',i,len(selected),len(staged))
    if len(staged)/len(selected)<min_coverage:
        raise RuntimeError(f'入选股票K线覆盖率不足：{len(staged)}/{len(selected)}，失败样例{chart_failed[:5]}')
    if chart_failed:
        LOG.warning('已入选但K线下载失败%d只，名单中标注不可回放：%s',len(chart_failed),chart_failed[:5])
    completed={s['code'] for s,_ in staged}
    public=[{**stock,'has_history':stock['code'] in completed,
             'history_url':f"data/stocks/{stock['code']}.json" if stock['code'] in completed else None}
            for stock in selected]
    manifest={'schema_version':1,'is_demo':False,'asof':asof.isoformat(),
              'generated_at':datetime.now(SH_TZ).isoformat(timespec='seconds'),
              'rps_window':rps_window,'rps_universe':len(rows),'rps_coverage':round(coverage,4),
              'rps_method':(f'截至{asof.isoformat()}的前复权{rps_window}交易日收益率百分位排名'
                            if historical or rps_window!=60 else '东财60日涨跌幅全市场百分位排名'),
              'candidate_count':candidates_count,'top_before_filter_count':len(top),
              'stock_count':len(public),'history_ready_count':len(completed),'stocks':public,
              'warnings':(['行情API获取失败时不自动补造历史数据']+
                          ([f'{len(chart_failed)}只股票历史K线下载失败，已标记不可回放'] if chart_failed else []))}
    for stock,doc in staged:
        atomic_json(outdir/'stocks'/f"{stock['code']}.json",doc)
    atomic_json(outdir/'manifest.json',manifest)
    LOG.info('写入%s：交易日%s，有回放数据%d只',outdir,asof,len(staged))
    return manifest


def main():
    p=argparse.ArgumentParser(description='A股RPS每日更新及PWA静态数据生成')
    p.add_argument('--output',default='web/data')
    p.add_argument('--rps-window',type=int,default=60,choices=[20,60,120,250])
    p.add_argument('--date',default=None,help='可指定历史交易日期YYYY-MM-DD；默认自动选择最近已完成交易日')
    p.add_argument('--min-coverage',type=float,default=.98)
    p.add_argument('--force',action='store_true',help='仅测试：允许当日18点前强制抓取盘中数据')
    p.add_argument('--sleep',type=float,default=.15,help='AKShare每次请求后的间隔（秒）')
    p.add_argument('--historical-workers',type=int,default=2,help='历史快照下载并发，默认2（1到6），减少数据源压力')
    args=p.parse_args()
    logging.basicConfig(level=logging.INFO,format='%(asctime)s %(levelname)s %(message)s')
    now=datetime.now(SH_TZ)
    provider=AkshareProvider(delay=args.sleep)
    asof=date.fromisoformat(args.date) if args.date else choose_asof(provider.trading_days(),now)
    if asof>now.date():raise ValueError('不可使用未来日期')
    historical=asof!=now.date()
    LOG.info('当前北京时间%s；选股基准交易日%s；数据模式%s',
             now.isoformat(timespec='seconds'),asof,'历史收盘快照' if historical else '今日收盘快照')
    if asof==now.date() and now.hour<18 and not args.force:
        raise RuntimeError('指定了今日且未满18:00；请去掉--date使用自动回退')
    # Treat explicitly requested old trading days as historical as well.
    update(provider,Path(args.output),asof,args.rps_window,args.min_coverage,
           args.force,not args.force,historical,args.historical_workers)

if __name__=='__main__':main()
