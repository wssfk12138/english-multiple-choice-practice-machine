import assert from 'node:assert/strict'
import { mkdir } from 'node:fs/promises'
import { createRequire } from 'node:module'
import { join } from 'node:path'
import { fileURLToPath } from 'node:url'
import { createServer } from 'vite'
import vue from '@vitejs/plugin-vue'

// 视觉验收（模型执行）：每日一学桌面宽屏双栏——目录、导航、双栏渲染、空态、续看持久化。
// 运行：PLAYWRIGHT_MODULE=<playwright 路径> DAILY_STUDY_OUTPUT=<截图目录> node tests/daily-study-ui.mjs
const require = createRequire(import.meta.url)
const { chromium } = require(process.env.PLAYWRIGHT_MODULE || 'playwright')
const root = fileURLToPath(new URL('../', import.meta.url))
const output = process.env.DAILY_STUDY_OUTPUT
if (output) await mkdir(output, { recursive: true })

const stubs = {
  router: 'const params={id:"1"};export const useRoute=()=>({get params(){return params},get query(){return globalThis.__routeQuery||{}}});export const useRouter=()=>({push:async(q)=>{globalThis.__lastPush=q}});export const onBeforeRouteLeave=()=>{};',
  api: 'export const get=(p)=>globalThis.mockApi(p);export const post=(p,b)=>globalThis.mockApi(p,b);export const put=(p,b)=>globalThis.mockApi(p,b);',
}

const fixture = function () {
  const words = [
    { id: 1, term: 'corruption', identity: 'nt:corruption', common_meaning: '腐败；堕落', contextual_meaning: 'the corruption of public trust', latest_sentence: 'Public trust erodes under corruption.', generated_example: { sentence: 'Corruption undermines institutions.' }, synonyms: ['dishonesty'], antonyms: ['integrity'], similar_forms: [], morphology: { forms: ['corrupt', 'corrupted'] }, memory_hint: '联想 corrupt', note: '', phonetic: '/kəˈrʌpʃn/', part_of_speech: 'n.', is_frequent: true },
    { id: 2, term: 'zealous', identity: 'nt:zealous', common_meaning: '热心的', contextual_meaning: '', latest_sentence: '', generated_example: {}, synonyms: [], antonyms: [], similar_forms: [], morphology: {}, memory_hint: '', note: '', phonetic: '', part_of_speech: 'adj.', is_frequent: false },
    { id: 3, term: 'abet', identity: 'nt:abet', common_meaning: '教唆', contextual_meaning: 'abet the crime', latest_sentence: 'He abetted the escape.', generated_example: {}, synonyms: [], antonyms: [], similar_forms: [], morphology: {}, memory_hint: '', note: '', phonetic: '', part_of_speech: 'v.', is_frequent: false },
  ]
  const empty = new URLSearchParams(location.search).get('run') === 'empty'
  const catalog = () => ({ scope_key: JSON.stringify(['daily-study', 1, 'all', 'all', '', 'browse-order-v1']), revision: 'v2:0', items: empty ? [] : words.map(w => ({ id: w.id, term: w.term, identity: w.identity })) })
  globalThis.mockApi = async (path) => {
    if (path.startsWith('/vocabulary/daily-study/catalog')) return catalog()
    if (path.startsWith('/vocabulary/daily-study/status')) { const { items, ...meta } = catalog(); return meta }
    const match = path.match(/^\/vocabulary\/(\d+)$/)
    if (match) {
      const word = words.find(item => item.id === Number(match[1]))
      if (!word) throw new Error('missing word ' + match[1])
      return { ...word, daily_study_identity: word.identity }
    }
    throw new Error('Unexpected mock API: ' + path)
  }
}
const entry = '(' + fixture.toString() + ')();' +
  'import {createApp,h} from "vue";import View from "/src/views/DailyStudyView.vue";import "/src/styles.css";import "/src/study-theme.css";' +
  'createApp({render:()=>h(View)}).mount("#app");'

