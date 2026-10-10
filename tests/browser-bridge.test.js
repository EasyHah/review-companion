const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const code = fs.readFileSync(path.join(__dirname,'../assets/browser-bridge.js'),'utf8');
async function setup(host) {
  let state = {session:'a',label:'test',repository:'/synthetic',host,input_mode:host==='pi'?'draft':'copy'};
  let interval, reset=0, urls=[], cleanUrl;
  const elements={};
  const window={ReviewCompanion:{resetSession(){reset++;}}};
  const context={window,URLSearchParams,Promise,Error,JSON,String,AbortSignal,setTimeout:fn=>{fn();},setInterval:fn=>{interval=fn;},
    location:{hash:'#token=synthetic'},sessionStorage:{getItem(){},setItem(){}},history:{replaceState(_,__,url){cleanUrl=url;}},
    document:{getElementById(id){return elements[id] || (elements[id]={textContent:''});}},
    fetch:async(url,options)=>{
      urls.push(url);assert.equal(options.headers.Authorization,'Bearer synthetic');
      let data=url==='/api/session'?state:url==='/api/draft'?{id:'d'}:url==='/api/draft-status'?{status:'filled'}:{structuredContent:{ok:true}};
      return {ok:true,json:async()=>data};
    }};
  vm.runInNewContext(code,context);await new Promise(resolve=>setImmediate(resolve));
  return {window,elements,urls,get cleanUrl(){return cleanUrl;},get reset(){return reset;},switch:async(next)=>{state=next;await interval();await new Promise(resolve=>setImmediate(resolve));}};
}
test('Claude and Codex use copy-only bridge and remove URL capability fragment',async()=>{
  for(const host of ['claude','codex','pi_web']) {
    const runtime=await setup(host);
    assert.equal(runtime.window.reviewCompanionDraft,undefined);
    assert.equal(runtime.cleanUrl,'/');
    assert.match(runtime.elements['host-session'].textContent,/复制问题后自行发送/);
    await runtime.window.reviewCompanionHost.callTool('read_review',{});
    assert.ok(!runtime.urls.some(url=>url.includes('draft')));
  }
});
test('Pi enables draft preparation and session changes clear stale prompt state',async()=>{
  const runtime=await setup('pi');
  assert.equal(typeof runtime.window.reviewCompanionDraft,'function');
  assert.equal((await runtime.window.reviewCompanionDraft('synthetic question')).filled,true);
  await runtime.switch({session:'b',label:'new',repository:'/synthetic',host:'codex',input_mode:'copy'});
  assert.equal(runtime.reset,1);assert.equal(runtime.window.reviewCompanionDraft,undefined);
});
