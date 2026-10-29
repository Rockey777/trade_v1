# AStockTrainer v1.8 完整版（含 v1.6 手机竖屏界面）

> **完整项目更新说明（必读）**
> - 本包已合并 v1.6 手机竖屏布局、v1.7 三次定时检查和 v1.8 双交易日历/手动强制更新。
> - 已**移除演示行情**。`web/data/` 中只有占位文件，真实行情必须由每日行情工作流生成。
> - 将本压缩包**解压后根目录的所有文件、文件夹**上传到仓库 `main` 的**根目录**。不要仅上传 ZIP 文件，不要多创建一层文件夹。
> - `.github/workflows/` 是隐藏文件夹，必须保留；否则定时更新不会运行。
> - **不要用“前端快速部署”来刷新股票数据**；必须运行「每日更新A股行情并发布PWA」，手动运行时勾选 `force_data_update`。
> - 替换前若仓库中曾存在 `web/data/manifest.json` 和示例 `web/data/stocks/*.json`，不要把它们当作最新行情。工作流会直接从网络重建数据并发布。
> - 当晚执行后，查看部署站点 `/data/manifest.json` 中的 `asof` 与 `is_demo:false`，切勿只依赖工作流绿色勾判断是否更新至最新交易日。

## 首次重新部署步骤

1. 解压本项目，将根目录文件更新到仓库（保留路径 `.github/workflows/update-and-deploy.yml` 和 `frontend-only.yml`）。
2. 在仓库 `Settings → Pages → Build and deployment` 设置 `Source: GitHub Actions`。
3. 提交后进入 `Actions → 每日更新A股行情并发布PWA → Run workflow → main`，确保 `force_data_update` 勾选。
4. 在日志中确认 `选股基准交易日` 为预期日期；等待 `build` 与 `deploy` 两项均成功。
5. 访问 `https://rockey777.github.io/trade_v1/data/manifest.json` 验证 `asof` 为已完成的最新交易日。此后手机打开原PWA地址，必要时关闭再打开以更新缓存。

工作流在北京时间工作日 **19:25、21:25、23:25** 自动检查；成功更新后，备用时段将跳过重复下载。GitHub定时触发可能延迟或遗漏，第三方行情故障也可能阻止发布，无法承诺每天必定更新。

---

# 强势股训练 | AStock Trainer PWA

面向**个人安卓手机**的 A股沪深主板 RPS 强势股筛选及60个交易日逐日模拟训练系统。采用**纯静态网页/PWA + Python 数据生成 + GitHub Actions 自动更新**，没有后端常驻服务器、Android SDK 或 APK 的要求。

> **交付状态（v1.3股票基础资料双源容错版，2026-10-29）**：前端代码、算法、模拟撮合、自动更新工作流及自动测试已完成。仓库内的 `web/data` 初始内容全部是 **明确标注的合成演示数据**，不是实时A股行情。请按部署说明首次运行更新任务，只有拉到真实数据、通过覆盖率检查并成功部署后，网站才会显示真实股票。当前构建环境无法访问第三方行情API，尚未完成真实接口的端到端验证。

## 一、严格保留用户要求的筛选顺序

1. 已收盘的当日使用全市场实时收盘快照；未满18点/非交易日按最近一个已完成交易日的历史日线生成历史快照。与交易所上市列表交叉匹配（接口故障时改用BaoStock独立证券基础资料），保留主板：`600/601/603/605/000/001/002` 开头；剔除创业板、科创板和北交所。
2. 最新未复权价 `0 < price <= 15` 元；上市日期距选股日**至少1000自然日**；按当日名称剔除 `ST`、`*ST` 与“退市”证券。
3. 对**上一步筛选出的股票池**计算 RPS60：当天18:00后采用行情接口一次返回的**60日涨跌幅**；回退历史日期时按截止该日的前复权历史K线计算60个交易日收益率，按收益率降序计算百分位 `RPS=100*(N-名次)/(N-1)`；相同涨幅共享名次（并列排名），取**前100只**。命令行另外支持20/120/250周期：按每只候选股前复权历史逐一计算，会更慢、可能受到接口限流。
4. **从这前100只中**剔除今日涨幅 `>+5%` 和跌幅 `<-5%`；最终数量可以少于100，不补位。
5. 对最终入选股票下载未复权日OHLCV，最多保留`T−1000`至`T`共1001根日K线；初始最多只显示到`T−60`，之后通过买入/卖出/观望逐个交易日解锁至`T`。

