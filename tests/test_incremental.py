from __future__ import annotations
import tempfile
import unittest
from datetime import date, timedelta
from pathlib import Path
import pandas as pd
from backend.batch_quotes import parse_tencent,fetch_batch_quotes
from backend.market_cache import MarketCache
from backend.incremental_update import run

class Provider:
    def __init__(self):
        self.hist_calls=[]
    def listings(self):
        return {f'{600000+i:06d}':(f'股票{i}',date(2015,1,1)) for i in range(110)}
    def history(self,code,start,end,adjust=''):
        self.hist_calls.append((code,start,end,adjust))
        days=pd.bdate_range(end=end,periods=1200)
        factor=int(code[-3:])
        if adjust=='qfq':
            # latest 10.0, older prices drift progressively lower for higher RPS.
            closes=[10.0 - (len(days)-1-j)*factor*.000005 for j in range(len(days))]
        else:
            closes=[10.0]*len(days)
        return pd.DataFrame({'日期':days,'开盘':closes,'最高':[v+.1 for v in closes],
                             '最低':[v-.1 for v in closes], '收盘':closes,
                             '成交量':[10000.0]*len(days)})


def quote(code,target):
    return {'code':code,'name':'股票'+str(int(code[-3:])), 'date':target.isoformat(),
            'o':10.,'h':10.1,'l':9.9,'c':10.,'v':10000.,'prev':10.,'change_pct':0.}

class IncrementalTests(unittest.TestCase):
    def test_parser_rejects_wrong_day(self):
        f=['']*55
        f[1]='示例';f[2]='600001';f[3]='10.00';f[4]='9.90';f[5]='9.95';f[6]='1000';f[30]='20261030151303';f[33]='10.20';f[34]='9.80'
        raw='v_sh600001="'+'~'.join(f)+'";'
        self.assertEqual(parse_tencent(raw,date(2026,10,29)),{})
        self.assertEqual(parse_tencent(raw,date(2026,10,30))['600001']['c'],10.0)

    def test_two_days_roll_once_and_no_full_history_redownload(self):
        first=date(2026,10,29); second=date(2026,10,30)
        dates=set(d.date() for d in pd.bdate_range('2026-01-01','2026-10-30'))
        calls=[]
        def fetch(codes,asof):
            calls.append((len(codes),asof))
            return {c:quote(c,asof) for c in codes}
        p=Provider()
        with tempfile.TemporaryDirectory() as td:
            path=Path(td);cache=MarketCache(path/'cache')
            m=run(p,path/'web'/'data',cache,first,dates,quote_fetcher=fetch)
            self.assertEqual(m['stock_count'],100)
            self.assertEqual(len(p.hist_calls),210) # 110 qfq + 100 replay
            self.assertEqual(len(cache.get_roll('600000')['bars']),61)
            old=cache.get_roll('600100')['bars'][0][0]
            loaded=MarketCache(path/'cache')
            p.hist_calls.clear()
            m2=run(p,path/'web'/'data',loaded,second,dates,quote_fetcher=fetch)
            self.assertEqual(m2['asof'],second.isoformat())
            self.assertEqual(len(p.hist_calls),0) # critical increment property
            self.assertEqual(len(loaded.get_roll('600100')['bars']),61)
            self.assertNotEqual(loaded.get_roll('600100')['bars'][0][0],old)
            s=m2['stocks'][0]
            self.assertTrue(s['has_history'])
            self.assertEqual(len(loaded.get_history(s['code'])),1001)
            self.assertEqual(loaded.get_history(s['code'])[-1]['date'],second.isoformat())
            self.assertEqual(calls,[(110,first),(110,second)])

    def test_rights_discrepancy_forces_qfq_refresh(self):
        d=date(2026,10,29);days=set(pd.bdate_range('2026-01-01','2026-10-30').date)
        p=Provider()
        with tempfile.TemporaryDirectory() as td:
            cache=MarketCache(Path(td)/'cache')
            cache.put_roll('600100',[[str(x.date()),9.] for x in pd.bdate_range(end='2026-10-28',periods=61)],10.)
            from backend.incremental_update import grow_roll
            q=quote('600100',d);q['prev']=9.2
            bars,source=grow_roll(cache,'600100',q,d,days,p)
            self.assertEqual(source,'repair')
            self.assertEqual(bars[-1][0],'2026-10-29')
            self.assertEqual(len(p.hist_calls),1)

if __name__=='__main__':unittest.main()
