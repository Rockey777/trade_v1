import {indicators} from './indicators.js';
const UP='#f06970',DOWN='#31bea0',GRID='#304158',TEXT='#8599b6',GOLD='#f1be57',BLUE='#5bb3f9',WHITE='#dce9f7';
const clamp=(v,min,max)=>Math.min(max,Math.max(min,v));
const fmt=v=>v==null?'—':Number(v).toFixed(2);
export class StockChart {
  constructor(canvas,tooltip){this.canvas=canvas;this.tooltip=tooltip;this.ctx=canvas.getContext('2d');this.visible=57;this.pan=0;this.highlight=null;this.bars=[];this.current=0;this.points=new Map();this.dragStart=null;this.pinchBase=null;
    this.resizeObserver=new ResizeObserver(()=>this.draw());this.resizeObserver.observe(canvas);
    canvas.addEventListener('pointerdown',e=>this.down(e));canvas.addEventListener('pointermove',e=>this.move(e));canvas.addEventListener('pointerup',e=>this.up(e));canvas.addEventListener('pointercancel',e=>this.up(e));
    canvas.addEventListener('wheel',e=>{e.preventDefault();this.visible=clamp(Math.round(this.visible+(e.deltaY>0?7:-7)),18,150);this.pan=clamp(this.pan,0,Math.max(0,this.current));this.draw();},{passive:false});
  }
  setData(bars,current){const old=this.bars;this.bars=bars;this.current=current;this.ind=indicators(bars);if(old!==bars){this.pan=0;this.highlight=null;}this.draw();}
  reset(){this.visible=57;this.pan=0;this.highlight=null;this.tooltip.hidden=true;this.draw();}
  geometry(){const w=this.canvas.clientWidth,h=this.canvas.clientHeight;return {w,h,left:44,right:8,plot:w-52,zones:[{top:8,height:Math.round(h*.49),title:'K线 / 元'},{top:Math.round(h*.50),height:Math.round(h*.13),title:'VOL / 手'},{top:Math.round(h*.64),height:Math.round(h*.17),title:'MACD (12,26,9)'},{top:Math.round(h*.82),height:Math.round(h*.16),title:'DMI (14)'}]};}
  visibleRange(){const end=clamp(this.current-this.pan,0,this.current),start=Math.max(0,end-this.visible+1);return {start,end,bars:this.bars.slice(start,end+1)};}
  draw(){if(!this.bars.length)return;const cv=this.canvas,dpr=window.devicePixelRatio||1,w=cv.clientWidth,h=cv.clientHeight;if(w<40||h<40)return;if(cv.width!==Math.round(w*dpr)||cv.height!==Math.round(h*dpr)){cv.width=Math.round(w*dpr);cv.height=Math.round(h*dpr);}const ctx=this.ctx;ctx.setTransform(dpr,0,0,dpr,0,0);ctx.clearRect(0,0,w,h);ctx.fillStyle='#121d2d';ctx.fillRect(0,0,w,h);
    const g=this.geometry(),range=this.visibleRange(),len=range.bars.length,dx=g.plot/this.visible;
    this.grid(g);this.candles(ctx,g,range,dx);this.volume(ctx,g,range,dx);this.macd(ctx,g,range,dx);this.dmi(ctx,g,range,dx);
    ctx.fillStyle=TEXT;ctx.font='10px system-ui';ctx.textAlign='left';ctx.fillText(range.bars[0]?.date.slice(5)||'',g.left,g.h-2);ctx.textAlign='right';ctx.fillText(range.bars.at(-1)?.date.slice(5)||'',g.w-g.right,g.h-2);
    if(this.highlight!=null&&this.highlight>=range.start&&this.highlight<=range.end){const x=g.left+(this.highlight-range.start+.5)*dx;ctx.strokeStyle='#cbd7e9';ctx.globalAlpha=.52;ctx.setLineDash([3,3]);ctx.beginPath();ctx.moveTo(x,6);ctx.lineTo(x,g.h-14);ctx.stroke();ctx.setLineDash([]);ctx.globalAlpha=1;}
    this.last={g,range,dx};
  }
  grid(g){const ctx=this.ctx;ctx.lineWidth=.7;ctx.font='10px system-ui';for(const z of g.zones){ctx.strokeStyle='#293950';ctx.beginPath();ctx.moveTo(0,z.top+z.height);ctx.lineTo(g.w,z.top+z.height);ctx.stroke();for(let i=1;i<4;i++){let y=z.top+i*z.height/4;ctx.globalAlpha=.45;ctx.beginPath();ctx.moveTo(g.left,y);ctx.lineTo(g.w-g.right,y);ctx.stroke();ctx.globalAlpha=1;}ctx.textAlign='left';ctx.fillStyle='#bac8dd';ctx.font='10px system-ui';ctx.fillText(z.title,g.left+3,z.top+12);}}
  candles(ctx,g,r,dx){const data=r.bars,z=g.zones[0],values=data.flatMap(b=>[b.l,b.h]),min=Math.min(...values),max=Math.max(...values),padding=Math.max((max-min)*.09,.03),bottom=z.top+z.height-7,top=z.top+19,span=max-min+padding*2,yy=p=>bottom-(p-(min-padding))/span*(bottom-top);
    ctx.fillStyle=TEXT;ctx.textAlign='right';ctx.font='10px system-ui';ctx.fillText(fmt(max),g.left-4,top+8);ctx.fillText(fmt(min),g.left-4,bottom);
    data.forEach((b,i)=>{const x=g.left+(i+.5)*dx,open=yy(b.o),close=yy(b.c),up=b.c>=b.o;ctx.strokeStyle=up?UP:DOWN;ctx.fillStyle=up?UP:DOWN;ctx.lineWidth=1;ctx.beginPath();ctx.moveTo(x,yy(b.h));ctx.lineTo(x,yy(b.l));ctx.stroke();const bw=clamp(dx*.59,1.5,12);const y=Math.min(open,close);ctx.fillRect(x-bw/2,y,bw,Math.max(1,Math.abs(close-open)));});}
  volume(ctx,g,r,dx){const z=g.zones[1],max=Math.max(1,...r.bars.map(b=>b.v)),bottom=z.top+z.height-3,usable=z.height-20;ctx.fillStyle=TEXT;ctx.textAlign='right';ctx.font='9px system-ui';ctx.fillText(max>=10000?(max/10000).toFixed(0)+'万':String(Math.round(max)),g.left-4,z.top+13);r.bars.forEach((b,i)=>{const x=g.left+(i+.5)*dx,bw=clamp(dx*.57,1.5,11);ctx.fillStyle=b.c>=b.o?UP:DOWN;const hh=usable*b.v/max;ctx.fillRect(x-bw/2,bottom-hh,bw,hh);});}
  macd(ctx,g,r,dx){const z=g.zones[2],I=this.ind,values=[];for(let i=r.start;i<=r.end;i++)values.push(I.dif[i],I.dea[i],I.hist[i]);const range=Math.max(.005,...values.map(v=>Math.abs(v)))*1.18,top=z.top+19,bottom=z.top+z.height-6,half=(top+bottom)/2,scale=(bottom-top)/2/range;
    ctx.strokeStyle='#43536b';ctx.beginPath();ctx.moveTo(g.left,half);ctx.lineTo(g.w-g.right,half);ctx.stroke();
    for(let i=r.start;i<=r.end;i++){const x=g.left+(i-r.start+.5)*dx,v=I.hist[i],h=Math.abs(v)*scale;ctx.fillStyle=v>=0?UP:DOWN;ctx.fillRect(x-clamp(dx*.23,1,5),v>=0?half-h:half,clamp(dx*.46,1,10),Math.max(.8,h));}
    this.line(ctx,g,r,dx,I.dif,v=>half-v*scale,GOLD);this.line(ctx,g,r,dx,I.dea,v=>half-v*scale,BLUE);
    ctx.fillStyle=GOLD;ctx.font='9px system-ui';ctx.fillText('DIF',g.w-75,z.top+12);ctx.fillStyle=BLUE;ctx.fillText('DEA',g.w-46,z.top+12);
  }
  dmi(ctx,g,r,dx){const z=g.zones[3],I=this.ind,keys=['pdi','mdi','adx','adxr'],colors=[UP,DOWN,GOLD,BLUE];const values=[];for(let i=r.start;i<=r.end;i++)for(const key of keys){const v=I[key][i];if(v!=null)values.push(v);}const max=Math.max(50,...values)*1.08,top=z.top+20,bottom=z.top+z.height-6,scale=(bottom-top)/max;
    keys.forEach((key,k)=>this.line(ctx,g,r,dx,I[key],v=>bottom-v*scale,colors[k]));
    ctx.font='9px system-ui';ctx.textAlign='right';ctx.fillStyle=UP;ctx.fillText('PDI',g.w-122,z.top+12);ctx.fillStyle=DOWN;ctx.fillText('MDI',g.w-86,z.top+12);ctx.fillStyle=GOLD;ctx.fillText('ADX',g.w-46,z.top+12);ctx.fillStyle=BLUE;ctx.fillText('ADXR',g.w-5,z.top+12);
  }
  line(ctx,g,r,dx,series,yfun,color){ctx.strokeStyle=color;ctx.lineWidth=1.5;ctx.beginPath();let started=false;for(let i=r.start;i<=r.end;i++){const v=series[i];if(v==null||!Number.isFinite(v)){started=false;continue;}const x=g.left+(i-r.start+.5)*dx,y=yfun(v);if(!started)ctx.moveTo(x,y);else ctx.lineTo(x,y);started=true;}ctx.stroke();}
  down(e){this.canvas.setPointerCapture(e.pointerId);this.points.set(e.pointerId,{x:e.clientX,y:e.clientY});this.dragStart??={x:e.clientX,pan:this.pan};if(this.points.size===2){const pts=[...this.points.values()];this.pinchBase={distance:Math.abs(pts[0].x-pts[1].x)||1,visible:this.visible};}}
  move(e){if(!this.points.has(e.pointerId))return;this.points.set(e.pointerId,{x:e.clientX,y:e.clientY});if(this.points.size>=2&&this.pinchBase){const pts=[...this.points.values()],dist=Math.abs(pts[0].x-pts[1].x)||1;this.visible=clamp(Math.round(this.pinchBase.visible*this.pinchBase.distance/dist),18,150);this.draw();return;}
    if(this.dragStart){const dx=this.geometry().plot/this.visible,moved=e.clientX-this.dragStart.x;if(Math.abs(moved)>5){this.pan=clamp(Math.round(this.dragStart.pan+moved/dx),0,this.current);this.highlight=null;this.tooltip.hidden=true;this.draw();}}}
  up(e){if(!this.points.has(e.pointerId))return;const moved=this.dragStart?Math.abs(e.clientX-this.dragStart.x):1000;this.points.delete(e.pointerId);this.pinchBase=null;if(!this.points.size){this.dragStart=null;if(moved<6)this.inspect(e);}}
  inspect(e){if(!this.last)return;const {g,range,dx}=this.last,x=e.clientX-this.canvas.getBoundingClientRect().left,idx=clamp(range.start+Math.floor((x-g.left)/dx),range.start,range.end);if(!this.bars[idx])return;this.highlight=idx;const b=this.bars[idx],I=this.ind;
    this.tooltip.textContent=`${b.date}  开 ${fmt(b.o)}  高 ${fmt(b.h)}  低 ${fmt(b.l)}  收 ${fmt(b.c)}\n成交量 ${Math.round(b.v).toLocaleString()} 手 · MACD ${fmt(I.hist[idx])}\nPDI ${fmt(I.pdi[idx])}  MDI ${fmt(I.mdi[idx])}  ADX ${fmt(I.adx[idx])}  ADXR ${fmt(I.adxr[idx])}`;
    this.tooltip.hidden=false;this.draw();}
}