**重要**：这是用户指定的“**今日选强势股，再回看历史**”训练，而非严格的无前视回测。今日筛选本身含未来信息。完整历史JSON随网页下载，前端通过回放进度隐藏未来K线，不提供防开发者工具查看数据的保密保证。

## 二、手机端功能

- 股票搜索、现价/涨跌幅/RPS、榜单名次，点击进入历史训练。
- 自绘日K线主图；三个副图：成交量VOL、MACD(12,26,9，柱为2×DIF-DEA)、DMI(14,6：PDI/MDI/ADX/ADXR)。无分时、均线或其他指标。
- Canvas绘图，支持触摸横移、双指缩放、点击显示当日OHLCV/指标。
- 初始虚拟现金10万元；默认100股买入；买入/卖出决策发生在当前K线收盘后，按**下一交易日开盘价**撮合；观望直接推进一天；A股T+1持仓限制、简化涨跌停一字板阻断。
- 佣金模拟0.025%、**最低5元**，过户费0.001%（双向），印花税0.05%（卖出）。**这只是可配置的简化费率模型**，不同券商实际收费可能不同。
- 显示现金、持仓、总资产、收益率和成交记录；每只股票/快照日独立保存于浏览器 IndexedDB（失败时降级localStorage）。可重开续玩或重置。
- PWA安装到安卓桌面、基础离线资源缓存，行情按网络优先、缓存兜底；缺网无法更新新日期的行情。

**模拟精度边界**：暂未完整处理现金分红、送配转股、历史ST和所有特殊涨跌停制度；停牌/无有效成交、涨跌停封单量、开盘量价冲击也无法用日K精准还原。因此收益不是严格可执行的真实交易结果。正式投研回测需补充公司行为、历史ST及交易规则数据。

## 三、GitHub Pages 自动更新部署（推荐，手机只需打开链接）

前提：GitHub账号，建议一个新建的**公开仓库**；公开Pages上展示的股票数据也会是公开可见的，必须确认上游行情数据的使用及再分发条款允许部署。

1. 把压缩包解压到电脑，打开其中的 `astock-pwa` 项目目录，将**全部文件（包括隐藏 `.github/workflows`）**上传到新GitHub仓库的根目录；或使用下面的git命令：
   ```bash
   cd astock-pwa
   git init
   git add .
   git commit -m "initial A-stock trainer"
   git branch -M main
   git remote add origin https://github.com/你的用户名/你的仓库名.git
   git push -u origin main
   ```
2. 进入GitHub仓库 **Settings → Pages → Build and deployment → Source → GitHub Actions**；启用GitHub Actions（Settings → Actions → General，如有必要允许工作流运行）。
3. 到仓库 **Actions → 每日更新A股行情并发布PWA → Run workflow**，**任意时刻**可以运行第一次任务：北京时间18:00前选择上一交易日，18:00及以后选择今天（若当天休市，则取上一个交易日）。任务顺序：抓取股票现价/上市日期→RPS排名→拉回放行情→覆盖率校验→部署Pages。**只有全部通过才部署真实版**。
4. 打开Actions任务页面，在 `deploy` 步骤查看自动生成的Pages网址，形如 `https://你的用户名.github.io/你的仓库名/`；安卓Chrome打开后选择菜单里的 **安装应用** / **添加到主屏幕**。
5. 之后计划任务是**北京时间周一至周五19:25**；若碰到中国股市休市日，选择最近一个已完成交易日；若数据接口失败，更新会**报错且不会覆盖上一次已发布的数据**；手机网页显示快照日期，不能把旧数据当成当日数据。
6. 为避免GitHub公开仓库**60天无活动自动停止定时任务**，工作流每隔至少30天，在**成功更新交易日**自动提交一个极小的活动日期文件 `.last-successful-checkin`；不会把你的交易记录推送到GitHub。若行情接口连续失败导致工作流长期不运行，仍需手动启用/修复。

