import unittest
from datetime import date, timedelta
from backend.core import is_main_board,is_st,eligible,ranking,compute_return

class TestCore(unittest.TestCase):
    def test_market(self):
        for code in ['000001','001001','002001','600000','601001','603001','605001']:
            self.assertTrue(is_main_board(code))
        for code in ['300001','301001','688001','689001','430001','920001','200001','900001']:
            self.assertFalse(is_main_board(code))

    def test_st_and_age(self):
        asof=date(2026,10,29)
        self.assertTrue(is_st('ST测试'))
        self.assertTrue(is_st('*ST测试'))
        self.assertTrue(is_st('＊ＳＴ测试'))
        self.assertTrue(is_st('S*ST测试'))
        self.assertTrue(is_st('某某退市'))
        self.assertFalse(is_st('正常公司'))
        self.assertTrue(eligible('600001','测试',15.0,asof-timedelta(days=1000),asof))
        self.assertFalse(eligible('600001','测试',15.01,asof-timedelta(days=1000),asof))
        self.assertFalse(eligible('600001','测试',15,asof-timedelta(days=999),asof))

    def test_ranking_then_change_filter(self):
        rows=[{'code':f'{i:06d}','return_pct':i,'change_pct':(5.1 if i==104 else (-5.1 if i==103 else 0))} for i in range(1,106)]
        top,remaining=ranking(rows,60,100)
        self.assertEqual(len(top),100)
        self.assertEqual(len(remaining),98)
        self.assertEqual(top[0]['rps'],100)
        self.assertEqual(top[0]['code'],'000105')
        self.assertEqual(len([x for x in remaining if x['return_pct']==4]),0)

    def test_return(self):
        self.assertAlmostEqual(compute_return([10,12,15],2),50)
        with self.assertRaises(ValueError):compute_return([10,12],2)
if __name__=='__main__':unittest.main()
