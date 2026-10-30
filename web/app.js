import {StockChart} from './chart.js';
import {INITIAL_CASH,newAccount,nextDay,position,accountValue} from './trading.js';
import {loadSession,saveSession} from './store.js';
import {stockNavigation} from './navigation.js';
const $=id=>document.getElementById(id), yuan=x=>'¥'+Number(x).toLocaleString('zh-CN',{minimumFractionDigits:2,maximumFractionDigits:2}),pct=x=>(x>=0?'+':'')+x.toFixed(2)+'%';
let manifest=null,selected=null,chart=null,bars=[],account=null,sessionKey='',requestId=0,activeIndicator='vol';
function message(text,error=false){$('data-status').textContent=text;$('data-status').className='data-status'+(error?' error':manifest?.is_demo?' demo':'');}
async function getJson(url){const r=await fetch(url,{cache:'no-store'});if(!r.ok)throw new Error(`HTTP ${r.status}: ${url}`);return r.json();}
function syncIndicatorButtons(){document.querySelectorAll('.switch-btn').forEach(btn=>{const active=btn.dataset.indicator===activeIndicator;btn.classList.toggle('active',active);btn.setAttribute('aria-pressed',String(active));});}
function setIndicator(mode){activeIndicator=mode;syncIndicatorButtons();if(chart)chart.setIndicator(mode);}
function syncStockNavigation(){
  const nav=stockNavigation(manifest?.stocks,selected?.code);
  $('prev-stock').disabled=!nav.previous;
  $('next-stock').disabled=!nav.next;
  return nav;
}
function goToAdjacentStock(direction){
  if(!selected||!manifest)return;
  const nav=syncStockNavigation();
  const target=direction<0?nav.previous:nav.next;
  if(target)openStock(target);
}
function clearTradeForLoading(){
  account=null;bars=[];sessionKey='';
  $('chart-container').hidden=true;
  $('chart-tooltip').hidden=true;
  $('trade-price').textContent='—';$('trade-day').textContent='正在加载历史行情…';
  $('buy').disabled=true;$('sell').disabled=true;$('wait').disabled=true;
  $('trade-message').className='trade-message';
  $('trade-message').textContent='正在加载日K线…';
}

async function refresh(){message('正在读取行情数据…');try{const data=await getJson('./data/manifest.json');if(data.schema_version!==1||!Array.isArray(data.stocks))throw new Error('数据结构不兼容');manifest=data;const label=data.is_demo?'【演示数据】尚未接入真实A股':'真实行情快照';const cnDate=Object.fromEntries(new Intl.DateTimeFormat('en-US',{timeZone:'Asia/Shanghai',year:'numeric',month:'2-digit',day:'2-digit'}).formatToParts(new Date()).map(p=>[p.type,p.value]));const today=`${cnDate.year}-${cnDate.month}-${cnDate.day}`;message(`${label} · 数据日期：${data.asof}${!data.is_demo&&data.asof!==today?'（并非今日）':''} · ${data.stock_count}只股票`);$('stat-count').textContent=data.stock_count;$('stat-rps').textContent='RPS'+data.rps_window;$('stat-date').textContent=data.asof.slice(5);$('footer-version').textContent=data.is_demo?'示例数据 · 非真实股票':`行情日期 ${data.asof}`;renderStocks();}
  catch(e){message(`读取失败：${e.message}。请检查网络或先运行数据更新程序。`,true);$('stocks').innerHTML='<div class="empty">暂无可用行情数据</div>';}}
function renderStocks(){const q=$('stock-search').value.trim().toLowerCase(),items=manifest.stocks.filter(s=>s.code.includes(q)||s.name.toLowerCase().includes(q));const panel=$('stocks');panel.replaceChildren();if(!items.length){panel.innerHTML='<div class="empty">没有符合搜索条件的股票</div>';return;}
  for(const s of items){const b=document.createElement('button');b.className='stock-row';b.disabled=!s.has_history;b.innerHTML=`<span><span class="stock-name"></span><span class="stock-code"></span></span><span class="price-col"><strong></strong><small></small></span><span class="rps-col"><strong></strong><small></small></span>`;
    b.querySelector('.stock-name').textContent=s.name;b.querySelector('.stock-code').textContent=s.code+(s.has_history?'':' · 历史K线不可用');b.querySelector('.price-col strong').textContent=yuan(s.price);const change=b.querySelector('.price-col small');change.textContent=pct(s.change_pct);change.className=s.change_pct>=0?'up':'down';b.querySelector('.rps-col strong').textContent=s.rps.toFixed(1);b.querySelector('.rps-col small').textContent='排名 '+s.rank;b.addEventListener('click',()=>openStock(s));panel.appendChild(b);}}
