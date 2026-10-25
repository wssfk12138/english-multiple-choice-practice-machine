import assert from 'node:assert/strict'
import { createServer } from 'vite'
import { createRequire } from 'node:module'
import { fileURLToPath } from 'node:url'

const require = createRequire(import.meta.url)
const { chromium } = require(process.env.PLAYWRIGHT_MODULE || 'playwright')
const root = fileURLToPath(new URL('../', import.meta.url))

const fixture = function () {
  const option = key => ({ stable_key: key, label: key, content: 'Option ' + key, metadata: {}, content_blocks: [] })
  const activeSession = {
    id: 1, status: 'active', mode: 'paper', score: 0, max_score: 2, submitted_at: null,
    content_choice_required: false,
    units: [{
      id: 11, title: 'Reading A', year: 2024, unit_type: 'reading', sequence: 1,
      passage: 'Repro passage.', submission: null,
      questions: [{
        id: 101, number: 1, stem: 'Repro stem.', question_type: 'choice', score: 2,
        metadata: {}, stem_blocks: [],
        options: [option('A'), option('B')], option_order: ['A', 'B'], user_answer: '',
      }],
    }, {
      id: 12, title: 'Reading B', year: 2024, unit_type: 'reading', sequence: 2,
      passage: 'Second passage.', submission: null,
      questions: Array.from({length: 5}, (_, i) => ({
        id: 200 + i, number: i + 1, stem: 'Second stem ' + i, question_type: 'choice', score: 2,
        metadata: {}, stem_blocks: [],
        options: [option('A'), option('B')], option_order: ['A', 'B'], user_answer: '',
      })),
    }, {
      id: 13, title: 'Listening A', year: 2024, unit_type: 'listening', sequence: 3,
      passage: 'Listening passage.', submission: null, shared_data: { audio_tracks: [] },
      questions: Array.from({length: 5}, (_, i) => ({
        id: 300 + i, number: i + 1, stem: 'Listening stem ' + i, question_type: 'choice', score: 2,
        metadata: {}, stem_blocks: [],
        options: [option('A'), option('B')], option_order: ['A', 'B'], user_answer: '',
      })),
    }],
  }
  globalThis.calls = []
  globalThis.mockApi = async (path, body) => {
    globalThis.calls.push({ path, body })
    if (path === '/practice/sessions/1') return activeSession
    if (path.startsWith('/practice/sessions/')) return { kept: false }
    return {}
  }
  globalThis.reproLog = []
}

const entry = '(' + fixture.toString() + ')();' +
  'const {createApp,h}=await import("vue");' +
  'const {createRouter,createWebHashHistory,RouterView}=await import("vue-router");' +
  'const {default:Practice}=await import("/src/views/PracticeView.vue");' +
  'const {default:DialogHost}=await import("/src/components/AppDialogHost.vue");' +
  'await import("/src/styles.css");await import("/src/study-theme.css");' +
  'const Blank={render:()=>h("div",{class:"blank-page"},"blank")};' +
  'const router=createRouter({history:createWebHashHistory(),routes:[' +
  '{path:"/practice/:id",component:Practice},{path:"/library",component:Blank},{path:"/:p(.*)",component:Blank}]});' +
  'router.afterEach((to,from,failure)=>globalThis.reproLog.push({to:to.fullPath,from:from.fullPath,failure:!!failure,type:failure&&failure.type,msg:failure&&failure.message}));' +
  'router.beforeEach((to)=>{globalThis.reproLog.push({before:to.fullPath,state:history.state});return true});' +
  'router.onError(e=>globalThis.reproLog.push({routerError:String(e)}));' +
  'createApp({render:()=>[h(RouterView),h(DialogHost)]}).use(router).mount("#app");'
  'globalThis.reproReady=true;'

const server = await createServer({ configFile: false, root, optimizeDeps: { noDiscovery: true, include: ['vue', 'vue-router', 'lucide-vue-next'] }, plugins: [
  { name: 'repro-timed-exit', enforce: 'pre',
    resolveId(id) {
      if (id === 'virtual:repro') return id
      if (id === '../api' || id.endsWith('/src/api')) return 'virtual:repro-api'
      if (id.endsWith('/components/ListeningPlayer.vue')) return 'virtual:repro-player'
      if (id === 'jszip') return 'virtual:repro-jszip'
      if (id === 'qrcode') return 'virtual:repro-qrcode'
    },
    load(id) {
      if (id === 'virtual:repro') return entry
      if (id === 'virtual:repro-api') return 'const api=p=>globalThis.mockApi(p);export {api};export const get=p=>globalThis.mockApi(p);export const post=(p,b)=>globalThis.mockApi(p,b);export const put=(p,b)=>globalThis.mockApi(p,b);export const patch=(p,b)=>globalThis.mockApi(p,b);export const del=p=>globalThis.mockApi(p,{});'
      if (id === 'virtual:repro-player') return 'import {h} from "vue";export default {props:["tracks","seekable","timerPaused","resumeOnTimerContinue","currentQuestionNumber","answeredCount"],render:()=>h("section",{class:"listening-stub"})}'
      if (id === 'virtual:repro-jszip') return 'export default class JSZip{constructor(){} file(){} async generateAsync(){return new Blob([])}}'
      if (id === 'virtual:repro-qrcode') return 'export default {toCanvas:async()=>{},toDataURL:async()=>""}'
    },
    configureServer(s) { s.middlewares.use((req, res, next) => {
      if (!req.url.startsWith('/__repro')) return next()
      res.setHeader('Content-Type', 'text/html; charset=utf-8')
      res.end('<!doctype html><html><meta name="viewport" content="width=device-width, initial-scale=1"><body><div id="app"></div><script type="module" src="/@id/virtual:repro"></script></body></html>')
    }) },
  }, (await import('@vitejs/plugin-vue')).default()], server: { host: '127.0.0.1', port: 0 } })

