import fs from 'node:fs/promises'
import path from 'node:path'
import http from 'node:http'
import os from 'node:os'
import assert from 'node:assert/strict'
import {createRequire} from 'node:module'
const require=createRequire(import.meta.url)
const {chromium}=require(process.env.PLAYWRIGHT_MODULE || 'playwright')
const work=process.env.DESKTOP_UI_OUTPUT || await fs.mkdtemp(path.join(os.tmpdir(),'desktop-ui-'))
const roots=[new URL('../../',import.meta.url).pathname.replace(/^\/([A-Z]:)/i,'$1')]
const labels={total:10,labeled:4,locked:4,review_pending:0,remaining:6,percentage:40,years:[2025,2026],year:null,paper_ids:[]}
const profiles=[{id:1,name:'布局验证合成题库',is_active:true,paper_count:2,question_count:10}]
const draft={id:10,filename:'合成试卷.docx',status:'draft',profile_id:1,created_at:'2026-10-12T01:00:00',warnings:[],draft:{year:2026,subject:'英语一',title:'合成校对试卷',detected_format:'英语一',answer_source:'人工校对',answer_sources:{1:'manual'},answers:{1:'A'},units:[{sequence:1,title:'合成篇目',unit_type:'reading',passage:'A synthetic passage.',questions:[{number:1,stem:'Synthetic question?',options:[{key:'A',content:'Synthetic answer A'},{key:'B',content:'Synthetic answer B'}]}]}]}}
const esq={id:20,filename:'合成题库.esq',status:'preview',profile_id:1,created_at:'2026-10-12T02:00:00',draft_data:{manifest:{schemaVersion:'1.1'}},preview:{title:'合成 ESQ 题库',publisher:'布局验证',contentVersion:'test',totals:{papers:80,units:80,questions:80,assets:0},conflicts:Array.from({length:80},(_,i)=>({paperKey:'year'+i,year:2000+i,existing:true,title:'冲突年份'+i}))}}
const member=id=>({id,term:id===43?'pneumonoultramicroscopicsilicovolcanoconiosis':'evidence'+id,identity:'synthetic-'+id})
function detail(id,variant){return {...member(id),daily_study_identity:'synthetic-'+id,common_meaning:'证据；依据；可用于判断和推理的事实。',contextual_meaning:'用于研究结论的事实依据。',phonetic:'/ˈevɪdəns/',part_of_speech:'n.',latest_sentence:'Researchers compare evidence and revise their explanations. '.repeat(variant==='long'?70:4),occurrences:[{year:2026,unit_title:'合成样本'}],generated_example:{sentence:'The evidence supports this conclusion.',translation:'证据支持这一结论。'},memory_hint:'把 evidence 与 evident 联系记忆，从清楚可见的事实想到证据。',note:'已有笔记。\n'.repeat(variant==='long'?160:3),morphology:{forms:[{label:'形容词',word:'evident'}],note:'通过后缀联系词形。'},similar_forms:[{word:'evident',note:'形容词'}],synonyms:[{word:'proof',note:'强调能够证实结论的证据。'}],antonyms:[{word:'assumption',note:'未经证实的假设。'}],...(variant==='short'?{memory_hint:'',contextual_meaning:'',latest_sentence:'',generated_example:null,note:'',morphology:null,similar_forms:[],synonyms:[],antonyms:[]}: {})}}
function api(url,method,variant){
 const u=new URL(url,'http://local'),p=u.pathname.slice(4)
 if(p==='/question-bank-profiles')return profiles
 if(p==='/imports')return [draft]
 if(p==='/imports/10')return {...draft,draft_data:draft.draft}
 if(p==='/question-banks/imports')return [esq]
 if(p==='/question-banks/imports/20')return esq
 if(p==='/ai/question-labels/status')return labels
 if(p==='/ai/question-labels')return []
 if(p==='/ai/profiles')return []
 if(p==='/vocabulary/daily-study/catalog')return {scope_key:'synthetic:all',revision:'v2:1',items:Array.from({length:43},(_,i)=>member(i+1))}
 if(p==='/vocabulary/daily-study/status')return {scope_key:'synthetic:all',revision:'v2:1'}
 const m=p.match(/^\/vocabulary\/(\d+)$/);if(m)return detail(Number(m[1]),variant)
 if(p==='/dashboard')return {stats:{},recent:[]}
 if(method!=='GET')return {ok:true}
 throw Error('Unmocked API '+p)
}
function serve(dir){return http.createServer(async(req,res)=>{try{let f=path.join(dir,decodeURIComponent(new URL(req.url,'http://local').pathname));if(!f.startsWith(dir))throw Error('Path');const st=await fs.stat(f).catch(()=>null);if(!st?.isFile())f=path.join(dir,'index.html');const type=({'.html':'text/html; charset=utf-8','.js':'text/javascript; charset=utf-8','.css':'text/css; charset=utf-8','.png':'image/png','.webp':'image/webp','.svg':'image/svg+xml'})[path.extname(f)]||'application/octet-stream';res.writeHead(200,{'Content-Type':type});res.end(await fs.readFile(f))}catch(e){res.writeHead(500);res.end(String(e))}})}
await fs.mkdir(path.join(work,'shots'),{recursive:true})
const browser=await chromium.launch({headless:true})
const evidence=[]
try{
for(const [index,root] of roots.entries()){
 const channel='desktop',dir=path.join(root,'frontend/dist')
 const server=serve(dir);await new Promise(r=>server.listen(0,'127.0.0.1',r));const origin='http://127.0.0.1:'+server.address().port
 const context=await browser.newContext({viewport:{width:1366,height:768}}),page=await context.newPage()
 const errors=[],calls=[];let variant='normal',failDetail=0
 page.on('pageerror',e=>errors.push(e.message))
 await page.route('**/api/**',async route=>{const req=route.request();calls.push({url:new URL(req.url()).pathname,method:req.method()});try{if(new URL(req.url()).pathname==='/api/vocabulary/'+failDetail)throw Error('Synthetic failed detail');await route.fulfill({json:api(req.url(),req.method(),variant)})}catch(e){await route.fulfill({status:500,json:{detail:String(e)}})}})
 const shot=async name=>{const f=path.join(work,'shots',channel+'-'+name+'.png');await page.screenshot({path:f});return f}
 const geometry=()=>page.evaluate(()=>({width:innerWidth,height:innerHeight,zoom:getComputedStyle(document.documentElement).zoom,bodyWidth:document.body.scrollWidth,font:parseFloat(getComputedStyle(document.body).fontSize),shellHeight:document.querySelector('.daily-study-shell')?.getBoundingClientRect().height}))
 const noOverflow=async name=>{const g=await geometry();assert.ok(g.bodyWidth<=g.width+1, name+' horizontal overflow '+JSON.stringify(g));assert.equal(g.zoom,'1');evidence.push({channel,name,...g})}
 for(const size of [{width:1920,height:1080},{width:1366,height:768},{width:1280,height:720},{width:1024,height:600},{width:800,height:600},{width:600,height:700},{width:360,height:640}]){
  await page.setViewportSize(size);await page.goto(origin+'/imports');await page.locator('.import-entrance').first().waitFor();await noOverflow('home-'+size.width)
  assert.equal(await page.locator('.import-entrances').getByRole('button').count(),2)
  const positions=await page.evaluate(()=>{const h=document.querySelector('.import-page-head').getBoundingClientRect(),n=document.querySelector('.import-nav').getBoundingClientRect(),t=document.querySelector('.import-page-title').getBoundingClientRect();return {headRight:h.right,navRight:n.right,navTop:n.top,titleBottom:t.bottom,headWidth:h.width}})
  assert.ok(Math.abs(positions.headRight-positions.navRight)<2,'right aligned nav')
  if(positions.headWidth>600)assert.ok(positions.navTop<positions.titleBottom,'same header row')
  await shot('import-home-'+size.width)
  await page.getByRole('button',{name:'从文档导入',exact:false}).click();await page.getByRole('heading',{name:'从文档导入',exact:true}).waitFor();await noOverflow('document-'+size.width)
  await page.locator('.import-attachments summary').click();await noOverflow('attachments-'+size.width)
  await page.getByRole('button',{name:'导入记录',exact:true}).click();await page.locator('.import-record-open').filter({hasText:'合成试卷.docx'}).click();await page.getByRole('heading',{name:'结构化草稿校对器'}).waitFor();await noOverflow('draft-'+size.width)
  if(size.width===1366||size.width===360)await shot('document-draft-'+size.width)
  await page.getByRole('button',{name:'导入记录',exact:true}).click();await page.locator('.import-record-open').filter({hasText:'合成题库.esq'}).click();await page.getByRole('button',{name:'发布题库包',exact:true}).waitFor();await noOverflow('esq-80-conflicts-'+size.width)
  await page.getByRole('button',{name:'发布题库包',exact:true}).click();await page.locator('[role=alert]').filter({hasText:'处理方式'}).waitFor();assert.equal(calls.filter(c=>c.url.endsWith('/publish')).length,0)
  if(size.width===1366||size.width===360)await shot('esq-conflicts-'+size.width)
  await page.getByRole('button',{name:'智能标注中心',exact:true}).click();await page.locator('#label-scope').waitFor();await noOverflow('labels-'+size.width)
 }
 // Files, unsaved draft and conflict choices survive workspace navigation.
 await page.setViewportSize({width:1366,height:768});await page.goto(origin+'/imports?section=document');await page.locator('input[type=file]').first().setInputFiles({name:'selected-synthetic.docx',mimeType:'application/vnd.openxmlformats-officedocument.wordprocessingml.document',buffer:Buffer.from('test')})
 await page.getByRole('button',{name:'导入记录',exact:true}).click();await page.locator('.import-record-open').filter({hasText:'合成试卷.docx'}).click();const titleField=page.locator('.draft-field-wide input');await titleField.fill('未保存的合成修改');await page.getByRole('button',{name:'智能标注中心',exact:true}).click();await page.getByRole('button',{name:'导入题库',exact:true}).click();await page.getByRole('button',{name:'从文档导入',exact:false}).click();assert.equal(await titleField.inputValue(),'未保存的合成修改');assert.equal(await page.locator('input[type=file]').first().evaluate(e=>e.files[0].name),'selected-synthetic.docx')
 await page.getByRole('button',{name:'导入记录',exact:true}).click();await page.locator('.import-record-open').filter({hasText:'合成题库.esq'}).click();await page.getByRole('button',{name:'保留本地',exact:true}).first().click();await page.getByRole('button',{name:'导入记录',exact:true}).click();await page.locator('.import-record-open').filter({hasText:'合成试卷.docx'}).click();assert.equal(await titleField.inputValue(),'未保存的合成修改');await page.getByRole('button',{name:'导入记录',exact:true}).click();await page.locator('.import-record-open').filter({hasText:'合成题库.esq'}).click();assert.equal(await page.getByRole('button',{name:'保留本地',exact:true}).first().evaluate(e=>e.classList.contains('secondary')),false)
 evidence.push({channel,name:'draft-file-conflict-retention',passed:true})
 // Daily study is independently usable at all desktop widths, including portrait windows.
 const ready=async id=>page.waitForFunction(id=>document.querySelector('.study-scroll[data-identity]')?.dataset.identity==='synthetic-'+id&&document.querySelector('.daily-study-shell')?.dataset.switching==='false'&&document.querySelector('.study-card')?.getAttribute('aria-busy')!=='true',id)
 for(const size of [{width:1920,height:1080},{width:1366,height:768},{width:1280,height:720},{width:1024,height:600},{width:800,height:600},{width:600,height:800},{width:360,height:640}]){
  await page.setViewportSize(size);await page.goto(origin+'/vocabulary/daily-study');await ready(1);await noOverflow('daily-'+size.width)
  assert.equal(await page.locator('.study-card details,.study-card summary,.expand-all').count(),0)
  const fields=await page.locator('.study-card [data-study-section]').evaluateAll(es=>es.map(e=>e.dataset.studySection));assert.deepEqual(fields,['memory_hint','common_meaning','contextual','source_sentence','model_sentence','note','morphology','similar_forms','synonyms','antonyms'])
  assert.ok(await page.locator('.study-footer').evaluate(e=>e.getBoundingClientRect().bottom)<=size.height+1,'footer in viewport')
  const columns=await page.locator('.reading-flow').evaluate(e=>getComputedStyle(e).columnCount);assert.equal(columns,size.width>750?'2':'1')
  await shot('daily-'+size.width)
 }
 await page.setViewportSize({width:1366,height:768});variant='long';await page.goto(origin+'/vocabulary/daily-study');await ready(1);await page.locator('.study-scroll[data-identity]').evaluate(e=>e.scrollTop=250);await page.waitForTimeout(180)
 await page.locator('.study-footer .next').click();await ready(2);await page.locator('.study-footer .previous').click();await ready(1);assert.equal(await page.locator('.study-scroll[data-identity]').evaluate(e=>e.scrollTop),250)
 await page.locator('.study-scroll[data-identity]').evaluate(e=>e.scrollTop=0)
 await page.waitForFunction(()=>document.querySelector('.study-deck')?.dataset.gesture==='idle')
 const box=await page.locator('.word-heading').boundingBox(),x=box.x+box.width*.6,y=box.y+30
 await page.mouse.move(x,y);await page.mouse.down();await page.mouse.move(x-45,y,{steps:4});assert.ok(Math.abs(Number(await page.locator('.study-deck').getAttribute('data-offset')))>20);await page.mouse.up();await ready(1);await page.waitForTimeout(250);assert.equal(Number(await page.locator('.study-deck').getAttribute('data-offset')),0)
 await page.mouse.move(x,y);await page.mouse.down();await page.mouse.move(x-120,y,{steps:5});await page.mouse.up();await ready(2)
 await page.keyboard.press('ArrowLeft');await ready(1);await page.mouse.move(x,y);await page.mouse.down();await page.mouse.move(x+120,y,{steps:5});await page.mouse.up();await page.waitForTimeout(260);await ready(1)
 // A failed unprefetched detail retains the visible card and supports retry.
 failDetail=4;await page.getByRole('button',{name:'4 evidence4',exact:true}).click();await page.getByRole('button',{name:'重新读取',exact:true}).waitFor();assert.equal(await page.locator('.study-scroll[data-identity]').getAttribute('data-identity'),'synthetic-1');failDetail=0;await page.getByRole('button',{name:'重新读取',exact:true}).click();await ready(4)
 const cdp=await context.newCDPSession(page),heading=await page.locator('.word-heading').boundingBox(),point={x:heading.x+heading.width*.6,y:heading.y+30,id:1}
 await cdp.send('Input.dispatchTouchEvent',{type:'touchStart',touchPoints:[point]});await cdp.send('Input.dispatchTouchEvent',{type:'touchMove',touchPoints:[{...point,x:point.x-45}]});await page.waitForFunction(()=>Number(document.querySelector('.study-deck').dataset.offset)<-20);await cdp.send('Input.dispatchTouchEvent',{type:'touchEnd',touchPoints:[]});await page.waitForTimeout(250);await ready(4)
 evidence.push({channel,name:'daily-mouse-touch-rebound-boundary-resume-retry',passed:true})
 for(const dark of [false,true])for(const size of [{width:1366,height:768},{width:600,height:800}]){
  await page.setViewportSize(size);await page.evaluate(d=>{localStorage.setItem('linjian-theme',d?'dark':'light');document.documentElement.classList.toggle('dark',d)},dark);await page.goto(origin+'/vocabulary/daily-study');await ready(4);await noOverflow('theme-daily-'+dark+'-'+size.width);await shot('daily-'+(dark?'dark':'light')+'-'+size.width)
  await page.goto(origin+'/imports');await page.locator('.import-entrance').first().waitFor();await noOverflow('theme-home-'+dark+'-'+size.width);if(dark)await shot('import-dark-'+size.width)
 }
 variant='short';await page.goto(origin+'/vocabulary/daily-study');await ready(4);assert.equal(await page.locator('.reading-flow.single-short').count(),1)
 assert.equal(await page.locator('.reading-flow').evaluate(e=>getComputedStyle(e).columnCount),'auto')
 await context.close();await new Promise(r=>server.close(r));assert.deepEqual(errors,[],'Browser errors')
 evidence.push({channel,name:'browser-errors',count:errors.length})
}
}finally{await browser.close();await fs.writeFile(path.join(work,'browser-evidence.json'),JSON.stringify(evidence,null,2))}
console.log(JSON.stringify({checks:evidence.length,evidence:path.join(work,'browser-evidence.json')}))
