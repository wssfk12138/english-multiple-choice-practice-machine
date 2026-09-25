import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { fileURLToPath } from 'node:url'

// 路由清单回归：设置中枢挂载 /settings，模型页迁移到 /settings/model，公测专属入口保持可达。
const routerPath = fileURLToPath(new URL('../src/router.ts', import.meta.url))
const source = readFileSync(routerPath, 'utf8')
const paths = [...source.matchAll(/path: '([^']+)'/g)].map(match => match[1])
const expectations = ['/', '/library', '/practice/:id', '/wrong', '/wrong/:unitId/analysis', '/vocabulary', '/imports', '/assistant', '/settings', '/settings/model', '/settings/sync', '/settings/updates', '/settings/diagnostics', '/trash', '/help', '/remote-question-banks', '/updates']
for (const expected of expectations) {
  assert.ok(paths.includes(expected), `missing route: ${expected}`)
}
assert.ok(/path: '\/settings', component: SettingsHubView/.test(source), 'hub path binding')
assert.ok(/path: '\/settings\/model', component: SettingsView/.test(source), 'model path binding')
assert.ok(!/path: '\/settings', component: SettingsView/.test(source), 'model page no longer shadows hub path')
assert.ok(/path: '\/settings\/sync', component: SyncSettingsView/.test(source), 'sync subpage binding')
assert.ok(/path: '\/settings\/updates', component: UpdatesSettingsView/.test(source), 'updates subpage binding')
assert.ok(/path: '\/settings\/diagnostics', component: DiagnosticsSettingsView/.test(source), 'diagnostics subpage binding')
assert.ok(/path: '\/updates', redirect: '\/settings\/updates'/.test(source), 'legacy /updates redirects to updates subpage')
console.log('settings-routes ok:', paths.join(' '))