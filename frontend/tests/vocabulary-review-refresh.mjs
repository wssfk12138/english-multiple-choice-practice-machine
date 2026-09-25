import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import vm from 'node:vm'
import ts from 'typescript'
// 沙盒按当前 VocabularyView 的到期队列状态机重建（批次3重写后）：
// 提取全部队列/评分函数，模块级 let/const 状态在预置段声明；
// scheduleDueQueueTopUp / waitForVocabularyIdle / ensureReviewDetails / cacheReviewDetail
// 属于时序与详情补全设施，不在本测语义内，用空桩代替。
const read = p => readFileSync(new URL('../src/' + p, import.meta.url), 'utf8')
const compile = s => ts.transpileModule(s, {compilerOptions:{module:ts.ModuleKind.CommonJS,target:ts.ScriptTarget.ES2022}}).outputText
const queueModule = { exports:{} }; vm.runInNewContext(compile(read('vocabulary-due-queue.ts')), queueModule)
const source = read('views/VocabularyView.vue').split('<script setup lang="ts">')[1].split('</script>')[0]
const ast = ts.createSourceFile('view.ts', source, ts.ScriptTarget.Latest, true)
const names = ['clearNotice','showNotice','newReviewCommandId','persistDueQueue','reviewRevision','refreshDueQueue','dueQueueUrl','duePageSignature','extractReviewRows','setScheduledWords','applyDueQueue','dueFillWanted','finishFill','readWholeDueQueue','fillDueQueue','topUpDueQueue','fillDueQueueForRating','loadDueQueue','applyCounts','displayCount','refreshReviewOnForeground','rate']
const functions = ast.statements.filter(s => ts.isFunctionDeclaration(s) && names.includes(s.name?.text)).map(s => s.getText(ast)).join('\n')
const prelude = `
let dueQueue = reconcileDueQueue(null, []);
let dueQueueStorageKey = '';
let dueRefreshPromise: Promise<void>|null = null;
let reviewDisposed = false;
let reviewCommandId = '';
let noticeTimer: ReturnType<typeof setTimeout>|null = null;
let reviewLoadToken = 0;
let dueQueueTimer: ReturnType<typeof setTimeout>|null = null;
const DUE_FIRST_PAGE = 12;
const DUE_PAGE = 48;
const DUE_PREFETCH_THRESHOLD = 4;
let dueQueueWords = new Map<string, any>();
let dueQueueOffset = 0;
let dueQueueComplete = false;
let dueQueueSaved: any = null;
let dueLoadToken = 0;
let dueFillPromise: Promise<any>|null = null;
let dueHeadSignature = '';
let dueHeadReview = -1;
let reviewSnapshotRevision = '';
async function scheduleDueQueueTopUp(): Promise<void> {}
function ensureReviewDetails(): void {}
function cacheReviewDetail(_id: number, _detail: any): void {}
async function waitForVocabularyIdle(_ms: number): Promise<void> {}
`
const word = id => ({id,term:'word'+id,normalized_term:'word'+id,translation_status:'ready',created_at:'2020-01-01'})
const value = v => ({value:v})
function setup() {
  const ctx = { ...queueModule.exports, exports:{}, Map, JSON, String, Date, setTimeout, clearTimeout,
    currentBankScope:false, reviewMode:value(true), reviewKind:value('scheduled'), reviewSaving:value(false), reviewIndex:value(0), reveal:value(true), error:value(''), notice:value(''), scheduledWords:value([word(1),word(2)]), countsLoaded:value(false), reviewLoading:value(false), listLoaded:value(false), listRevision:value(''), reviewDetails:value({}),
    counts:value({}), document:{visibilityState:'visible'}, localStorage:{getItem:()=>null,setItem:()=>{}}, load:async()=>{},
    persistListSnapshot:()=>{}, refreshVocabularyStatus:async()=>({counts:{},revision:''}) }
  ctx.reviewItems = {get value(){return ctx.scheduledWords.value}}
  ctx.reviewWord = {get value(){return ctx.reviewItems.value[ctx.reviewIndex.value]}}
  ctx.words = [word(1),word(2)]; ctx.posts = []; ctx.getCount = 0
  ctx.get = async()=>{ ctx.getCount++; return {items:ctx.words} }
  ctx.post = async(path)=>{ctx.posts.push(path);ctx.words=ctx.words.filter(w=>path!== '/vocabulary/'+w.id+'/review')}
  vm.createContext(ctx)
  vm.runInContext(compile(prelude+functions+'\nthis.api={rate,refreshDueQueue,refreshReviewOnForeground,dispose:()=>{reviewDisposed=true}};'),ctx)
  return ctx
}
{
 // 已删除/已同步的卡不在新队列里：身份变化让评分落在刷新后的卡上，不再向旧卡发请求
 const c=setup();c.words=[word(2)]; await c.api.rate('good')
 assert.equal(c.posts.length,0);assert.equal(c.reviewWord.value.id,2);assert.equal(c.reveal.value,false);assert.equal(c.reviewSaving.value,false)
}
{
 // 刷新带回已评分（last_reviewed_at）版本时，兜底身份随之变化，评分被跳过
 const c=setup();c.words=[{...word(1),last_reviewed_at:'2026-09-10'},word(2)];await c.api.rate('good');assert.equal(c.posts.length,0)
}
{
 // 保存失败：错误可见、停留在当前卡、 saving 复位
 const c=setup();c.post=async()=>{throw Error('save failed')};await c.api.rate('good')
 assert.equal(c.reviewWord.value.id,1);assert.match(c.error.value,/save failed/);assert.equal(c.reviewSaving.value,false)
}
{
 // 队列刷新单飞：并发调用共享同一 Promise，只发一次请求
 const c=setup();let release;c.get=()=>{c.getCount++;return new Promise(resolve=>release=resolve)}
 const a=c.api.refreshDueQueue(),b=c.api.refreshDueQueue();assert.equal(a,b);assert.equal(c.getCount,1)
 release({items:[word(2)]});await a;assert.equal(c.reveal.value,false)
}
{
 // 重复评分：第二个评分在 saving 期间被丢弃，只发一次请求
 const c=setup();await Promise.all([c.api.rate('good'),c.api.rate('good')]);assert.equal(c.posts.length,1);assert.equal(c.reviewWord.value.id,2)
}
{
 // 前台恢复：页面隐藏不请求；可见时刷新队列；dispose 后不再改状态
 const c=setup();c.document.visibilityState='hidden';await c.api.refreshReviewOnForeground();assert.equal(c.getCount,0)
 c.document.visibilityState='visible';c.words=[word(2)];await c.api.refreshReviewOnForeground();assert.equal(c.reviewWord.value.id,2)
 c.api.dispose();c.words=[word(3)];await c.api.refreshDueQueue();assert.equal(c.reviewWord.value.id,2)
}

