import unittest
from datetime import date
from types import SimpleNamespace
import pandas as pd
from backend.providers import AkshareProvider, normalize_history


def tx_data():
    return pd.DataFrame([
        {'date':'2026-10-27','open':'8.8','high':'9.3','low':'8.7',
         'close':'9.1','amount':'1234'},
        {'date':'2026-10-28','open':'9.1','high':'9.5','low':'9',
         'close':'9.4','amount':'1421'},
        {'date':'2026-10-29','open':'9.4','high':'9.6','low':'9.2',
         'close':'9.5','amount':'1490'},
    ])

class ProviderFallbackTest(unittest.TestCase):
    def test_tx_canonical_columns_and_strict_date_cutoff(self):
        df=normalize_history(tx_data(),'600006','腾讯','20261027','20261028')
        self.assertEqual(len(df),2)
        self.assertEqual(float(df.iloc[-1]['收盘']),9.4)
        self.assertEqual(float(df.iloc[-1]['成交量']),1421)
        self.assertEqual(df.iloc[-1]['日期'].date(),date(2026,10,28))

    def test_primary_tencent_success_does_not_call_eastmoney(self):
        def no_em(**kw):
            raise AssertionError('TX works; Eastmoney should not be called')
        fake=SimpleNamespace(stock_zh_a_hist_tx=lambda **kw:tx_data(),stock_zh_a_hist=no_em)
        p=AkshareProvider(ak=fake,delay=0,history_attempts=1)
        df=p.history('600006','20261027','20261028')
        self.assertEqual(len(df),2)
        self.assertEqual(p.history_stats(),{'腾讯':1})

    def test_when_tx_fails_eastmoney_is_used(self):
        def no_tx(**kw):
            raise ConnectionError('RemoteDisconnected')
        def em(**kw):
            t=tx_data().iloc[:2].rename(columns={'date':'日期','open':'开盘',
                'high':'最高','low':'最低','close':'收盘','amount':'成交量'})
            return t
        p=AkshareProvider(ak=SimpleNamespace(stock_zh_a_hist_tx=no_tx,
                           stock_zh_a_hist=em),delay=0,history_attempts=1)
        self.assertEqual(len(p.history('600006','20261027','20261028')),2)
        self.assertEqual(p.history_stats()['东方财富'],1)
        self.assertEqual(p.history_stats()['腾讯失败'],1)

    def test_both_fail_raise_instead_of_faking_data(self):
        def bad(**kw):
            raise ConnectionError('connection reset')
        p=AkshareProvider(ak=SimpleNamespace(stock_zh_a_hist_tx=bad,
                           stock_zh_a_hist=bad),delay=0,history_attempts=1)
        with self.assertRaisesRegex(RuntimeError,'双源均失败'):
            p.history('600006','20261027','20261028')

    def test_newer_tx_volume_column(self):
        df=tx_data().drop(columns=['amount']).assign(volume=[1200,1300,1400])
        norm=normalize_history(df,'000001','腾讯','20261027','20261028')
        self.assertEqual(float(norm.iloc[-1]['成交量']),1300)

if __name__=='__main__':unittest.main()