let browser
try {
  await server.listen()
  browser = await chromium.launch({ headless: true })
  const page = await browser.newPage({ viewport: { width: 1280, height: 800 } })
  const errors = []
  page.on('pageerror', error => errors.push(error.message))
  await page.route('**/*', route => new URL(route.request().url()).hostname === '127.0.0.1' ? route.continue() : route.abort())
  await page.goto(server.resolvedUrls.local[0] + '__repro#/practice/1')
  await page.waitForTimeout(4000)
  console.log('LOADED', JSON.stringify(await page.evaluate(() => ({ hash: location.hash, html: document.body.innerText.slice(0, 500), calls: globalThis.calls }))))
  console.log('LOAD_ERRORS', JSON.stringify(errors))
  await page.locator('.timer-overlay').waitFor({ timeout: 15000 })
  await page.getByRole('button', { name: '开始计时', exact: true }).click()
  await page.locator('.timer-overlay').waitFor({ state: 'detached' })
  await page.waitForTimeout(600)
  await page.locator('.unit-tab').nth(1).click()
  await page.waitForTimeout(400)
  await page.locator('.unit-tab').nth(2).click()
  await page.waitForTimeout(500)
  console.log('LISTENING', JSON.stringify(await page.evaluate(() => ({ body: document.body.innerText.slice(0, 300) }))))
  await page.locator('.practice-page').evaluate(el => { const s = el.querySelector('.content-scroll, .exam-scroll, main, .practice-layout'); if (s) s.scrollTop = 900 })
  await page.waitForTimeout(300)
  await page.mouse.wheel(0, 1200)
  await page.waitForTimeout(300)
  const before = await page.evaluate(() => ({ hash: location.hash, log: globalThis.reproLog }))
  console.log('BEFORE', JSON.stringify(before))
  await page.getByRole('button', { name: '休息一下', exact: true }).click()
  await page.locator('.timer-pause-overlay').waitFor()
  console.log('PAUSED_HTML', JSON.stringify((await page.locator('.practice-page').innerText()).slice(0, 400)))
  await page.locator('.timer-pause-overlay').getByRole('button', { name: '退出练习', exact: true }).click()
  await page.waitForTimeout(1200)
  console.log('CONFIRM_VISIBLE', JSON.stringify(await page.evaluate(() => ({ dialog: document.querySelector('.app-dialog')?.innerText, hash: location.hash }))))
  console.log('HISTORY_BEFORE', JSON.stringify(await page.evaluate(() => ({ state: history.state, length: history.length }))))
  await page.evaluate(() => { window.addEventListener('popstate', () => globalThis.reproLog.push({ popstate: location.hash, state: history.state })) })
  await page.getByRole('dialog').getByRole('button', { name: '继续退出', exact: true }).click()
  await page.waitForTimeout(4000)
  console.log('HISTORY_AFTER', JSON.stringify(await page.evaluate(() => ({ state: history.state, length: history.length }))))
  const after = await page.evaluate(() => ({ hash: location.hash, log: globalThis.reproLog, practice: !!document.querySelector('.practice-page') }))
  console.log('AFTER_PAUSE_EXIT', JSON.stringify(after))
  assert.equal(after.hash, '#/library', 'confirming exit from a paused listening practice must leave the page')
  assert.equal(after.practice, false)
  assert.deepEqual(errors, [])
  if (await page.locator('.practice-page').count()) {
    await page.keyboard.press('Escape')
    await page.getByRole('button', { name: '继续练习', exact: true }).click()
    await page.waitForTimeout(400)
  }
  if (!(await page.locator('.practice-top button', { hasText: '退出' }).count())) {
    console.log('NO_EXIT_BUTTON')
    console.log('FINAL', JSON.stringify(await page.evaluate(() => ({ hash: location.hash, body: document.body.innerText.slice(0, 400), overlay: !!document.querySelector('.timer-overlay') }))))
    console.log('ERRORS', JSON.stringify(errors))
    process.exit(0)
  }
  await page.locator('.practice-top button', { hasText: '退出' }).first().click()
  await page.waitForTimeout(2500)
  console.log('AFTER1', JSON.stringify(await page.evaluate(() => ({ hash: location.hash, log: globalThis.reproLog, blank: !!document.querySelector('.blank-page'), practice: !!document.querySelector('.practice-page'), overlay: !!document.querySelector('.timer-overlay'), calls: globalThis.calls }))))
  await page.locator('.practice-top button', { hasText: '退出' }).first().click({ timeout: 3000 }).catch(() => null)
  await page.waitForTimeout(2000)
  console.log('AFTER2', JSON.stringify(await page.evaluate(() => ({ hash: location.hash, log: globalThis.reproLog, practice: !!document.querySelector('.practice-page'), overlay: !!document.querySelector('.timer-overlay') }))))
  console.log('ERRORS', JSON.stringify(errors))
} finally {
  await browser?.close()
  await server.close()
}
