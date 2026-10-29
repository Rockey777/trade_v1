export const INITIAL_CASH = 100000;
export function newAccount(code,asof,initialIndex){return {version:1,code,asof,index:initialIndex,cash:INITIAL_CASH,lots:[],trades:[],message:'已进入T−60，选择操作以解锁下一交易日。'};}
const round=(x)=>Math.round((x+Number.EPSILON)*100)/100;
export const position=(a)=>a.lots.reduce((s,l)=>s+l.qty,0);
export const accountValue=(a,price)=>round(a.cash+position(a)*price);
export function fees(side,price,qty){const gross=price*qty;return round(Math.max(5,gross*.00025)+gross*.00001+(side==='sell'?gross*.0005:0));}
function lockedLimit(b,previous,side){
  // Approximate 10% main-board daily limit. Historical special-risk warnings in UI.
  if(!previous)return false;
  const locked=Math.abs(b.h-b.l)<.001 && Math.abs(b.o-b.c)<.001;
  return locked && (side==='buy'?b.o>=previous.c*1.098:b.o<=previous.c*.902);
}
export function nextDay(account,bars,action,qty=0){
  if(account.index>=bars.length-1)return {account,notice:'已经到达最新交易日，训练结束。',success:false};
  const a=structuredClone(account), current=a.index, next=current+1, b=bars[next];
  if(!['buy','sell','hold'].includes(action))throw Error('无效操作');
  if(action!=='hold'&&(!Number.isInteger(qty)||qty<=0))return {account,notice:'请输入正整数股数。',success:false};
  if(action==='buy'&&qty%100!==0)return {account,notice:'买入股数必须是100股的整数倍。',success:false};
  if(action==='sell'){
    const canSell=a.lots.filter(l=>l.buyIndex<=current).reduce((s,l)=>s+l.qty,0);
    if(qty>canSell)return {account,notice:`可卖股数仅 ${canSell}（含T+1限制）。`,success:false};
  }
  if(action==='buy'&&round(a.cash-(b.o*qty+fees('buy',b.o,qty)))<0)return {account,notice:'资金不足，按次日开盘价及费用估算不可成交。',success:false};
  let notice='观望一天，已解锁新K线。';
  if(action!=='hold'){
    if(lockedLimit(b,bars[current],action)){notice=`${b.date} 疑似一字${action==='buy'?'涨':'跌'}停，订单未成交；仍已推进一天。`;}
    else{
      const cost=fees(action,b.o,qty),gross=round(b.o*qty);
      if(action==='buy'){
        a.cash=round(a.cash-gross-cost);
        a.lots.push({qty,buyIndex:next});
        notice=`${b.date} 开盘买入 ${qty} 股，成交价 ¥${b.o.toFixed(2)}，费用 ¥${cost.toFixed(2)}。`;
      } else {
        a.cash=round(a.cash+gross-cost);
        let remaining=qty;
        for(const lot of a.lots){if(lot.buyIndex>current||remaining===0)continue;const sold=Math.min(lot.qty,remaining);lot.qty-=sold;remaining-=sold;}
        a.lots=a.lots.filter(l=>l.qty>0);
        notice=`${b.date} 开盘卖出 ${qty} 股，成交价 ¥${b.o.toFixed(2)}，费用 ¥${cost.toFixed(2)}。`;
      }
      a.trades.push({side:action,date:b.date,price:b.o,qty,fee:cost,index:next});
    }
  }
  a.index=next;a.message=notice;
  if(next===bars.length-1)notice+=' 已抵达今天，模拟结束。';
  return {account:a,notice,success:true};
}