GitHub Pages是静态站点服务，不是保密服务器。仓库公开时不要上传私人信息或供应商令牌。

### 自动更新可能遇到的两类问题

**AKShare上游接口时断时续**：本项目使用 `ak.stock_zh_a_spot_em`、`stock_info_sh_name_code`、`stock_info_sz_name_code`（失败则 `baostock.query_stock_basic`）、`stock_zh_a_hist_tx`（腾讯优先）/`stock_zh_a_hist`（东财备用）和 `tool_trade_date_hist_sina`。接口可能限流、改变字段或在GitHub Actions网络环境中不可访问。工作流会严格失败而非伪造数据；检查Actions日志中失败的接口和覆盖率。必要时更换付费/授权行情供应商（需修改 `backend/providers.py`）。

**国内用户访问GitHub Pages存在网络不稳定**：可以将同样的 `web/` 内容部署到其他支持HTTPS静态托管的平台，但需另外配置定时更新。


### v1.3 交易所名单接口的备用方案

如果GitHub Actions出现 `stock_info_sh_name_code 请求失败：Expecting value: line 1 column 1`，新版 `backend/providers.py` 会自动改用独立的 **BaoStock** `query_stock_basic()` 获取上海/深圳主板的股票名称与实际上市日期。`requirements.txt` 已增加 `baostock` 依赖，更新仓库时**必须同时替换这两个文件**。交易所和BaoStock都失败时，更新任务会明确报错，不会编造上市日期或绕过1000天规则。请查看日志中的“股票基础资料：...成功”。注意：备用服务在GitHub Actions出网环境下仍有可能不可达，尚未经真实联网验证。

## 四、电脑上本地测试（不必装安卓环境）

```bash
# 查看前端页面结构（此完整部署包不带行情JSON；需先运行真实行情更新）：
cd astock-pwa/web
python -m http.server 8000
# 电脑浏览器打开 http://127.0.0.1:8000
```

若要下载真实数据（Python 3.11+，需要网络）：

```bash
cd astock-pwa
python -m venv .venv
# Linux/macOS: source .venv/bin/activate
# Windows: .venv\Scripts\activate
pip install -r requirements.txt
python -m backend.update --output web/data --rps-window 60
```

默认在北京时间**18:00之前使用上一个A股交易日收盘数据，18:00之后使用今天收盘数据**；周末和休市日自动退回最近交易日。可选 `--date YYYY-MM-DD` 明确指定历史交易日。

**首次白天手动构建会比较慢**：当前AKShare不提供可直接用于这个筛选方案的全市场历史收盘价+历史RPS组合快照，所以必须逐只获取候选主板股票的历史K线以保证日期准确；首次白天构建可能明显慢于晚间收盘后的构建。GitHub Actions `build` 有执行时间上限，上游API不稳定时可能失败。正式的19:25计划任务仍使用较快的当天收盘快照。

非交易日通过交易日历自动回退；如果行情接口失败或完整率不够，拒绝发布，保留网站旧数据。测试调试可传 `--force`，但**不得把强制模式输出当作完整当日收盘快照**。

*注意：*历史回退使用对应日期的OHLCV和前复权RPS，但股票简称/ST状态来自当前交易所基础信息，不能完全还原较久以前的历史名称变更；本功能默认仅回退最近一个交易日。

## 五、运行测试

```bash
cd astock-pwa
python -m unittest discover -s tests -v
npm test   # 仅使用Node自带测试框架，无需npm install
```

主要文件：

```
backend/core.py            市场过滤、RPS排名、收益率
backend/providers.py       AKShare接口、BaoStock基础资料备用接口与上市日期校验
backend/update.py          当日真实行情更新、历史回放数据生成
backend/make_demo.py       明确标注的合成行情（只用于演示）
web/index.html             手机页面
web/styles.css             深色适配移动端的样式
web/app.js                 股票列表/回放/记录/进度
web/chart.js               原生Canvas日K线+三个副图
web/indicators.js          MACD/DMI因果计算
web/trading.js             T+1/次日开盘模拟撮合
web/store.js               IndexedDB持久化
web/sw.js                  PWA Service Worker
web/data/                  空目录（真实行情通过GitHub Actions生成）
.github/workflows/          自动每日更新与部署
```