function showTrade(on){$('list-view').hidden=on;$('trade-view').hidden=!on;document.body.classList.toggle('in-trade',on);window.scrollTo({top:0,behavior:'instant'});if(on)requestAnimationFrame(()=>chart?.draw());}
async function openStock(stock){
  const id=++requestId;
  selected=stock;
  const nav=syncStockNavigation();
  $('trade-name').textContent=stock.name;
  $('trade-code').textContent=`${stock.code} · 第 ${nav.index+1}/${nav.total} 只`;
  $('training-rps').textContent='RPS'+manifest.rps_window+' '+stock.rps.toFixed(1);
  clearTradeForLoading();
  showTrade(true);
  try{
    const data=await getJson('./'+stock.history_url);
    if(id!==requestId)return;
    if(data.code!==stock.code||!Array.isArray(data.bars)||data.bars.length<61||data.asof!==manifest.asof)throw new Error('历史数据不完整或日期不匹配');
    const loadedBars=data.bars;
    const key=`${manifest.asof}_${stock.code}`;
    const saved=await loadSession(key);
    if(id!==requestId)return;
    bars=loadedBars;sessionKey=key;
    const start=bars.length-61;
    account=(saved?.version===1&&saved.code===stock.code&&saved.asof===manifest.asof&&Number.isInteger(saved.index)&&saved.index>=start&&saved.index<bars.length)?saved:newAccount(stock.code,manifest.asof,start);
    if(!chart)chart=new StockChart($('chart'),$('chart-tooltip'));
    chart.pan=0;chart.highlight=null;chart.setIndicator(activeIndicator);
    $('chart-container').hidden=false;
    updateTrade();
    requestAnimationFrame(()=>{if(id===requestId)chart.draw();});
  }catch(e){
    if(id!==requestId)return;
    $('trade-message').textContent='K线加载失败：'+e.message+'；可点击↑或↓切换其他股票。';
  }
}
function updateTrade(){if(!account||!bars.length)return;const b=bars[account.index],total=accountValue(account,b.c),gain=(total/INITIAL_CASH-1)*100,end=account.index===bars.length-1,done=account.index-(bars.length-61);
  $('trade-price').textContent=yuan(b.c);$('trade-day').textContent=b.date+(end?' · 已结束':' · 可见最新K线');$('progress-fill').style.width=done/60*100+'%';$('progress-text').textContent=`回放 ${done} / 60 交易日`;
  $('balance-total').textContent=yuan(total);$('balance-cash').textContent=yuan(account.cash);$('balance-shares').textContent=`持仓 ${position(account)} 股`;$('balance-profit').textContent=`收益率 ${pct(gain)}`;$('balance-profit').className=gain>=0?'up':'down';$('buy').disabled=end;$('sell').disabled=end;$('wait').disabled=end;
  $('trade-message').textContent=account.message+(end?' · 今日行情已经完整解锁。':'');$('records').replaceChildren();if(!account.trades.length){$('records').innerHTML='<div class="empty">尚无已成交交易</div>';}else for(const t of [...account.trades].reverse()){
    const d=document.createElement('div');d.className='record';d.innerHTML='<div><div class="type"></div><div class="detail"></div></div><div class="money"></div>';
    const type=d.querySelector('.type');type.textContent=t.side==='buy'?'买入':'卖出';type.className='type '+(t.side==='buy'?'up':'down');d.querySelector('.detail').textContent=`${t.date} · ${t.qty}股 · 手续费${yuan(t.fee)}`;d.querySelector('.money').textContent=yuan(t.price)+' / 股';$('records').appendChild(d);}
  chart.setData(bars,account.index);
}
async function perform(action){
  if(!account||!bars.length)return;
  const id=requestId;
  const q=Number($('quantity').value),out=nextDay(account,bars,action,q);
  $('trade-message').textContent=out.notice;
  $('trade-message').className='trade-message'+(out.success?'':' warn');
  if(!out.success)return;
  const key=sessionKey;
  account=out.account;
  await saveSession(key,out.account);
  if(id!==requestId)return; // The user has already switched to another stock.
  chart.pan=0;chart.highlight=null;$('chart-tooltip').hidden=true;
  updateTrade();
}
$('refresh').addEventListener('click',refresh);$('stock-search').addEventListener('input',()=>manifest&&renderStocks());$('back').addEventListener('click',()=>{requestId++;selected=null;account=null;bars=[];sessionKey='';showTrade(false);});$('prev-stock').addEventListener('click',()=>goToAdjacentStock(-1));$('next-stock').addEventListener('click',()=>goToAdjacentStock(1));$('buy').addEventListener('click',()=>perform('buy'));$('sell').addEventListener('click',()=>perform('sell'));$('wait').addEventListener('click',()=>perform('hold'));$('reset-zoom').addEventListener('click',()=>chart?.reset());$('restart').addEventListener('click',async()=>{if(!account)return;if(!confirm('确定重新开始这只股票的60交易日训练？原有模拟记录将被重置。'))return;account=newAccount(selected.code,manifest.asof,bars.length-61);await saveSession(sessionKey,account);chart.reset();chart.setIndicator(activeIndicator);updateTrade();});
document.querySelectorAll('.switch-btn').forEach(btn=>btn.addEventListener('click',()=>setIndicator(btn.dataset.indicator)));
syncIndicatorButtons();
if('serviceWorker' in navigator)window.addEventListener('load',()=>navigator.serviceWorker.register('./sw.js').catch(e=>console.warn('PWA安装/离线缓存可能不可用',e)));
refresh();
