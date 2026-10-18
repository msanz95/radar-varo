'use strict';
const CACHE='radar-varo-shell-v1';
const ASSETS=['./','./index.html','./style.css','./app.js','./assets/icon.svg','./assets/icon-192.png','./assets/icon-512.png','./manifest.webmanifest'];
self.addEventListener('install',event=>{event.waitUntil(caches.open(CACHE).then(cache=>cache.addAll(ASSETS)).then(()=>self.skipWaiting()));});
self.addEventListener('activate',event=>{event.waitUntil(caches.keys().then(keys=>Promise.all(keys.filter(k=>k.startsWith('radar-varo-')&&k!==CACHE).map(k=>caches.delete(k)))).then(()=>self.clients.claim()));});
self.addEventListener('fetch',event=>{
  const url=new URL(event.request.url);
  if(event.request.method!=='GET'||url.origin!==self.location.origin)return;
  // Never cache any JSON/data. Offline is an explicit unavailable state, not a frozen price.
  if(url.pathname.includes('/data/')||url.pathname.endsWith('.json')){event.respondWith(fetch(event.request,{cache:'no-store'}));return;}
  event.respondWith(fetch(event.request).then(response=>{if(response.ok){const copy=response.clone();caches.open(CACHE).then(cache=>cache.put(event.request,copy));}return response;}).catch(()=>caches.match(event.request).then(cached=>cached||Response.error())));
});