## 六、数据合规与说明

- 数据源为第三方行情接口，未在当前开发环境完成真实接口在线验证；**正式部署前必须查看更新日志和数据日期**。
- 作为私人学习工具，不应在未经许可的情况下对第三方行情进行公开再分发或商业化。
- PWA安装需要HTTPS（GitHub Pages默认HTTPS）或localhost；直接双击 `index.html` 的 file:// 路径不具备完整浏览器安全上下文，应该使用本地HTTP服务器。
- 本工具不连接券商账户，不发送真实证券交易指令。


## 七、v1.2 修复说明（2026-10-29）

此前手动运行的历史快照模式出现 551/2934 只股票历史行情请求失败，提示 `RemoteDisconnected`；完整率81.2%，系统正确阻止发布。v1.2 **保留98%完整率门槛**，但对每只股票日线采用两家相互独立的上游数据源：

1. 首选 AKShare `stock_zh_a_hist_tx`（腾讯日线）。
2. 若连接断开、字段异常或没有有效数据，自动回退到 `stock_zh_a_hist`（东方财富日线）。
3. 规范化两套字段到相同的“日期/开盘/最高/最低/收盘/成交量”结构，严格截止本次的 `asof`，防止读取未完成交易日的未来行情。
4. 每个源短暂重试最多2次；在日志中每100只打印各来源成功/失败次数。默认历史下载并发由3改为2，减轻上游连接压力。
5. 两个源都不可用或综合覆盖率仍低于98%时，**照旧失败并不发布**，不能以81.2%残缺数据计算RPS前100名。实际是否成功需GitHub Actions联网验证，单元测试不代表数据源可用率承诺。

仅升级现有仓库时：替换 `backend/providers.py`、`backend/update.py`，推荐补上 `tests/test_provider_fallback.py`。 `.github/workflows/update-and-deploy.yml` 不需要改，命令保持：

```bash
python -m backend.update --output web/data --rps-window 60
```

注意：**本次失败的GitHub Actions不会覆盖网页数据**；直接重新运行即可。当天18:00以后按全市场快照计算RPS，速度通常比需要逐只取历史日线的白天模式快，但最终入选股票的历史K线仍依赖两个上游接口。若腾讯和东财在GitHub公网出口均无法稳定服务，需要更换有稳定授权的行情供应商，程序不能保证仅凭免费接口每天100%可用。

## v1.5 前端快速部署（无需重新抓取行情）

新增 `.github/workflows/frontend-only.yml` 和 `scripts/reuse_published_data.py`。

- 日常定时RPS行情更新仍使用「每日更新A股行情并发布PWA」工作流，在北京时间周一至周五19:25执行。
- **仅修改网页布局、K线、副图、样式时**：只提交 `web/*.html`、`web/*.js`、`web/*.css` 等前端文件即可自动触发「前端快速部署（不下载股票行情）」工作流；也可在 Actions 页面选择该工作流手动运行 `main`。
- 快速部署从已成功上线的GitHub Pages网址下载**已发布的真实 `data/manifest.json` 和入选股票的历史JSON**，随后把新网页与这些数据一起发布。不会访问腾讯/东财/BaoStock，也不重新计算RPS或逐只扫描全部A股；预计比行情更新快得多。
- **保护措施**：如果线上尚无真实数据、行情文件不完整或网络下载失败，则直接失败并保留当前网页，不会将仓库自带的演示数据发布到线上。
- 因为这个流程重用现有快照，最终行情基准日不会因前端更新而改变。下一次正常行情任务会再次发布最新前端与新数据。
- 首次升级现有仓库，除 v1.4 的 `web/index.html`、`web/app.js`、`web/chart.js`、`web/styles.css` 外，**还需新增**这两个文件（不是替换原来的行情工作流）。
