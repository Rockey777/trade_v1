"""Scheduled snapshot generator: real data only, failures leave published site untouched."""
from __future__ import annotations
import argparse
import json
import logging
import math
import os
from datetime import datetime, timedelta, date
from pathlib import Path
from zoneinfo import ZoneInfo
import pandas as pd
from backend.core import eligible, ranking, compute_return
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


def update(provider, outdir: Path, asof: date, rps_window: int = 60,
           min_coverage: float = .98, allow_old: bool = False, skip_holiday: bool = True) -> dict:
    if skip_holiday and not provider.is_trade_day(asof):
        raise RuntimeError(f'{asof} 不是A股交易日：拒绝发布，保留当前网站数据')
    if not allow_old and datetime.now(SH_TZ).date() == asof and datetime.now(SH_TZ).hour < 18:
        raise RuntimeError('请在北京时间18:00之后运行，避免使用盘中未完成行情')
    spot = provider.spot()
    listings = provider.listings()
    spot_codes = {str(c).zfill(6) for c in spot['代码']}
    missing_listings = len([c for c in spot_codes if c not in listings])
    LOG.info('现价股票%d，上交所/深交所上市记录%d，未匹配上市记录%d', len(spot), len(listings), missing_listings)
    likely = [str(r['代码']).zfill(6) for _,r in spot.iterrows()
              if str(r['代码']).zfill(6).startswith(('600','601','603','605','000','001','002'))]
    matching = sum(c in listings for c in likely) / max(1,len(likely))
    if matching < .96:
        raise RuntimeError(f'交易所上市日期匹配覆盖率异常：{matching:.1%}，拒绝生成RPS')
    candidates = []
    for _, item in spot.iterrows():
        code = str(item['代码']).strip().zfill(6)
        if code not in listings: continue
        name = str(item['名称']).strip()
        listed = listings[code][1]
        price, change = finite(item['最新价']), finite(item['涨跌幅'])
        if price is None or change is None: continue
        if eligible(code, name, price, listed, asof):
            candidates.append({'code':code,'name':name,'price':round(price,3),
                               'change_pct':round(change,3), 'listing_date':listed.isoformat(),
                               'rps_source':'provider_60d' if rps_window == 60 else 'qfq_history',
                               'provider_return':finite(item.get('60日涨跌幅'))})
    LOG.info('满足主板/上市1000天/非ST/不高于15元的股票%d',len(candidates))
    if len(candidates) < 20:
        raise RuntimeError('候选股票异常稀少，拒绝发布')

    # 60 days is available for the entire A-share market in a single spot call.
    # Other RPS periods use adjusted history for *every* candidate, slower.
    asof_key = asof.strftime('%Y%m%d')
    history_start = (asof - timedelta(days=7*365)).strftime('%Y%m%d')
    rows = []
    failed = []
    for index, c in enumerate(candidates, 1):
        try:
            if rps_window == 60:
                ret = c['provider_return']
                if ret is None:
                    raise ValueError('缺失60日涨幅')
            else:
                adjusted = provider.history(c['code'], (asof - timedelta(days=max(520, rps_window*2))).strftime('%Y%m%d'), asof_key, 'qfq')
                if adjusted.iloc[-1]['日期'].date() != asof:
                    raise ValueError('今日无日线数据')
                ret = compute_return(adjusted['收盘'].tolist(), rps_window)
            rows.append({k:v for k,v in c.items() if k != 'provider_return'} | {'return_pct':round(ret,5)})
        except Exception as exc:
            failed.append(f"{c['code']}: {exc}")
        if rps_window != 60 and index % 50 == 0:
            LOG.info('RPS历史计算：%d/%d', index, len(candidates))
    coverage = len(rows)/len(candidates)
    LOG.info('RPS覆盖%d/%d(%.1f%%)',len(rows),len(candidates),coverage*100)
    if coverage < min_coverage:
        raise RuntimeError(f'RPS数据覆盖率 {coverage:.1%} 低于阈值 {min_coverage:.0%}，拒绝发布；失败样例 {failed[:5]}')
    top, selected = ranking(rows, rps_window)
    if not selected:
        raise RuntimeError('前100名过滤后为空，未生成数据')

    # Must stage all files before replacing public data. Failed downloads are never
    # silently hidden: threshold enforces coverage of surviving selected stocks.
    staged = []
    chart_failed = []
    for i, stock in enumerate(selected, 1):
        try:
            df = provider.history(stock['code'], history_start, asof_key, '')
            bars = render_bars(df)
            if len(bars) < 120 or bars[-1]['date'] != asof.isoformat():
                raise ValueError('最近日线缺失或可用历史不足120根')
            if abs(bars[-1]['c']-stock['price']) > max(.03,.004*stock['price']):
                raise ValueError(f"行情现价{stock['price']}与收盘日线{bars[-1]['c']}不一致")
            # Limit the complete simulation dataset to 1001 bars (T-1000 ... T).
            bars = bars[-1001:]
            if len(bars) <= 60:
                raise ValueError('缺少60日回放区间')
            doc = {'code':stock['code'], 'name':stock['name'], 'asof':asof.isoformat(),
                   'bars':bars, 'data_type':'raw_ohlcv', 'bar_unit':'hand'}
            staged.append((stock, doc))
        except Exception as exc:
            chart_failed.append(f"{stock['code']}: {exc}")
        if i % 10 == 0:
            LOG.info('回放历史下载：%d/%d，成功%d',i,len(selected),len(staged))
    if len(staged) / len(selected) < min_coverage:
        raise RuntimeError(f'入选股票K线覆盖率不足：{len(staged)}/{len(selected)}，失败样例 {chart_failed[:5]}')
    if chart_failed:
        LOG.warning('已入选但K线下载失败%d只，名单中标注不可回放：%s',len(chart_failed),chart_failed[:5])

    completed = {s['code'] for s,_ in staged}
    public = [{**stock,'has_history':stock['code'] in completed,
               'history_url':f"data/stocks/{stock['code']}.json" if stock['code'] in completed else None}
              for stock in selected]
    manifest = {'schema_version':1, 'is_demo':False, 'asof':asof.isoformat(),
                'generated_at':datetime.now(SH_TZ).isoformat(timespec='seconds'),
                'rps_window':rps_window, 'rps_universe':len(rows), 'rps_coverage':round(coverage,4),
                'rps_method':'东财60日涨跌幅全市场百分位排名' if rps_window==60 else f'前复权{rps_window}交易日收益率百分位排名',
                'candidate_count':len(candidates), 'top_before_filter_count':len(top),
                'stock_count':len(public), 'history_ready_count':len(completed), 'stocks':public,
                'warnings':(['行情API获取失败时不自动补造历史数据'] + ([f'{len(chart_failed)}只股票历史K线下载失败，已标记不可回放'] if chart_failed else []))}
    for stock, doc in staged:
        atomic_json(outdir / 'stocks' / f"{stock['code']}.json",doc)
    atomic_json(outdir / 'manifest.json', manifest)  # Publish manifest LAST
    LOG.info('写入%s, %d只股票有完整回放数据',outdir,len(staged))
    return manifest


def main():
    p=argparse.ArgumentParser(description='A股RPS每日更新及PWA静态数据生成')
    p.add_argument('--output',default='web/data')
    p.add_argument('--rps-window',type=int,default=60,choices=[20,60,120,250])
    p.add_argument('--date',default=None,help='YYYY-MM-DD 默认北京时间今日')
    p.add_argument('--min-coverage',type=float,default=.98)
    p.add_argument('--force',action='store_true',help='测试模式：允许非交易日/非收盘后拉取（不保证当日快照完整）')
    p.add_argument('--sleep',type=float,default=.15,help='AKShare每次请求后的间隔（秒）')
    args=p.parse_args()
    logging.basicConfig(level=logging.INFO,format='%(asctime)s %(levelname)s %(message)s')
    asof=date.fromisoformat(args.date) if args.date else datetime.now(SH_TZ).date()
    provider=AkshareProvider(delay=args.sleep)
    update(provider,Path(args.output),asof,args.rps_window,args.min_coverage,args.force,not args.force)

if __name__=='__main__':main()
