"""Reuse the already published, verified market snapshot for UI-only Pages deployments.

The repository contains demo JSON, so a UI-only deployment MUST NOT publish it.
Fail closed if the currently published real snapshot cannot be fetched and validated.
This script does not call any upstream quote or stock-list API.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import re
import shutil
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from urllib.error import HTTPError, URLError
from urllib.parse import urljoin
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parents[1]
WEB = ROOT / "web"
STAGE = WEB / "_snapshot_download"
DEST = WEB / "data"
PATH_RE = re.compile(r"^data/stocks/([0-9]{6})\.json$")
RETRIES = 3


def pages_base() -> str:
    override = os.environ.get("ASTOCK_PAGES_BASE")
    if override:
        return override.rstrip("/") + "/"
    repo = os.environ.get("GITHUB_REPOSITORY", "").split("/", 1)
    if len(repo) != 2 or not all(repo):
        raise RuntimeError("未设置 GITHUB_REPOSITORY，无法推断已部署的Pages网址")
    owner, name = repo
    return f"https://{owner.lower()}.github.io/{name}/"


def fetch_json(base: str, path: str):
    if path.startswith(("/", "\\")) or ".." in path or "\\" in path:
        raise RuntimeError(f"非法数据路径: {path}")
    url = urljoin(base, path)
    exc = None
    for attempt in range(RETRIES):
        try:
            req = Request(url, headers={"User-Agent": "AStockTrainer-UI-Deploy/1.0", "Cache-Control": "no-cache"})
            with urlopen(req, timeout=25) as r:
                raw = r.read(16 * 1024 * 1024 + 1)
            if len(raw) > 16 * 1024 * 1024:
                raise ValueError("数据文件超过16MB，已拒绝")
            return json.loads(raw)
        except (HTTPError, URLError, OSError, ValueError, json.JSONDecodeError) as err:
            exc = err
            if attempt < RETRIES - 1:
                time.sleep(attempt + 1)
    raise RuntimeError(f"读取已发布快照失败 {url}: {exc}")


def main() -> None:
    base = pages_base()
    print(f"前端快速部署：复用 {base} 当前已发布的真实行情", flush=True)
    manifest = fetch_json(base, "data/manifest.json")
    stocks = manifest.get("stocks", [])
    if (
        manifest.get("schema_version") != 1
        or manifest.get("is_demo") is not False
        or not isinstance(stocks, list)
        or not stocks
        or manifest.get("stock_count") != len(stocks)
        or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", str(manifest.get("asof")))
    ):
        raise RuntimeError("线上行情尚未发布或数据不完整；已拒绝用仓库演示数据覆盖真实行情")

    items = []
    seen = set()
    for stock in stocks:
        code = stock.get("code")
        history_url = stock.get("history_url")
        if not stock.get("has_history"):
            continue
        match = PATH_RE.fullmatch(str(history_url))
        if not match or match.group(1) != str(code) or code in seen:
            raise RuntimeError(f"数据路径、代码或重复记录异常: {code}: {history_url}")
        seen.add(code)
        items.append((code, history_url))
    if not items:
        raise RuntimeError("行情数据中没有可用于历史模拟的股票")

    if STAGE.exists():
        shutil.rmtree(STAGE)
    (STAGE / "stocks").mkdir(parents=True)

    def one(item):
        code, path = item
        data = fetch_json(base, path)
        if data.get("code") != code or data.get("asof") != manifest["asof"] or not isinstance(data.get("bars"), list) or len(data["bars"]) < 61:
            raise RuntimeError(f"历史行情不完整或日期错误: {code}")
        return code, data

    with ThreadPoolExecutor(max_workers=5) as executor:
        futures = {executor.submit(one, item): item[0] for item in items}
        for future in as_completed(futures):
            code, data = future.result()
            (STAGE / "stocks" / f"{code}.json").write_text(json.dumps(data, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")

    (STAGE / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    # Replace only after every file is validated. Never silently deploy demo data.
    if DEST.exists():
        shutil.rmtree(DEST)
    STAGE.rename(DEST)
    print(f"已复用真实快照：{manifest['asof']}，{len(items)}只股票历史K线；未重新请求A股行情接口", flush=True)


if __name__ == "__main__":
    main()