// 后端 revision 随卡片精确携带；延迟响应不吞掉其他视图的切换
{
 const c=setup();c.scheduledWords.value[0].review_revision='revision-1';c.words[0].review_revision='revision-1'
 let release;const posted=new Promise(resolve=>{c.post=async(path,body)=>{c.posts.push(path);assert.equal(body.expected_revision,'revision-1');resolve();return new Promise(done=>release=done)}})
 const saving=c.api.rate('hard');await posted;c.reviewMode.value=false;release();await saving
 assert.equal(c.reviewWord.value.id,1);assert.equal(c.reviewSaving.value,false)
}
{
 const c=setup();let release;const posted=new Promise(resolve=>{c.post=async()=>{resolve();return new Promise(done=>release=done)}})
 const saving=c.api.rate('hard');await posted;c.scheduledWords.value=[word(2)];release();await saving
 assert.equal(c.reviewWord.value.id,2);assert.equal(c.reviewMode.value,true);assert.equal(c.reviewSaving.value,false)
}
{
 // 过期卡按新契约走服务端 409：丢弃缓存行、重读队列、停在刷新后的同一张卡
 const c=setup();c.scheduledWords.value[0].review_revision='old';c.words=[{...word(1),review_revision:'new'},word(2)]
 let posts=0
 c.post=async(_path,body)=>{posts++;assert.equal(body.expected_revision,'old');const cause=new Error('复习记录已更新');cause.status=409;throw cause}
 await c.api.rate('hard')
 assert.equal(posts,1);assert.equal(c.reviewWord.value.id,1);assert.equal(c.reviewSaving.value,false);assert.equal(c.reveal.value,false)
}

console.log('PASS due review: deleted/synced cards, failed save, overlapping refresh, duplicate rating, foreground, disposal and 409 recovery')
