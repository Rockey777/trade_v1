import unittest
from datetime import date, datetime
from zoneinfo import ZoneInfo
from types import SimpleNamespace
import pandas as pd
from backend.providers import AkshareProvider

TZ=ZoneInfo('Asia/Shanghai')
T=datetime(2026,10,29,20,40,tzinfo=TZ)

class Rows:
    error_code='0';error_msg='ok';fields=['calendar_date','is_trading_day']
    def __init__(self, values):self.values=iter(values)
    def next(self):
        try:
            self.current=next(self.values)
            return True
        except StopIteration:return False
    def get_row_data(self):return self.current

class MockBao:
    def __init__(self, trading='1', works=True):self.trading=trading;self.works=works
    def login(self):
        if not self.works:raise RuntimeError('network down')
        return SimpleNamespace(error_code='0',error_msg='ok')
    def logout(self):pass
    def query_trade_dates(self, **kwargs):return Rows([['2026-10-28','1'],['2026-10-29',self.trading]])

class MockAk:
    def __init__(self, dates):self.dates=dates
    def tool_trade_date_hist_sina(self):return pd.DataFrame({'trade_date':self.dates})

class CalendarTests(unittest.TestCase):
    def make(self, sina, bao):return AkshareProvider(ak=MockAk(sina), baostock=bao, delay=0, retries=1)
    def test_stale_sina_fixed_by_baostock(self):
        p=self.make(['2026-10-28'],MockBao('1'))
        self.assertIn(date(2026,10,29),p.trading_days(now=T))
    def test_baostock_explicit_holiday(self):
        p=self.make(['2026-10-28','2026-10-29'],MockBao('0'))
        self.assertNotIn(date(2026,10,29),p.trading_days(now=T))
    def test_missing_and_bao_down_fails_closed(self):
        p=self.make(['2026-10-28'],MockBao(works=False))
        with self.assertRaisesRegex(RuntimeError,'拒绝静默回退'):
            p.trading_days(now=T)
    def test_sina_current_and_bao_down_still_works(self):
        p=self.make(['2026-10-28','2026-10-29'],MockBao(works=False))
        self.assertIn(date(2026,10,29),p.trading_days(now=T))

if __name__=='__main__':unittest.main()
