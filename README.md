AStock Trainer PWA

面向**个人安卓手机**的 A股沪深主板 RPS 强势股筛选及60个交易日逐日模拟训练系统。采用**纯静态网页/PWA + Python 数据生成 + GitHub Actions 自动更新**，没有后端常驻服务器、Android SDK 或 APK 的要求。

> **交付状态（2026-10-29）**：前端代码、算法、模拟撮合、自动更新工作流及自动测试已完成。仓库内的 `web/data` 初始内容全部是 **明确标注的合成演示数据**，不是实时A股行情。请按部署说明首次运行更新任务，只有拉到真实数据、通过覆盖率检查并成功部署后，网站才会显示真实股票。当前构建环境无法访问第三方行情API，尚未完成真实接口的端到端验证。

## 一、严格保留用户要求的筛选顺序

1. 全市场A股实时快照与沪深交易所上市列表交叉匹配，保留主板：`600/601/603/605/000/001/002` 开头；剔除创业板、科创板和北交所。
2. 最新未复权价 `0 < price <= 15` 元；上市日期距选股日**至少1000自然日**；按当日名称剔除 `ST`、`*ST` 与“退市”证券。
3. 对**上一步筛选出的股票池**计算 RPS60：采用行情接口一次返回的**60日涨跌幅**作为收益率，按收益率降序计算百分位 `RPS=100*(N-名次)/(N-1)`；相同涨幅共享名次（并列排名），取**前100只**。命令行另外支持20/120/250周期：按每只候选股前复权历史逐一计算，会更慢、可能受到接口限流。
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
3. 到仓库 **Actions → 每日更新A股行情并发布PWA → Run workflow**，**在北京时间交易日19:25以后**运行第一次任务。任务顺序：抓取股票现价/上市日期→RPS排名→拉回放行情→覆盖率校验→部署Pages。**只有全部通过才部署真实版**。
4. 打开Actions任务页面，在 `deploy` 步骤查看自动生成的Pages网址，形如 `https://你的用户名.github.io/你的仓库名/`；安卓Chrome打开后选择菜单里的 **安装应用** / **添加到主屏幕**。
5. 之后计划任务是**北京时间周一至周五19:25**（交易日）。中国股市休市日或数据接口失败时，更新会**报错且不会覆盖上一次已发布的数据**；手机网页显示快照日期，不能把旧数据当成当日数据。
6. 为避免GitHub公开仓库**60天无活动自动停止定时任务**，工作流每隔至少30天，在**成功更新交易日**自动提交一个极小的活动日期文件 `.last-successful-checkin`；不会把你的交易记录推送到GitHub。若行情接口连续失败导致工作流长期不运行，仍需手动启用/修复。

GitHub Pages是静态站点服务，不是保密服务器。仓库公开时不要上传私人信息或供应商令牌。

### 自动更新可能遇到的两类问题

**AKShare上游接口时断时续**：本项目使用 `ak.stock_zh_a_spot_em`、`stock_info_sh_name_code`、`stock_info_sz_name_code`、`stock_zh_a_hist` 和 `tool_trade_date_hist_sina`。接口可能限流、改变字段或在GitHub Actions网络环境中不可访问。工作流会严格失败而非伪造数据；检查Actions日志中失败的接口和覆盖率。必要时更换付费/授权行情供应商（需修改 `backend/providers.py`）。

**国内用户访问GitHub Pages存在网络不稳定**：可以将同样的 `web/` 内容部署到其他支持HTTPS静态托管的平台，但需另外配置定时更新。

## 四、电脑上本地测试（不必装安卓环境）

```bash
# 直接浏览随包附带的“演示数据”，不用Python行情依赖：
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

默认只允许**北京时间当天18时后且当天为A股交易日**生成数据，并要求回放K线日期和当日现价匹配。非交易日直接失败，不覆盖之前的网页数据。测试调试可传 `--force`，但**不得把强制模式输出当作完整当日收盘快照**。

## 五、运行测试

```bash
cd astock-pwa
python -m unittest discover -s tests -v
npm test   # 仅使用Node自带测试框架，无需npm install
```

主要文件：

```
backend/core.py            市场过滤、RPS排名、收益率
backend/providers.py       AKShare接口及上市日期字段校验
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
web/data/                  初始演示JSON（首次真实更新时替换）
.github/workflows/          自动每日更新与部署
```

## 六、数据合规与说明

- 数据源为第三方行情接口，未在当前开发环境完成真实接口在线验证；**正式部署前必须查看更新日志和数据日期**。
- 作为私人学习工具，不应在未经许可的情况下对第三方行情进行公开再分发或商业化。
- PWA安装需要HTTPS（GitHub Pages默认HTTPS）或localhost；直接双击 `index.html` 的 file:// 路径不具备完整浏览器安全上下文，应该使用本地HTTP服务器。
- 本工具不连接券商账户，不发送真实证券交易指令。
