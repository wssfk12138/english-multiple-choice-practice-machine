import {
  projectDiagnosticEntries,
  sanitizeDiagnosticValue,
  serializeDiagnosticPayload,
} from '../src/platform/diagnostics.ts'

const samples = [
  ['Authorization: Bearer private-token', ['private-token']],
  ['api_key=sk-private-key-12345678', ['sk-private-key-12345678']],
  ['https://example.com/path?token=private#fragment', ['example.com', 'private', '#fragment']],
  ['C:\\Users\\example\\Documents\\paper.esq', ['Users', 'Documents', 'paper.esq']],
  ['server=192.168.1.12 device_id=abcdef answer: private answer', ['192.168.1.12', 'abcdef', 'private answer']],
]

for (const [raw, forbiddenValues] of samples) {
  const sanitized = sanitizeDiagnosticValue(raw)
  for (const forbidden of forbiddenValues) {
    if (sanitized.includes(forbidden)) throw new Error(`Diagnostic sanitizer leaked: ${forbidden}`)
  }
}

const projected = projectDiagnosticEntries([{
  createdAt: '2026-08-29T00:00:00.000Z', module: 'import', event: 'private free text',
  appVersion: '0.1.0 private-version', errorCategory: 'NETWORK_ERROR private-category',
  symptom: 'failed at C:\\Users\\example\\private.log from 10.0.0.2',
  legacySymptom: 'unlabeled private question and answer content',
  requestBody: 'private question and answer', stack: 'private stack', deviceId: 'private device',
}])
const allowedKeys = ['appVersion', 'createdAt', 'errorCategory', 'event', 'module', 'symptom']
if (Object.keys(projected[0]).sort().join(',') !== allowedKeys.sort().join(',')) {
  throw new Error(`Unexpected diagnostic fields: ${Object.keys(projected[0]).join(',')}`)
}
const payload = serializeDiagnosticPayload(projected)
for (const forbidden of ['requestBody', 'private question', 'private stack', 'private device', '10.0.0.2', 'Users', 'unlabeled private', 'private free text', 'private-version', 'private-category']) {
  if (payload.includes(forbidden)) throw new Error(`Diagnostic payload leaked: ${forbidden}`)
}
if (new TextEncoder().encode(payload).byteLength > 1024 * 1024) throw new Error('Diagnostic payload exceeded 1 MiB')

const updateEntry = projectDiagnosticEntries([{
  createdAt: '2026-08-29T00:00:00.000Z', module: 'update', event: 'update_post',
  appVersion: '0.1.0', errorCategory: 'NETWORK_ERROR', symptom: 'ignored source text',
}])[0]
if (!updateEntry || updateEntry.event !== 'update_post') throw new Error('Update event was not allowlisted')
