"""On first cache initialization, reuse the user's *already published* 1001-bar files.

Data source is our own Pages website, not external stock-history suppliers.
This is best-effort: failure is logged and updater can still bootstrap via API.
"""
from __future__ import annotations
from concurrent.futures import ThreadPoolExecutor,as_completed
from pathlib import Path
import sys

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from backend.market_cache import MarketCache
from scripts.reuse_published_data import fetch_json,pages_base


def main():
    cache=MarketCache(ROOT/'.market-cache-state')
    if len(list(cache.history_dir.glob('*.json.gz')))>=40:
        print('历史回放缓存已有足够数据，跳过旧网页种子恢复。',flush=True)
        return
    try:
        base=pages_base()
        manifest=fetch_json(base,'data/manifest.json')
        if manifest.get('is_demo') is not False or not isinstance(manifest.get('stocks'),list):
            raise RuntimeError('已发布网站不是已验证的真实行情快照')
        items=[s for s in manifest['stocks'] if s.get('has_history') and s.get('history_url')]
        def get(s):
            code=s['code']
            # Safe: fetch_json rejects path traversal and our own manifest
            # already constrains stock file URL form.
            if s['history_url']!=f'data/stocks/{code}.json':
                raise ValueError(f'历史数据路径异常：{code}')
            doc=fetch_json(base,s['history_url'])
            if doc.get('code')!=code or doc.get('asof')!=manifest['asof']:
                raise ValueError(f'回放数据日期、代码不匹配：{code}')
            bars=doc.get('bars')
            if not isinstance(bars,list) or len(bars)<61:
                raise ValueError('回放历史不足61根')
            return code,bars
        ok=0
        with ThreadPoolExecutor(max_workers=5) as pool:
            for future in as_completed([pool.submit(get,s) for s in items]):
                try:
                    code,bars=future.result()
                    if cache.get_history(code) is None:
                        cache.put_history(code,bars)
                        ok+=1
                except Exception as exc:
                    print(f'历史回放种子恢复单文件失败：{exc}',flush=True)
        print(f'已从原GitHub Pages复用{ok}只股票历史日K线（未查询行情API）。',flush=True)
    except Exception as exc:
        print(f'原网页历史数据复用不可用，继续从行情源初始化：{exc}',flush=True)

if __name__=='__main__':main()
