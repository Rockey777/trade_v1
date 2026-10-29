"""Generate clearly labeled synthetic data for offline UI testing, NEVER real market data."""
from __future__ import annotations
import math
import random
from datetime import date,timedelta
from pathlib import Path
from backend.update import atomic_json

def make(out: Path):
    rng=random.Random(20261029)
    asof=date(2026,10,29)
    trading=[]
    d=asof-timedelta(days=1600)
    while d<=asof:
        if d.weekday()<5:trading.append(d)
        d+=timedelta(days=1)
    dates=trading[-1001:]
    names=[('600001','示例甲'),('000002','示例乙'),('002003','示例丙'),('601005','示例丁'),('603006','示例戊')]
    stocks=[]
    for i,(code,name) in enumerate(names):
        last=5.5+i*1.3;bars=[]
        for idx,day in enumerate(dates):
            gap=rng.gauss(0,.006);o=max(.8,last*(1+gap));c=max(.8,o*(1+rng.gauss(.00015+.00008*i,.017)));high=max(o,c)*(1+abs(rng.gauss(.006,.004)));low=min(o,c)*(1-abs(rng.gauss(.006,.004)));vol=round(1e5+rng.random()*9e5)
            bars.append({'date':day.isoformat(),'o':round(o,3),'h':round(high,3),'l':round(low,3),'c':round(c,3),'v':vol});last=c
        today=bars[-1];previous=bars[-2];ret=(today['c']/bars[-61]['c']-1)*100
        stocks.append({'code':code,'name':name,'price':today['c'],'change_pct':round((today['c']/previous['c']-1)*100,2),'listing_date':'2010-01-01','return_pct':round(ret,2),'rank':i+1,'rps':round(99-i*1.35,2),'rps_window':60,'has_history':True,'history_url':f'data/stocks/{code}.json'})
        atomic_json(out/'stocks'/f'{code}.json',{'code':code,'name':name,'asof':asof.isoformat(),'bars':bars,'data_type':'synthetic_demo','bar_unit':'hand'})
    atomic_json(out/'manifest.json',{'schema_version':1,'is_demo':True,'asof':asof.isoformat(),'generated_at':'2026-10-29T19:00:00+08:00','rps_window':60,'rps_universe':100,'rps_coverage':1,'rps_method':'演示数据，没有使用真实行情','candidate_count':2400,'top_before_filter_count':100,'stock_count':len(stocks),'history_ready_count':len(stocks),'stocks':stocks,'warnings':['示例股票和行情均为随机生成，请运行backend.update更新为真实数据']})

if __name__=='__main__':make(Path('web/data'))