const server = await createServer({ configFile: false, root, optimizeDeps: { noDiscovery: true, include: ['vue', 'lucide-vue-next'] }, plugins: [
  { name: 'isolated-daily-study', enforce: 'pre',
    resolveId(id) {
      if (id === 'virtual:daily-study') return id
      if (id === 'vue-router') return 'virtual:stub:router'
      if (id === '../api' || id.endsWith('/src/api')) return 'virtual:stub:api'
    },
    load(id) { if (id === 'virtual:daily-study') return entry; if (id.startsWith('virtual:stub:')) return stubs[id.slice(13)] },
    configureServer(server) { server.middlewares.use((req, res, next) => {
      if (!req.url.startsWith('/__daily-study')) return next()
      res.setHeader('Content-Type', 'text/html; charset=utf-8')
      res.end('<!doctype html><html><meta name="viewport" content="width=device-width, initial-scale=1"><body><div id="app"></div><script type="module" src="/@id/virtual:daily-study"></script></body></html>')
    }) },
  }, vue()], server: { host: '127.0.0.1', port: 0 } })

let browser
try {
  await server.listen()
  browser = await chromium.launch({ headless: true })
  for (const dark of [false, true]) {
    const page = await browser.newPage({ viewport: { width: 1280, height: 800 } })
    const errors = []
    page.on('pageerror', error => errors.push(error.message))
    await page.route('**/*', route => new URL(route.request().url()).hostname === '127.0.0.1' ? route.continue() : route.abort())
    await page.goto(server.resolvedUrls.local[0] + '__daily-study' + (dark ? '?run=dark' : ''))
    await page.locator('.daily-study-shell').waitFor({ timeout: 15000 })
    await page.locator('.study-card').waitFor()
    assert.ok(await page.getByText('corruption', { exact: true }).count(), '词头未渲染')
    assert.ok(await page.getByText('语境释义', { exact: true }).count(), '语境释义栏缺失')
    assert.ok(await page.getByText('腐败；堕落', { exact: true }).count(), '常用释义缺失')
    assert.equal(await page.locator('.study-count').innerText(), '1 / 3')
    // 导航：下一词 + 键盘左右键。
    await page.getByRole('button', { name: '下一词' }).click()
    await page.waitForFunction(() => document.querySelector('.study-count')?.innerText?.startsWith('2'))
    await page.waitForFunction(() => document.querySelector('.daily-study-shell')?.dataset.switching !== 'true')
    await page.keyboard.press('ArrowRight')
    await page.waitForFunction(() => document.querySelector('.study-count')?.innerText?.startsWith('3'))
    await page.waitForFunction(() => document.querySelector('.daily-study-shell')?.dataset.switching !== 'true')
    await page.keyboard.press('ArrowLeft')
    await page.waitForFunction(() => document.querySelector('.study-count')?.innerText?.startsWith('2'))
    // 续看持久化：导航捕获会写入 localStorage（含滚动位置）。
    await page.waitForFunction(() => { const raw = localStorage.getItem('daily-study:v1'); return raw && raw.includes('nt:zealous') })
    assert.ok(await page.locator('.word-nav button[aria-current=true]').innerText().then(text => text.includes('zealous')))
    const geometry = await page.evaluate(() => {
      const overflow = [...document.querySelectorAll('main *')].filter(el => {
        const r = el.getBoundingClientRect(); return r.width > 0 && (r.right > innerWidth + 1 || r.left < -1)
      }).map(el => el.tagName + '.' + el.className)
      return overflow
    })
    assert.deepEqual(geometry, [], JSON.stringify(geometry))
    await page.waitForFunction(() => document.querySelector('.daily-study-shell')?.dataset.switching !== 'true')
    await page.waitForTimeout(400)
    await page.evaluate(dark => document.documentElement.classList.toggle('dark', dark), dark)
    if (output) await page.screenshot({ path: join(output, `daily-study-${dark ? 'dark' : 'light'}.png`), fullPage: true })
    await page.close()
  }
  // 空态：目录为空时显示占位与返回按钮。
  const page = await browser.newPage({ viewport: { width: 1280, height: 800 } })
  await page.route('**/*', route => new URL(route.request().url()).hostname === '127.0.0.1' ? route.continue() : route.abort())
  await page.goto(server.resolvedUrls.local[0] + '__daily-study?run=empty')
  await page.getByText('当前范围没有可学习的单词').waitFor({ timeout: 15000 })
  if (output) await page.screenshot({ path: join(output, 'daily-study-empty.png'), fullPage: true })
  await page.close()
  console.log('PASS daily study UI: catalog render, nav + keyboard, two columns, resume persistence, empty state, light+dark, no overflow')
} finally {
  if (browser) await browser.close()
  await server.close()
}
