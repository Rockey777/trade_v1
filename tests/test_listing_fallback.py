"""Offline, deterministic tests: exchange listings failure and BaoStock fallback."""
import unittest
from datetime import date
from types import SimpleNamespace
import pandas as pd
from backend.providers import AkshareProvider, parse_baostock_basic


def bao_rows(count_sh=1450, count_sz=1350):
    rows = []
    for i in range(count_sh):
        prefix = ['600','601','603','605'][i % 4]
        code = f'{prefix}{i//4:03d}'
        rows.append([f'sh.{code}',f'沪股{i}','2010-03-01','','1','1'])
    for i in range(count_sz):
        prefix = ['000','001','002'][i % 3]
        code = f'{prefix}{i//3:03d}'
        rows.append([f'sz.{code}',f'深股{i}','2011-05-01','','1','1'])
    rows += [['sh.688001','科创板','2018-02-04','','1','1'],
             ['sz.300001','创业板','2018-02-04','','1','1'],
             ['sh.600999','停牌退市','2010-03-01','','1','0'],
             ['sh.600998','指数','2010-03-01','','2','1'],
             ['sh.600997','日期错误','INVALID','','1','1']]
    return pd.DataFrame(rows,columns=['code','code_name','ipoDate','outDate','type','status'])


class FakeBaoStock:
    def __init__(self, rows=None, fail=False):
        self.rows = bao_rows() if rows is None else rows
        self.fail = fail
        self.logged_in = False
        self.logged_out = False

    def login(self):
        self.logged_in = True
        return SimpleNamespace(error_code='1' if self.fail else '0',error_msg='no connection' if self.fail else '')

    def query_stock_basic(self):
        rows = self.rows
        class R:
            error_code='0';error_msg='';fields=list(rows.columns)
            def __init__(self):self.i=0
            def next(self):
                if self.i < len(rows):
                    self.i += 1
                    return True
                return False
            def get_row_data(self):return rows.iloc[self.i-1].tolist()
        return R()

    def logout(self):self.logged_out=True


class ListingFallbackTest(unittest.TestCase):
    def test_parse_baostock_excludes_non_mainboard_unlisted_and_invalid_ipo(self):
        rows = bao_rows()
        parsed = parse_baostock_basic(rows)
        self.assertEqual(len(parsed),2800)
        self.assertEqual(parsed['600000'][1],date(2010,3,1))
        self.assertNotIn('688001',parsed)
        self.assertNotIn('600999',parsed)
        self.assertNotIn('600997',parsed)

    def test_fallback_when_exchange_endpoint_returns_not_json(self):
        def bad(**kw):raise ValueError('Expecting value: line 1 column 1 (char 0)')
        bs=FakeBaoStock()
        ak=SimpleNamespace(stock_info_sh_name_code=bad,stock_info_sz_name_code=bad)
        provider=AkshareProvider(ak=ak, baostock=bs, delay=0, retries=1)
        result=provider.listings()
        self.assertEqual(len(result),2800)
        self.assertTrue(bs.logged_in)
        self.assertTrue(bs.logged_out)

    def test_exchange_success_skips_baostock(self):
        sh=pd.DataFrame([{'证券代码':f'{["600","601"][i%2]}{i//2:03d}','证券简称':'SH', '上市日期':'20100101'} for i in range(1200)])
        sz=pd.DataFrame([{'A股代码':f'{["000","001"][i%2]}{i//2:03d}','A股简称':'SZ', 'A股上市日期':'20100101'} for i in range(1200)])
        ak=SimpleNamespace(stock_info_sh_name_code=lambda **kw:sh,
                           stock_info_sz_name_code=lambda **kw:sz)
        bs=FakeBaoStock(fail=True)
        provider=AkshareProvider(ak=ak, baostock=bs, delay=0,retries=1)
        result=provider.listings()
        self.assertEqual(len(result),2400)
        self.assertFalse(bs.logged_in)

    def test_never_accept_incomplete_baostock_list(self):
        def bad(**kw):raise ConnectionError('unavailable')
        ak=SimpleNamespace(stock_info_sh_name_code=bad,stock_info_sz_name_code=bad)
        bs=FakeBaoStock(rows=bao_rows(100,100))
        p=AkshareProvider(ak=ak,baostock=bs,delay=0,retries=1)
        with self.assertRaisesRegex(RuntimeError,'主备接口全部失败'):
            p.listings()
        self.assertTrue(bs.logged_out)

    def test_no_fabrication_when_both_sources_fail(self):
        def bad(**kw):raise ConnectionError('unavailable')
        ak=SimpleNamespace(stock_info_sh_name_code=bad,stock_info_sz_name_code=bad)
        bs=FakeBaoStock(fail=True)
        p=AkshareProvider(ak=ak,baostock=bs,delay=0,retries=1)
        with self.assertRaisesRegex(RuntimeError,'主备接口全部失败'):
            p.listings()

if __name__=='__main__':unittest.main()
