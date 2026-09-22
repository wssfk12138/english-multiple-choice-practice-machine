import assert from 'node:assert/strict'
import { mkdir } from 'node:fs/promises'
import { createRequire } from 'node:module'
import { fileURLToPath, pathToFileURL } from 'node:url'
import { createServer } from 'vite'
import vue from '@vitejs/plugin-vue'

const require = createRequire(import.meta.url)
const { chromium } = require(process.env.PLAYWRIGHT_MODULE || 'playwright')
const output = process.env.LAN_UI_OUTPUT ? pathToFileURL(process.env.LAN_UI_OUTPUT + '/') : new URL('../../.codex-local/lan-devices-ui/', import.meta.url)
await mkdir(output, { recursive: true })
const server = await createServer({
  configFile: false, root: fileURLToPath(new URL('../', import.meta.url)),
  plugins: [vue(), { name: 'isolated-device-test',
    resolveId(id) { if (id === 'virtual:device-test') return id },
    load(id) { if (id === 'virtual:device-test') return 'import {createApp} from "vue";import Component from "/src/components/LanPairedDevices.vue";import "/src/styles.css";createApp(Component,{onReplace:id=>globalThis.__replacement=id}).mount("#app")' },
    configureServer(server) {
    server.middlewares.use((req, res, next) => {
      if (req.url !== '/__devices') return next()
      res.setHeader('Content-Type', 'text/html; charset=utf-8')
      res.end('<div id="app"></div><script type="module" src="/@id/virtual:device-test"></script>')
    })
  } }], server: { host: '127.0.0.1', port: 0 },
})
let browser
try {
  await server.listen()
  browser = await chromium.launch({ headless: true })
  for (const [width, height] of [[360, 640], [640, 360], [1280, 800]]) {
    const page = await browser.newPage({ viewport: { width, height } })
    page.on('pageerror', error => console.error(error.message))
    page.on('console', message => { if (message.type() === 'error') console.error(message.text()) })
    let devices = Array.from({ length: 11 }, (_, i) => ({ device_id: 'android-' + String(i).padStart(3, '0') + '-0123456789abcdef0123456789abcdef', paired_at: '2026-01-01', last_seen_at: '2026-01-01' }))
    let failed = false
    await page.route('**/api/**', async route => {
      const url = new URL(route.request().url())
      if (!url.pathname.startsWith('/api/lan-sync/devices')) return route.abort()
      if (failed) return route.fulfill({ status: 503, json: { detail: 'unavailable' } })
      if (route.request().method() === 'DELETE') {
        devices = devices.filter(d => d.device_id !== decodeURIComponent(url.pathname.split('/').at(-1)))
        return route.fulfill({ json: { revoked: 1 } })
      }
      // 每台设备的同步权限走独立子路径：不能落回下面的分页分支，否则组件读到的是设备列表而不是权限对象。
      if (url.pathname.endsWith('/permissions')) {
        if (route.request().method() === 'PUT') return route.fulfill({ json: { saved: 1 } })
        return route.fulfill({ json: { categories: ['practice', 'models'], effective_categories: ['practice'] } })
      }
      const offset = Number(url.searchParams.get('offset'))
      return route.fulfill({ json: { items: devices.slice(offset, offset + 10), total: devices.length } })
    })
    await page.goto(server.resolvedUrls.local[0] + '__devices')
    await page.waitForFunction(() => document.querySelectorAll('.lan-devices li').length === 10)
    assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), true)
    for (const button of await page.locator('.lan-devices button').all()) {
      const box = await button.boundingBox()
      assert.ok(box.width >= 44 && box.height >= 44 && box.x >= 0 && box.x + box.width <= width)
    }
    await page.screenshot({ path: fileURLToPath(new URL(width + 'x' + height + '.png', output)), fullPage: true })
    await page.getByRole('button', { name: '下一页', exact: true }).click()
    await page.waitForFunction(() => document.querySelectorAll('.lan-devices li').length === 1)
    page.once('dialog', dialog => dialog.dismiss())
    await page.getByRole('button', { name: /^重装替换：/ }).click()
    assert.equal(await page.evaluate(() => globalThis.__replacement), undefined)
    page.once('dialog', dialog => dialog.accept())
    await page.getByRole('button', { name: /^重装替换：/ }).click()
    assert.equal(await page.evaluate(() => globalThis.__replacement), devices[10].device_id)
    assert.equal(devices.length, 11)
    page.once('dialog', dialog => dialog.accept())
    await page.getByRole('button', { name: /^解除绑定：/ }).click()
    await page.waitForFunction(() => document.querySelectorAll('.lan-devices li').length === 10)
    assert.equal(devices.length, 10)
    failed = true
    await page.getByRole('button', { name: '刷新绑定设备', exact: true }).click()
    await page.getByRole('alert').waitFor()
    failed = false
    devices = []
    await page.getByRole('button', { name: '刷新绑定设备', exact: true }).click()
    await page.waitForFunction(() => document.querySelectorAll('.lan-devices li').length === 0)
    console.log(width + 'x' + height + ': mounted pagination, replace confirm/cancel, revoke, error, empty and layout passed')
    await page.close()
  }
} finally {
  await browser?.close()
  await server.close()
}
