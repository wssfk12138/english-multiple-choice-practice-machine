import assert from 'node:assert/strict'
import {createRequire} from 'node:module'
import {createServer} from 'vite'
import vue from '@vitejs/plugin-vue'
import {fileURLToPath} from 'node:url'
const require=createRequire(import.meta.url)
const {chromium}=require(process.env.PLAYWRIGHT_MODULE||'playwright')
const root=fileURLToPath(new URL('../',import.meta.url))
const server=await createServer({root,configFile:false,plugins:[vue()],server:{host:'127.0.0.1',port:0},logLevel:'error'})
await server.listen();const browser=await chromium.launch({headless:true})
try{
 const page=await browser.newPage({viewport:{width:640,height:400}})
 await page.goto(server.resolvedUrls.local[0])
 const result=await page.evaluate(async()=>{
  const {createApp,h,reactive,nextTick}=await import('/node_modules/.vite/deps/vue.js')
  const {createRouter,createMemoryHistory}=await import('/node_modules/.vite/deps/vue-router.js')
  const {default:Assistant}=await import('/src/components/AiAssistant.vue')
  const {default:Keys}=await import('/src/components/NamedModelKeys.vue')
  const {assistantSession:s}=await import('/src/services/assistantSession.ts')
  const calls=[];let fail=false
  const profile=reactive({id:788,keys:Array.from({length:8},(_,i)=>({id:'key-'+i,name:'Synthetic alias '+i,mask:'****1234'})),selected_key_id:'key-7',has_api_key:true})
  window.fetch=async(url,options={})=>{
   const path=String(url),body=options.body?JSON.parse(options.body):null;calls.push({path,body})
   if(path.includes('/keys')){if(body.action==='rename')profile.keys.find(k=>k.id===body.identity).name=body.name;return new Response(JSON.stringify(profile))}
   if(path.endsWith('/ai/selector-models'))return new Response(JSON.stringify({models:[{profile_id:1,profile_name:'Synthetic',model_id:'synthetic',display_name:'Synthetic'}]}))
   if(path.endsWith('/ai/chat'))return new Response(JSON.stringify(fail?{detail:'Synthetic failure'}:{conversation_id:98,message:{role:'assistant',content:'Synthetic response'}}),{status:fail?500:200})
   return new Response(JSON.stringify([]))
  }
  const wait=async()=>{await nextTick();await new Promise(r=>setTimeout(r,90))}
  const host=document.createElement('div');document.body.append(host)
  const app=createApp({render:()=>h(Keys,{profile})});app.mount(host);await wait()
  const selectedVisible=[...host.querySelectorAll('.key-row')].some(e=>e.textContent.includes('Synthetic alias 7')&&getComputedStyle(e).display!=='none')
  host.querySelector('.key-more').click();await wait()
  const keyDialog=document.querySelector('.key-sheet'),allKeys=keyDialog.querySelectorAll('[role=option]').length
  keyDialog.querySelector('button[aria-label="改名"]').click();await wait()
  const alias=keyDialog.querySelector('input[aria-label="密钥名称"]');alias.value='Synthetic renamed';alias.dispatchEvent(new Event('input',{bubbles:true}));alias.dispatchEvent(new Event('blur'));await wait()
  const renamed=calls.some(c=>c.body?.action==='rename'&&c.body.name==='Synthetic renamed')
  window.dispatchEvent(new KeyboardEvent('keydown',{key:'Escape',bubbles:true}));await wait();const closed=!document.querySelector('.key-sheet')
  app.unmount()
  Object.assign(s,{controller:null});s.messages.value=[];s.conversationId.value=null;s.input.value='';s.attachments.value=[];s.loading.value=false;s.models.value=[];s.reasoningEffort.value='high'
  const router=createRouter({history:createMemoryHistory(),routes:[{path:'/',component:Assistant}]});await router.push('/');await router.isReady()
  const assistant=createApp({render:()=>h(Assistant)});assistant.use(router);assistant.mount(host);await wait()
  let text=host.querySelector('textarea');text.value='Synthetic draft';text.dispatchEvent(new Event('input',{bubbles:true}));await wait()
  text.dispatchEvent(new KeyboardEvent('keydown',{key:'Enter',shiftKey:true,bubbles:true,cancelable:true}));text.dispatchEvent(new KeyboardEvent('keydown',{key:'Enter',isComposing:true,bubbles:true,cancelable:true}));await wait()
  const guarded=calls.filter(c=>c.path.endsWith('/ai/chat')).length===0
  fail=true;text.dispatchEvent(new KeyboardEvent('keydown',{key:'Enter',bubbles:true,cancelable:true}));await wait();const failedVisible=s.messages.value.some(m=>m.role==='user'&&m.content==='Synthetic draft')&&s.messages.value.some(m=>m.error)&&!s.loading.value
  fail=false;s.input.value='';s.attachments.value=[{id:1,name:'synthetic.png',size:1,dataUrl:'data:image/png;base64,AA=='}];await wait();host.querySelector('form').dispatchEvent(new Event('submit',{bubbles:true,cancelable:true}));await wait()
  const attachment=calls.some(c=>c.path.endsWith('/ai/chat')&&c.body.attachments?.[0]?.name==='synthetic.png')
  const historyToggle=host.querySelector('[aria-label="显示历史对话"]');historyToggle?.click();await wait()
  const backdrop=host.querySelector('.ai-history-dismiss-area');const dismiss=!!backdrop;backdrop?.click();await wait();const dismissed=!host.querySelector('.ai-history-dismiss-area')
  s.reasoningEffort.value='high';host.querySelector('.ai-new-chat').click();await wait();const reset=s.reasoningEffort.value===''
  assistant.unmount();host.remove()
  return {selectedVisible,allKeys,renamed,closed,guarded,failedVisible,attachment,dismiss,dismissed,reset}
 })
 for(const [key,value]of Object.entries(result))assert.equal(value,key==='allKeys'?8:true,key)
 console.log('PASS synthetic UI transport: selected hidden key remains reachable, all-key sheet/rename/Escape, Enter/Shift/IME guards, visible failure and preserved user message, attachment-only send, history backdrop, new-dialog reasoning reset')
}finally{await browser.close();await server.close()}
