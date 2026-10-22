import assert from 'node:assert/strict'
import { test } from 'node:test'
import { readFileSync } from 'node:fs'
import vm from 'node:vm'
import ts from 'typescript'
import * as vue from 'vue'
import * as timer from '../src/services/practiceTimer.ts'
const source = readFileSync(new URL('../src/views/PracticeView.vue',import.meta.url),'utf8').split('<script setup lang="ts">')[1].split('</script>')[0]
const exports = 'timerState,timerNow,timerNotice,timerSaveError,timerReady,session,listeningPlayer,load,startTimedPractice,startWithoutTimer,pauseTimer,resumeTimer,flushVocabularyOnVisibilityChange,flushVocabularyOnPageHide'
const code = ts.transpileModule(source + '\nexport {'+exports+'}', {compilerOptions:{module:ts.ModuleKind.CommonJS,target:ts.ScriptTarget.ES2022}}).outputText
const deferred = () => { let resolve,reject; const promise=new Promise((a,b)=>{resolve=a;reject=b});return {promise,resolve,reject} }
const settle = async () => { for(let i=0;i<8;i++) await new Promise(r=>setImmediate(r)); await vue.nextTick() }
function fixture() {
  const state={now:0,failSave:false,reads:[],posts:[],removed:0}
  const storage=new Map();const native=new Map();const hooks={};const events={};const scope=vue.effectScope()
  const route=vue.reactive({params:{id:'1'}})
  const session=id=>({id:Number(id),mode:'custom',status:'active',units:[{id:1,unit_type:'reading',questions:[]}]})
  const api={get:async path=>session(path.split('/').at(-1)),post:async path=>path.endsWith('submit')?session(1):{},put:async()=>{}}
  const router={afterEach:fn=>{hooks.after=fn;return ()=>{}},replace:async path=>{route.params.id=path.split('/').at(-1)}}
  const win={addEventListener:(n,fn)=>events[n]=fn,removeEventListener:n=>delete events[n],setInterval:fn=>{hooks.tick=fn;return 1},clearInterval:()=>{},setTimeout:()=>0,scrollTo:()=>{}}
  const doc={visibilityState:'visible',documentElement:{dataset:{platform:'web'},scrollTop:0},activeElement:null,addEventListener:win.addEventListener,removeEventListener:win.removeEventListener}
  const nativeState=deferred()
  const app={addListener:async(n,fn)=>{native.set(n,fn);return {remove:async()=>{native.delete(n);state.removed++}}},getState:()=>nativeState.promise}
  const context={exports:{},performance:{now:()=>state.now},window:win,document:doc,console,
    localStorage:{getItem:k=>storage.get(k)??null,setItem:(k,v)=>{if(state.failSave)throw Error('full');storage.set(k,v)},removeItem:k=>storage.delete(k)},
    require:name=>name==='vue'?{...vue,onMounted:fn=>hooks.mount=fn,onBeforeUnmount:fn=>hooks.unmount=fn}
      :name==='vue-router'?{useRoute:()=>route,useRouter:()=>router,onBeforeRouteLeave:fn=>hooks.leave=fn,onBeforeRouteUpdate:fn=>hooks.update=fn}
      :name==='@capacitor/app'?{App:app}:name.endsWith('/practiceTimer')?timer
      :name==='../api'?{get:async(...args)=>{state.reads.push(args[0]);return api.get(...args)},post:async(...args)=>{state.posts.push({path:args[0],timer:storage.get('linjian-practice-timer-1')});return api.post(...args)},put:api.put}:{} }
  scope.run(()=>vm.runInNewContext(code,context))
  return {a:context.exports,state,storage,native,hooks,route,api,doc,events,nativeState,session,
    async mount(){hooks.mount();await settle();nativeState.resolve({isActive:true});await settle()},close(){hooks.unmount?.();scope.stop()}}
}
test('browser visibility settles elapsed time and preserves manual pause',async()=>{
 const f=fixture();await f.mount();const a=f.a
 a.startTimedPractice();f.state.now=600000;f.doc.visibilityState='hidden';a.flushVocabularyOnVisibilityChange();await settle()
 assert.equal(a.timerState.value.elapsedMs,600000)
 f.state.now+=86400000;f.doc.visibilityState='visible';a.flushVocabularyOnVisibilityChange();assert.equal(a.timerState.value.mode,'running')
 f.state.now+=300000;f.hooks.tick();assert.equal(a.timerState.value.elapsedMs,900000)
 a.pauseTimer();f.doc.visibilityState='hidden';a.flushVocabularyOnVisibilityChange();f.doc.visibilityState='visible';a.flushVocabularyOnVisibilityChange();assert.equal(a.timerState.value.pauseReason,'manual');f.close()
})
test('old load cannot replace a new route and cancelled navigation resumes automatic pause only',async()=>{
 const f=fixture();await f.mount();f.a.startTimedPractice();f.state.now=1000
 await f.hooks.leave();assert.equal(f.a.timerState.value.mode,'paused');f.hooks.after(null,null,Error('cancelled'));assert.equal(f.a.timerState.value.mode,'running')
 const old=deferred();f.api.get=path=>path.endsWith('/1')?old.promise:Promise.resolve(f.session(2))
 const pending=f.a.load();await f.hooks.update();f.route.params.id='2';await settle();old.resolve(f.session(1));await pending
 assert.equal(f.a.session.value.id,2);assert.equal(f.a.timerState.value,null);f.close()
})
test('load after unmount cannot install a session or write another timer',async()=>{
 const f=fixture();const read=deferred();f.api.get=()=>read.promise;f.hooks.mount();f.close();read.resolve(f.session(1));await settle()
 assert.equal(f.a.session.value,null);assert.equal(f.storage.has('linjian-practice-timer-1'),false)
})
