// All indicators use exclusively current-or-earlier candles (causal calculations).
export function indicators(bars) {
  const n=bars.length, dif=[], dea=[], hist=[], pdi=[], mdi=[], adx=[], adxr=[];
  if(!n)return {dif,dea,hist,pdi,mdi,adx,adxr};
  let ema12=bars[0].c, ema26=bars[0].c, signal=0;
  for(const b of bars){
    ema12+=(b.c-ema12)*2/13;ema26+=(b.c-ema26)*2/27;
    const d=ema12-ema26;signal+=(d-signal)*2/10;
    dif.push(d);dea.push(signal);hist.push((d-signal)*2);
  }
  // 通达信常见DMI(14,6): 14日滚动SUM(TR,+DM,-DM),
  // 6日MA(DX), ADXR=(ADX+REF(ADX,6))/2.
  const period=14, lag=6, trList=[0], plusList=[0], minusList=[0], dx=[];
  for(let i=0;i<n;i++){
    if(i===0){pdi.push(null);mdi.push(null);adx.push(null);adxr.push(null);dx.push(null);continue;}
    const prev=bars[i-1], b=bars[i],up=b.h-prev.h,down=prev.l-b.l;
    plusList.push(up>0&&up>down?up:0);
    minusList.push(down>0&&down>up?down:0);
    trList.push(Math.max(b.h-b.l,Math.abs(b.h-prev.c),Math.abs(b.l-prev.c)));
    if(i<period){pdi.push(null);mdi.push(null);adx.push(null);adxr.push(null);dx.push(null);continue;}
    const tr=trList.slice(i-period+1,i+1).reduce((a,b)=>a+b,0);
    const plus=plusList.slice(i-period+1,i+1).reduce((a,b)=>a+b,0);
    const minus=minusList.slice(i-period+1,i+1).reduce((a,b)=>a+b,0);
    const pi=tr?100*plus/tr:0,mi=tr?100*minus/tr:0;
    pdi.push(pi);mdi.push(mi);
    const d=(pi+mi)?100*Math.abs(pi-mi)/(pi+mi):0;dx.push(d);
    const recent=dx.slice(i-lag+1,i+1);
    if(recent.length<lag||recent.some(v=>v==null)){adx.push(null);adxr.push(null);continue;}
    const a=recent.reduce((x,y)=>x+y,0)/lag;adx.push(a);
    const old=adx[i-lag];adxr.push(old==null?null:(a+old)/2);
  }
  return {dif,dea,hist,pdi,mdi,adx,adxr};
}
