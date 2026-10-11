import assert from 'node:assert/strict'
import { mkdir } from 'node:fs/promises'
import { createRequire } from 'node:module'
import { join } from 'node:path'
import { fileURLToPath } from 'node:url'
import { createServer } from 'vite'
import vue from '@vitejs/plugin-vue'

// 视觉验收（模型执行）：内容修正选择弹窗 + 无损转移结果提示，在真实 PracticeView 中渲染。
// 运行：PLAYWRIGHT_MODULE=<playwright 路径> PRACTICE_TRANSFER_OUTPUT=<截图目录> node tests/practice-content-transfer.mjs
const require = createRequire(import.meta.url)
const { chromium } = require(process.env.PLAYWRIGHT_MODULE || 'playwright')
const root = fileURLToPath(new URL('../', import.meta.url))
const output = process.env.PRACTICE_TRANSFER_OUTPUT
if (output) await mkdir(output, { recursive: true })

const stubs = {
  router: 'const params={id:"1"};export const useRoute=()=>({get params(){return params}});export const useRouter=()=>({push:async()=>{},replace:async(p)=>{params.id=String(p).split("/").pop()}});export const onBeforeRouteLeave=()=>{};',
  api: 'export const get=(p)=>globalThis.mockApi(p);export const post=(p,b)=>globalThis.mockApi(p,b);export const put=(p,b)=>globalThis.mockApi(p,b);',
  dialogs: 'export const confirmDialog=async()=>true;',
  player: 'import {h} from "vue";export default {name:"ListeningPlayerStub",props:["tracks","seekable","timerPaused","currentQuestionNumber","answeredCount"],render:()=>h("section",{class:"listening-stub"},"listening-stub")};',
}

const fixture = function () {
  let chosen = ''
  const option = (key, content) => ({ stable_key: key, label: key, content, metadata: {}, content_blocks: [] })
  const activeSession = {
    id: 2, status: 'active', mode: 'paper', score: 2, max_score: 4, submitted_at: null,
    content_choice_required: false, snapshot_accepted_revision: 'current',
    units: [
      {
        id: 11, title: 'Reading A', year: 2024, unit_type: 'reading', sequence: 1,
        passage: 'Carried unit passage. The old selection stays visible.',
        submission: { submitted: true, score: 2, max_score: 2, submitted_at: '2026-10-11 09:00:00' },
        questions: [{
          id: 101, number: 1, stem: 'Carried question — unchanged material.', question_type: 'choice', score: 2,
          metadata: {}, stem_blocks: [],
          options: [option('A', 'Carried option A'), option('B', 'Carried option B')],
          option_order: ['A', 'B'], user_answer: 'A', is_correct: true,
        }],
      },
      {
        id: 12, title: 'Reading B', year: 2024, unit_type: 'reading', sequence: 2,
        passage: 'Reset unit passage with corrected material.',
        submission: null,
        questions: [{
          id: 102, number: 2, stem: 'Corrected question — needs a fresh answer.', question_type: 'choice', score: 2,
          metadata: {}, stem_blocks: [],
          options: [option('A', 'Corrected option A'), option('B', 'Corrected option B'), option('C', 'Corrected option C')],
          option_order: ['A', 'B', 'C'], user_answer: '',
        }],
      },
    ],
  }
  globalThis.mockApi = async (path, body) => {
    if (path === '/practice/sessions/1') return { id: 1, content_choice_required: true, status: 'active', mode: 'paper' }
    if (path === '/practice/sessions/2') return activeSession
    if (path === '/practice/sessions/1/content-choice') {
      chosen = String(body && body.choice)
      return { id: 2, carried: 38, reset: 2, carried_submissions: 1 }
    }
    if (path.startsWith('/practice/sessions/')) return { kept: false }
    throw new Error('Unexpected mock API: ' + path)
  }
  globalThis.mockState = () => ({ chosen })
}
const entry = '(' + fixture.toString() + ')();' +
  'import {createApp,h} from "vue";import View from "/src/views/PracticeView.vue";import "/src/styles.css";import "/src/study-theme.css";' +
  'createApp({render:()=>h(View)}).mount("#app");'

