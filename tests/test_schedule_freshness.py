"""Run with: python -m unittest discover -s tests -p 'test_schedule_freshness.py'"""
import importlib.util
import unittest
from datetime import date
from pathlib import Path

path = Path(__file__).resolve().parents[1] / 'scripts' / 'check_market_freshness.py'
spec = importlib.util.spec_from_file_location('check_market_freshness', path)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)

D = date(2026, 10, 29)


def manifest(asof='2026-10-29', demo=False):
    return {'schema_version': 1, 'is_demo': demo, 'asof': asof,
            'stock_count': 1, 'history_ready_count': 1,
            'stocks': [{'code': '600000', 'has_history': True}]}


class FreshnessTests(unittest.TestCase):
    def test_fresh_current_skips_download(self):
        self.assertFalse(module.need_update(D, manifest()))

    def test_old_requires_update(self):
        self.assertTrue(module.need_update(D, manifest('2026-10-28')))

    def test_newer_never_regresses(self):
        self.assertFalse(module.need_update(D, manifest('2026-10-30')))

    def test_demo_requires_update(self):
        self.assertTrue(module.need_update(D, manifest(demo=True)))

    def test_missing_or_incomplete_requires_update(self):
        self.assertTrue(module.need_update(D, None))
        m = manifest(); m['history_ready_count'] = 0
        self.assertTrue(module.need_update(D, m))
        m = manifest(); m['stock_count'] = 999
        self.assertTrue(module.need_update(D, m))

    def test_invalid_date_requires_update(self):
        self.assertTrue(module.need_update(D, manifest('invalid')))


if __name__ == '__main__':
    unittest.main()
