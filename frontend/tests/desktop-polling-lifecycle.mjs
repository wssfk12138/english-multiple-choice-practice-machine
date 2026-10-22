import assert from 'node:assert/strict'
import {readFileSync} from 'node:fs'
import vm from 'node:vm'
import ts from 'typescript'
function extract(file,names){
 const source=readFileSync(new URL('../src/views/'+file,import.meta.url),'utf8').split('<script setup lang="ts">')[1].split('</script>')[0]
 const ast=ts.createSourceFile('view.ts',source,ts.ScriptTarget.Latest,true)
 return ast.statements.filter(s=>ts.isFunctionDeclaration(s)&&names.includes(s.name?.text)).map(s=>s.getText(ast)).join('\n')
}
const compile=source=>ts.transpileModule(source,{compilerOptions:{target:ts.ScriptTarget.ES2022}}).outputText
function fixture(){
 const intervals=new Set(),pending=[]
 const ctx={intervals,pending,nextTick:async()=>{},requestAnimationFrame:fn=>fn(),window:{setInterval:fn=>(intervals.add(fn),intervals.size),clearInterval:()=>intervals.clear()},post:()=>new Promise(resolve=>pending.push(resolve))}
 vm.createContext(ctx)
 vm.runInContext(compile(`let reviewDisposed=false,translationRefreshTimer=0,translationRefreshTick=0;const enrichment={value:null},error={value:''};function refreshTranslationStatuses(){};`+extract('VocabularyView.vue',['startTranslationRefresh','stopTranslationRefresh','startPendingTranslations','enrichmentAction'])+`;this.api={startPendingTranslations,enrichmentAction,start:startTranslationRefresh,dispose:()=>{reviewDisposed=true;stopTranslationRefresh()}}`),ctx)
 return ctx
}
for(const action of ['translation','enrichment']){
 const c=fixture(),run=action==='translation'?c.api.startPendingTranslations():c.api.enrichmentAction('inventory')
 for(let i=0;i<6;i++)await Promise.resolve()
 assert.equal(c.pending.length,1);c.api.dispose();c.pending[0]({});await run
 assert.equal(c.intervals.size,0,action+' late response must not recreate a disposed view timer')
}
{
 const c=fixture();c.api.start();c.api.start();assert.equal(c.intervals.size,1);c.api.dispose();c.api.start();assert.equal(c.intervals.size,0)
}
{
 const intervals=new Set(),ctx={window:{clearInterval:()=>intervals.clear(),setInterval:fn=>(intervals.add(fn),1)},advanceVocabulary:()=>{}}
 vm.createContext(ctx);vm.runInContext(compile('let homeDisposed=false,vocabularyTimer=null;'+extract('DashboardView.vue',['startVocabularyRotation'])+';this.api={start:startVocabularyRotation,dispose:()=>{homeDisposed=true;window.clearInterval(vocabularyTimer)}}'),ctx)
 // The actual starter is called by the mounted async loader after it resolves.
 let resolve;const delayed=new Promise(r=>resolve=r).then(()=>ctx.api.start());ctx.api.dispose();resolve();await delayed;assert.equal(intervals.size,0)
}
console.log('PASS late translation/enrichment/home responses cannot recreate disposed timers; live polling remains single-instance')
