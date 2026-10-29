import unittest
import tempfile
from pathlib import Path
from datetime import date,timedelta
import pandas as pd
from backend.update import update
from backend.providers import normalize_list_date

D=date(2026,10,29)
class Provider:
    def is_trade_day(self,asof):return True
    def spot(self):
        # 115 eligible candidates, 5 high-return stocks exceed +/-5% today.
        rows=[]
        for i in range(115):
            rows.append({'代码':f'{600000+i}','名称':f'股票{i}','最新价':10.,'涨跌幅':6. if i>=112 else (-6. if i>=110 else 1.),'60日涨跌幅':float(i)})
        return pd.DataFrame(rows)
    def listings(self):return {f'{600000+i}':(f'股票{i}',date(2010,1,1)) for i in range(115)}
    def history(self,code,start,end,adjust=''):
        idx=pd.bdate_range(end=end,periods=240)
        return pd.DataFrame({'日期':idx,'开盘':[10.]*len(idx),'最高':[10.2]*len(idx),'最低':[9.8]*len(idx),'收盘':[10.]*len(idx),'成交量':[123000.]*len(idx)})

class UpdateTests(unittest.TestCase):
    def test_complete_pipeline(self):
        with tempfile.TemporaryDirectory() as td:
            result=update(Provider(),Path(td),D,allow_old=True)
            self.assertEqual(result['top_before_filter_count'],100)
            self.assertEqual(result['stock_count'],95)
            self.assertEqual(result['rps_universe'],115)
            self.assertEqual(result['stocks'][0]['code'],'600109')
            self.assertEqual(result['stocks'][0]['rps'],round(100*(115-6)/(114),2))
            self.assertTrue((Path(td)/'stocks'/'600109.json').exists())
            self.assertEqual((Path(td)/'manifest.json').exists(),True)

    def test_listing_date_compact_integer(self):
        self.assertEqual(normalize_list_date(20261001),date(2026,10,1))
        self.assertEqual(normalize_list_date('20261001'),date(2026,10,1))

    def test_holiday_refuses_to_publish_demo(self):
        class HolidayProvider(Provider):
            def is_trade_day(self, asof):return False
        with tempfile.TemporaryDirectory() as td:
            with self.assertRaises(RuntimeError):update(HolidayProvider(),Path(td),D,allow_old=True)
            self.assertFalse((Path(td)/'manifest.json').exists())

    def test_no_publish_on_missing_data(self):
        class BadProvider(Provider):
            def history(self,*a,**k):raise RuntimeError('down')
        with tempfile.TemporaryDirectory() as td:
            with self.assertRaises(RuntimeError):update(BadProvider(),Path(td),D,allow_old=True)
            self.assertFalse((Path(td)/'manifest.json').exists())
if __name__=='__main__':unittest.main()