const server = await createServer({ configFile: false, root, optimizeDeps: { noDiscovery: true, include: ['vue', 'lucide-vue-next'] }, plugins: [
  { name: 'isolated-practice-transfer', enforce: 'pre',
    resolveId(id) {
      if (id === 'virtual:practice-transfer') return id
      if (id === 'vue-router') return 'virtual:stub:router'
      if (id === '../api' || id.endsWith('/src/api')) return 'virtual:stub:api'
      if (id.endsWith('/platform/dialogs')) return 'virtual:stub:dialogs'
      if (id.endsWith('/components/ListeningPlayer.vue')) return 'virtual:stub:player'
    },
    load(id) { if (id === 'virtual:practice-transfer') return entry; if (id.startsWith('virtual:stub:')) return stubs[id.slice(13)] },
    configureServer(server) { server.middlewares.use((req, res, next) => {
      if (req.url !== '/__practice-transfer') return next()
      res.setHeader('Content-Type', 'text/html; charset=utf-8')
      res.end('<!doctype html><html><meta name="viewport" content="width=device-width, initial-scale=1"><body><div id="app"></div><script type="module" src="/@id/virtual:practice-transfer"></script></body></html>')
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
    await page.goto(server.resolvedUrls.local[0] + '__practice-transfer')
    try {
      await page.locator('.content-choice').waitFor({ timeout: 15000 })
    } catch (error) {
      const body = await page.evaluate(() => document.body.innerText.slice(0, 600))
      throw new Error('choice UI missing; pageerrors=' + JSON.stringify(errors) + '; body=' + JSON.stringify(body))
    }
    assert.ok(await page.getByText('未受修正影响的题目会保留你的选择', { exact: false }).count(), 'restart 描述未显示')
    assert.ok(await page.getByText('继续使用旧内容、原选项顺序及原评分依据', { exact: false }).count(), 'continue 描述缺失')
    await page.evaluate(dark => document.documentElement.classList.toggle('dark', dark), dark)
    if (output) await page.screenshot({ path: join(output, `transfer-choice-${dark ? 'dark' : 'light'}.png`), fullPage: true })
    await page.getByRole('button', { name: '用修正版重新练习', exact: true }).click()
    await page.locator('.transfer-notice').waitFor({ timeout: 15000 }).catch(async () => {
      const body = await page.evaluate(() => document.body.innerText.slice(0, 600))
      throw new Error('notice missing; pageerrors=' + JSON.stringify(errors) + '; body=' + JSON.stringify(body))
    })
    assert.equal(await page.evaluate(() => globalThis.mockState().chosen), 'restart')
    assert.equal(await page.locator('.transfer-notice').innerText(),
      '已保留 38 题作答，2 题因内容修正需重做；1 篇已提交成绩原样带入。')
    assert.ok(await page.getByText('Carried question — unchanged material.', { exact: false }).count(), '练习页未渲染')
    assert.ok(await page.getByText('Carried option A', { exact: false }).count(), '携带题选项未渲染')
    const geometry = await page.evaluate(() => {
      const overflow = [...document.querySelectorAll('main *')].filter(el => {
        const r = el.getBoundingClientRect(); return r.width > 0 && (r.right > innerWidth + 1 || r.left < -1)
      }).map(el => el.tagName + '.' + el.className)
      const notice = document.querySelector('.transfer-notice')
      const rect = notice.getBoundingClientRect()
      const style = getComputedStyle(notice)
      return { overflow, notice: { left: rect.left, right: rect.right, visible: rect.height > 0, radius: style.borderRadius } }
    })
    assert.deepEqual(geometry.overflow, [], JSON.stringify({ dark, ...geometry }))
    assert.ok(geometry.notice.visible && geometry.notice.left >= 0, '提示条越界或不可见')
    if (output) await page.screenshot({ path: join(output, `transfer-notice-${dark ? 'dark' : 'light'}.png`), fullPage: true })
    await page.close()
  }
  console.log('PASS practice content transfer UI: dialog copy, carried/reset notice, practice page render, light+dark, no overflow')
} finally {
  if (browser) await browser.close()
  await server.close()
}
