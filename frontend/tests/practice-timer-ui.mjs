import assert from 'node:assert/strict'
import { mkdir } from 'node:fs/promises'
import { createRequire } from 'node:module'
import { join } from 'node:path'
import { fileURLToPath } from 'node:url'
import { createServer } from 'vite'
import vue from '@vitejs/plugin-vue'

// 视觉验收（模型执行）：练习计时 S2 语义——提示弹层、手动休息、自动暂停恢复、旧记录迁移提示。
// 运行：PLAYWRIGHT_MODULE=<playwright 路径> PRACTICE_TIMER_OUTPUT=<截图目录> node tests/practice-timer-ui.mjs
const require = createRequire(import.meta.url)
const { chromium } = require(process.env.PLAYWRIGHT_MODULE || 'playwright')
const root = fileURLToPath(new URL('../', import.meta.url))
const output = process.env.PRACTICE_TIMER_OUTPUT
if (output) await mkdir(output, { recursive: true })

const stubs = {
  router: 'const params={id:"1"};export const useRoute=()=>({get params(){return params}});export const useRouter=()=>({afterEach:()=>()=>{},push:async()=>{},replace:async(p)=>{params.id=String(p).split("/").pop()}});export const onBeforeRouteLeave=()=>{};export const onBeforeRouteUpdate=()=>{};',
  api: 'export const get=(p)=>globalThis.mockApi(p);export const post=(p,b)=>globalThis.mockApi(p,b);export const put=(p,b)=>globalThis.mockApi(p,b);',
  dialogs: 'export const confirmDialog=async()=>true;',
  player: 'import {h} from "vue";export default {name:"ListeningPlayerStub",props:["tracks","seekable","timerPaused","resumeOnTimerContinue","currentQuestionNumber","answeredCount"],render:()=>h("section",{class:"listening-stub"},"listening-stub")};',
}

const fixture = function () {
  // 按查询参数在应用挂载前注入计时记录夹具，绕开卸载钩子的检查点写回。
  const kind = new URLSearchParams(location.search).get('timerFixture')
  if (kind === 'legacy') {
    localStorage.setItem('linjian-practice-timer-1', JSON.stringify({ mode: 'running', elapsedMs: 61000, startedAt: Date.now() - 3600000 }))
  }
  if (kind === 'recovery') {
    localStorage.setItem('linjian-practice-timer-1', JSON.stringify({ version: 2, mode: 'paused', elapsedMs: 125000, pauseReason: 'recovery' }))
  }
  const option = (key, content) => ({ stable_key: key, label: key, content, metadata: {}, content_blocks: [] })
  const activeSession = {
    id: 1, status: 'active', mode: 'paper', score: 0, max_score: 2, submitted_at: null,
    content_choice_required: false,
    units: [
      {
        id: 11, title: 'Reading A', year: 2024, unit_type: 'reading', sequence: 1,
        passage: 'Timer fixture passage.', submission: null,
        questions: [{
          id: 101, number: 1, stem: 'Timer question stem.', question_type: 'choice', score: 2,
          metadata: {}, stem_blocks: [],
          options: [option('A', 'Option A'), option('B', 'Option B')],
          option_order: ['A', 'B'], user_answer: '',
        }],
      },
    ],
  }
  globalThis.mockApi = async (path) => {
    if (path === '/practice/sessions/1') return activeSession
    if (path.startsWith('/practice/sessions/')) return { kept: false }
    throw new Error('Unexpected mock API: ' + path)
  }
}
const entry = '(' + fixture.toString() + ')();' +
  'import {createApp,h} from "vue";import View from "/src/views/PracticeView.vue";import "/src/styles.css";import "/src/study-theme.css";' +
  'createApp({render:()=>h(View)}).mount("#app");'

const server = await createServer({ configFile: false, root, optimizeDeps: { noDiscovery: true, include: ['vue', 'lucide-vue-next'] }, plugins: [
  { name: 'isolated-practice-timer', enforce: 'pre',
    resolveId(id) {
      if (id === 'virtual:practice-timer') return id
      if (id === 'vue-router') return 'virtual:stub:router'
      if (id === '../api' || id.endsWith('/src/api')) return 'virtual:stub:api'
      if (id.endsWith('/platform/dialogs')) return 'virtual:stub:dialogs'
      if (id.endsWith('/components/ListeningPlayer.vue')) return 'virtual:stub:player'
    },
    load(id) { if (id === 'virtual:practice-timer') return entry; if (id.startsWith('virtual:stub:')) return stubs[id.slice(13)] },
    configureServer(server) { server.middlewares.use((req, res, next) => {
      if (!req.url.startsWith('/__practice-timer')) return next()
      res.setHeader('Content-Type', 'text/html; charset=utf-8')
      res.end('<!doctype html><html><meta name="viewport" content="width=device-width, initial-scale=1"><body><div id="app"></div><script type="module" src="/@id/virtual:practice-timer"></script></body></html>')
    }) },
  }, vue()], server: { host: '127.0.0.1', port: 0 } })

