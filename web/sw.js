const STATIC_CACHE='astock-static-v4',DATA_CACHE='astock-data-v4';
const ASSETS=['./','./index.html','./styles.css','./app.js','./chart.js','./indicators.js','./trading.js','./store.js','./manifest.webmanifest','./assets/icon-192.png','./assets/icon-512.png'];
self.addEventListener('install',e=>{e.waitUntil(caches.open(STATIC_CACHE).then(c=>c.addAll(ASSETS)).then(()=>self.skipWaiting()));});
self.addEventListener('activate',e=>{e.waitUntil(Promise.all([caches.keys().then(names=>Promise.all(names.filter(n=>![STATIC_CACHE,DATA_CACHE].includes(n)).map(n=>caches.delete(n)))),self.clients.claim()]));});
self.addEventListener('fetch',e=>{
  const r=e.request;if(r.method!=='GET'||new URL(r.url).origin!==self.location.origin)return;
  const isData=new URL(r.url).pathname.includes('/data/');
  if(isData){e.respondWith(fetch(r).then(async resp=>{if(resp.ok)(await caches.open(DATA_CACHE)).put(r,resp.clone());return resp;}).catch(async()=> (await caches.match(r))||Response.error()));return;}
  e.respondWith(fetch(r).then(async resp=>{if(resp.ok)(await caches.open(STATIC_CACHE)).put(r,resp.clone());return resp;}).catch(async()=>(await caches.match(r))||Response.error()));
});
