import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import vm from 'node:vm'
import ts from 'typescript'
const values = new Map()
const context = { exports: {}, localStorage: { getItem: k => values.get(k) ?? null, setItem: (k,v) => values.set(k,v) }, window: { dispatchEvent() {} }, Event: class {}, CustomEvent: class { constructor(type, opts) { this.type = type; Object.assign(this, opts || {}) } } }
vm.runInNewContext(ts.transpileModule(readFileSync(new URL('../src/services/vocabularyDisplayConfig.ts', import.meta.url), 'utf8'), { compilerOptions: { module: ts.ModuleKind.CommonJS } }).outputText, context)
const { loadVocabDisplayConfig, saveVocabDisplayConfig, VOCAB_DISPLAY_OPTIONS } = context.exports
values.set('vocab-display-config', JSON.stringify({sentence:false,contextual:false,morphology:false,note:'invalid'}))
let config = loadVocabDisplayConfig()
assert.equal(config.source_sentence,false)
// 语境释义重新成为可配置项，保留历史 false 选择与 sentence 旧名兼容。
assert.equal(config.contextual,false)
assert.equal(config.morphology,false)
assert.equal(config.note,true)
config.model_sentence=true
saveVocabDisplayConfig(config)
assert.equal(loadVocabDisplayConfig().model_sentence,true)
assert.equal(loadVocabDisplayConfig().source_sentence,false)
values.set('vocab-display-config','null')
assert.equal(loadVocabDisplayConfig().common_meaning,true)
values.set('vocab-display-config','broken')
assert.equal(loadVocabDisplayConfig().source_sentence,true)
assert.equal(VOCAB_DISPLAY_OPTIONS.length,12)
console.log('PASS vocabulary display migration: old preferences, invalid values, persisted fields')