const setVisibility = page => value => page.evaluate(value => {
  Object.defineProperty(document, 'visibilityState', { configurable: true, get: () => value })
  Object.defineProperty(document, 'hidden', { configurable: true, get: () => value === 'hidden' })
  document.dispatchEvent(new Event('visibilitychange'))
}, value)

let browser
try {
  await server.listen()
  browser = await chromium.launch({ headless: true })
  const page = await browser.newPage({ viewport: { width: 1280, height: 800 } })
  const errors = []
  page.on('pageerror', error => errors.push(error.message))
  await page.route('**/*', route => new URL(route.request().url()).hostname === '127.0.0.1' ? route.continue() : route.abort())

  // 场景一：首次进入显示计时选择弹层；开始计时后运行、手动休息、显式继续。
  await page.goto(server.resolvedUrls.local[0] + '__practice-timer')
  await page.locator('.timer-overlay').waitFor({ timeout: 15000 })
  if (output) await page.screenshot({ path: join(output, 'timer-prompt-light.png'), fullPage: true })
  await page.getByRole('button', { name: '开始计时', exact: true }).click()
  await page.locator('.practice-timer').waitFor()
  assert.equal(await page.locator('.practice-timer').getAttribute('class'), 'practice-timer', '开始后应为运行态')
  await page.getByRole('button', { name: '休息一下', exact: true }).click()
  await page.waitForFunction(() => document.querySelector('.practice-timer')?.classList.contains('paused'))
  assert.ok(await page.locator('.timer-pause-overlay').count(), '手动休息应显示遮罩')
  if (output) await page.screenshot({ path: join(output, 'timer-paused-light.png'), fullPage: true })
  await page.locator('.timer-pause-overlay').getByRole('button', { name: '继续练习', exact: true }).click()
  await page.waitForFunction(() => !document.querySelector('.practice-timer')?.classList.contains('paused'))

  // 场景二：页面隐藏自动暂停（automatic）——不弹休息遮罩，回前台自动续计。
  await setVisibility(page)('hidden')
  await page.waitForFunction(() => document.querySelector('.practice-timer')?.classList.contains('paused'))
  assert.equal(await page.locator('.timer-pause-overlay').count(), 0, '自动暂停不应显示手动休息遮罩')
  await setVisibility(page)('visible')
  await page.waitForFunction(() => !document.querySelector('.practice-timer')?.classList.contains('paused'))

  // 场景三：旧版计时记录（v1 running）迁移——保留累计、不补算离线时间、显示迁移提示。
  await page.goto(server.resolvedUrls.local[0] + '__practice-timer?timerFixture=legacy')
  await page.locator('.timer-notice').waitFor({ timeout: 15000 })
  assert.ok(await page.getByText('已迁移旧计时记录并保留原值', { exact: false }).count(), '迁移提示缺失')
  assert.ok(await page.getByText('00:01:01', { exact: true }).count(), '迁移累计应为 1 分 1 秒')
  assert.ok(await page.locator('.practice-timer.paused').count(), '迁移后应为暂停态等待显式继续')
  const geometry = await page.evaluate(() => {
    const overflow = [...document.querySelectorAll('main *')].filter(el => {
      const r = el.getBoundingClientRect(); return r.width > 0 && (r.right > innerWidth + 1 || r.left < -1)
    }).map(el => el.tagName + '.' + el.className)
    return overflow
  })
  assert.deepEqual(geometry, [], JSON.stringify(geometry))
  await page.evaluate(() => document.documentElement.classList.toggle('dark', true))
  if (output) await page.screenshot({ path: join(output, 'timer-migrated-dark.png'), fullPage: true })

  // 场景四：v2 recovery 快照提示未正常结束，点击继续后续计。
  await page.goto(server.resolvedUrls.local[0] + '__practice-timer?timerFixture=recovery')
  await page.locator('.timer-notice').waitFor({ timeout: 15000 })
  assert.ok(await page.getByText('上次计时未正常结束', { exact: false }).count(), '恢复提示缺失')
  assert.ok(await page.getByText('00:02:05', { exact: true }).count(), '恢复累计应为 2 分 5 秒')
  await page.evaluate(() => document.documentElement.classList.remove('dark'))
  await page.locator('.practice-timer').getByRole('button', { name: '继续练习', exact: true }).click()
  await page.waitForFunction(() => !document.querySelector('.practice-timer')?.classList.contains('paused'))
  if (output) await page.screenshot({ path: join(output, 'timer-recovery-resumed-light.png'), fullPage: true })
  assert.deepEqual(errors, [], '页面错误: ' + JSON.stringify(errors))
  console.log('PASS practice timer UI: prompt, manual pause/continue, automatic visibility pause/resume, legacy migration + recovery notices, no overflow')
} finally {
  if (browser) await browser.close()
  await server.close()
}
