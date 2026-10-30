"""Regression tests for v2.2: T-1 daytime must never invoke the legacy updater."""
from __future__ import annotations

import tempfile
import unittest
from unittest.mock import patch, MagicMock
from datetime import date, datetime
from zoneinfo import ZoneInfo
from pathlib import Path

import pandas as pd

from backend.incremental_update import run
from backend.market_cache import MarketCache


class HistoricalProvider:
    def __init__(self, count=110):
        self.count = count
        self.calls = []

    def listings(self):
        return {f'{600000 + n:06d}': (f'样例{n}', date(2017, 1, 1))
                for n in range(self.count)}

    def history(self, code, start, end, adjust=''):
        self.calls.append((code, adjust))
        dates = pd.bdate_range(end=end, periods=1100)
        factor = int(code[-3:])
        if adjust == 'qfq':
            closes = [10 - (len(dates) - 1 - x) * factor * .000003
                      for x in range(len(dates))]
        else:
            closes = [10.] * len(dates)
        # Exact date range clipping mirrors normalized real provider.
        begin = pd.Timestamp(start)
        filtered = [(dt, close) for dt, close in zip(dates, closes) if dt >= begin]
        return pd.DataFrame({
            '日期': [dt for dt, _ in filtered],
            '开盘': [c for _, c in filtered],
            '最高': [c + .1 for _, c in filtered],
            '最低': [c - .1 for _, c in filtered],
            '收盘': [c for _, c in filtered],
            '成交量': [10000.] * len(filtered),
        })


def must_not_fetch_current_quote(codes, target):
    raise AssertionError('历史日期不允许调用只提供实时数据的批量行情接口')


class DaytimeIncrementalTests(unittest.TestCase):
    def setUp(self):
        self.target = date(2026, 10, 29)
        self.days = set(pd.bdate_range('2025-01-01', '2026-10-30').date)

    def test_main_daytime_dispatches_to_new_pipeline(self):
        from backend import incremental_update as module
        class FixedClock:
            @staticmethod
            def now(tz):
                return datetime(2026, 10, 30, 16, 0, tzinfo=tz)
        provider = MagicMock()
        provider.trading_days.return_value = self.days
        fake_cache = MagicMock()
        fake_cache.records = {}
        with patch.object(module, 'datetime', FixedClock), \
             patch.object(module, 'AkshareProvider', return_value=provider), \
             patch.object(module, 'MarketCache', return_value=fake_cache), \
             patch.object(module, 'choose_asof', return_value=self.target), \
             patch.object(module, 'run') as runner, \
             patch('sys.argv', ['incremental_update']):
            module.main()
        self.assertTrue(runner.called)
        self.assertTrue(runner.call_args.kwargs['historical_target'])
        fake_cache.save.assert_called_once()

    def test_yesterday_cold_start_builds_cache_and_publishes_without_current_quotes(self):
        provider = HistoricalProvider()
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            cache = MarketCache(root / 'cache')
            result = run(provider, root / 'web/data', cache,
                         self.target, self.days, historical_target=True,
                         quote_fetcher=must_not_fetch_current_quote)
            self.assertEqual(result['asof'], self.target.isoformat())
            self.assertEqual(result['stock_count'], 100)
            self.assertEqual(len(cache.records), 110)
            self.assertTrue(all(len(x['bars']) == 61 for x in cache.records.values()))
            self.assertTrue((root / 'cache/universe.json.gz').exists())
            provider.calls.clear()
            saved = MarketCache(root / 'cache')
            result2 = run(provider, root / 'web/data', saved,
                          self.target, self.days, historical_target=True,
                          quote_fetcher=must_not_fetch_current_quote)
            self.assertEqual(result2['stock_count'], 100)
            self.assertEqual(provider.calls, [], '重复运行不应重新请求已验证的历史数据')

    def test_time_budget_error_saves_partial_cache_and_resume_only_missing(self):
        provider = HistoricalProvider(count=110)
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            cache = MarketCache(root / 'cache')
            with self.assertRaisesRegex(RuntimeError, '时间预算'):
                run(provider, root / 'web/data', cache,
                    self.target, self.days, historical_target=True,
                    quote_fetcher=must_not_fetch_current_quote,
                    bootstrap_max_seconds=-1)
            self.assertTrue((root / 'cache/universe.json.gz').exists())
            loaded = MarketCache(root / 'cache')
            self.assertEqual(len(loaded.records), 100)
            provider.calls.clear()
            result = run(provider, root / 'web/data', loaded,
                         self.target, self.days, historical_target=True,
                         quote_fetcher=must_not_fetch_current_quote)
            self.assertEqual(result['stock_count'], 100)
            # Only 10 missing stock rolls + 100 histories for replay; no roll
            # bootstrap for first 100 verified stocks.
            qfq = [x for x in provider.calls if x[1] == 'qfq']
            self.assertEqual(len(qfq), 10)
            self.assertEqual(len(loaded.records), 110)


if __name__ == '__main__':
    unittest.main()
