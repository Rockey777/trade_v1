"""Tencent *batch* daily snapshots: one HTTP request per 50 symbols.

Strict 2026 format. Invalid/missing/stale quotes are returned as missing and
must be verified separately; never silently pass yesterday as today's close.
"""
from __future__ import annotations
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date
import logging
import re
import time
import requests

LOG = logging.getLogger('astock')
ROW = re.compile(r'v_(?:sh|sz)(\d{6})="([^"]*)"')


def parse_tencent(text: str, target: date) -> dict[str, dict]:
    output = {}
    for match in ROW.finditer(text):
        code, payload = match.groups()
        f = payload.split('~')
        if len(f) < 37 or f[2] != code:
            continue
        try:
            stamp = f[30][:8]
            if len(stamp)!=8 or not stamp.isdigit() or date(int(stamp[:4]),int(stamp[4:6]),int(stamp[6:8])) != target:
                continue
            c,prev,o,hi,lo,vol = [float(f[i]) for i in (3,4,5,33,34,6)]
            name=f[1].strip()
            if not name or min(c,prev,o,hi,lo)<=0 or hi<max(o,c) or lo>min(o,c) or vol<0:
                continue
            output[code]={'code':code,'name':name,'date':target.isoformat(),
                          'o':round(o,4),'h':round(hi,4),'l':round(lo,4),'c':round(c,4),
                          'v':round(vol,2),'prev':round(prev,4),
                          'change_pct':round((c/prev-1)*100,4)}
        except (ValueError,IndexError,OverflowError):
            continue
    return output


def fetch_batch_quotes(codes: list[str], target: date, batch_size=50, workers=3,
                       session=None, attempts=2) -> dict[str, dict]:
    """Requests stable 50-symbol packets; no per-stock HTTP calls in success path.

    Returns only quotes dated target; caller decides if missing rows are stale,
    suspended, or hard failures. Retries use limited concurrency.
    """
    session = session or requests  # stateless requests.get is safe across worker threads
    codes=sorted(set(codes))
    groups=[codes[i:i+batch_size] for i in range(0,len(codes),batch_size)]
    def fetch(group):
        sym=','.join(('sh' if c.startswith('6') else 'sz')+c for c in group)
        err=None
        for n in range(attempts):
            try:
                response=session.get('https://qt.gtimg.cn/q='+sym,
                    headers={'User-Agent':'Mozilla/5.0 (AStockTrainer/2.1)', 'Referer':'https://gu.qq.com/'},
                    timeout=16)
                response.raise_for_status()
                response.encoding='gbk'
                data=parse_tencent(response.text,target)
                # A partly truncated group should be retried, not counted as complete.
                if len(data)<int(len(group)*.85):
                    raise RuntimeError(f'返回{len(data)}/{len(group)}条有效目标日行情')
                return data
            except Exception as ex:
                err=ex
                if n+1<attempts: time.sleep(1.0+n)
        LOG.warning('腾讯批量报价失败（%s.. %s）：%s',group[0],group[-1],err)
        return {}
    rows={}
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures=[pool.submit(fetch,group) for group in groups]
        for fut in as_completed(futures):
            rows.update(fut.result())
    LOG.info('腾讯批量报价：%d批、目标日期%s、成功%d/%d只',len(groups),target,len(rows),len(codes))
    return rows
