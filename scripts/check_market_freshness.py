"""Skip redundant full-market downloads when the published Pages snapshot
already matches the most recent *completed* Chinese market session.

Intentionally fail open for the freshness *check*: any network/calendar error
should cause the normal updater to run and perform its strict data validation.
Never publish the repository's synthetic demo JSON when this check skips work.
"""
from __future__ import annotations

import json
import os
from datetime import date, datetime
from pathlib import Path
import sys
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def pages_base() -> str:
    override = os.environ.get('ASTOCK_PAGES_BASE', '').strip()
    if override:
        if not override.startswith('https://'):
            raise ValueError('ASTOCK_PAGES_BASE 必须使用 HTTPS')
        return override.rstrip('/') + '/'
    owner, repo = os.environ['GITHUB_REPOSITORY'].split('/', 1)
    return f'https://{owner.lower()}.github.io/{repo}/'


def remote_manifest(base: str) -> dict:
    url = base + 'data/manifest.json'
    req = Request(url, headers={
        'User-Agent': 'AStockTrainer-Schedule-Check/1.0',
        'Cache-Control': 'no-cache',
        'Pragma': 'no-cache',
    })
    with urlopen(req, timeout=20) as resp:
        raw = resp.read(2_000_001)
    if len(raw) > 2_000_000:
        raise ValueError('行情清单体积异常')
    obj = json.loads(raw)
    if not isinstance(obj, dict):
        raise ValueError('清单不是JSON对象')
    return obj


def already_published(manifest: dict, target: date) -> bool:
    if manifest.get('schema_version') != 1 or manifest.get('is_demo') is not False:
        return False
    if not isinstance(manifest.get('stocks'), list):
        return False
    if manifest.get('stock_count') != len(manifest['stocks']) or len(manifest['stocks']) < 1:
        return False
    if not isinstance(manifest.get('history_ready_count'), int) or manifest['history_ready_count'] < 1:
        return False
    try:
        published = date.fromisoformat(manifest['asof'])
    except (KeyError, TypeError, ValueError):
        return False
    # Do not regress the website to an older trade date on delayed schedule runs.
    return published >= target


def need_update(target: date, manifest: dict | None) -> bool:
    return manifest is None or not already_published(manifest, target)


def main() -> None:
    # If anything here fails, do the normal rigorous update rather than relying
    # on stale data or silently publishing demo content.
    required = True
    try:
        from backend.providers import AkshareProvider
        from backend.update import SH_TZ, choose_asof
        now = datetime.now(SH_TZ)
        target = choose_asof(AkshareProvider().trading_days(), now)
        manifest = remote_manifest(pages_base())
        required = need_update(target, manifest)
        print(f'数据新鲜度检查：当前目标交易日{target.isoformat()}，线上日期{manifest.get("asof")}，'
              f'{"需要更新" if required else "已最新，跳过下载"}', flush=True)
    except Exception as exc:
        print(f'无法确认线上快照是否最新（{type(exc).__name__}: {exc}），执行正常严格更新。', flush=True)
        required = True
    output = os.environ.get('GITHUB_OUTPUT')
    if output:
        with open(output, 'a', encoding='utf-8') as file:
            file.write(f'update_required={str(required).lower()}\n')
    else:
        print(f'update_required={str(required).lower()}', flush=True)


if __name__ == '__main__':
    main()
