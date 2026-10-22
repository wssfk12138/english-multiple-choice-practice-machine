import { reactive } from 'vue'

export type SpeechAccent = 'us' | 'gb'
export type SpeechPhase = 'idle' | 'loading' | 'playing' | 'error'

const CHANNEL_NAME = 'english-practice-speech'
const AUTOPLAY_BLOCKED = '浏览器拦截了播放，请再点一次'
const FALLBACK_ERROR = '离线发音失败，请稍后重试'

/** Shared playback state so only one word is ever reading aloud at a time. */
export const speech = reactive({
  ownerId: '',
  requestId: '',
  accent: '' as SpeechAccent | '',
  state: 'idle' as SpeechPhase,
  error: '',
})

/** Accents advertised by the local backend; the public build only ships en_US. */
export const voiceStatus = reactive({
  loaded: false,
  loading: false,
  available: false,
  accents: [] as SpeechAccent[],
  error: '',
})

class SpeechRequestError extends Error {}

let statusRequest: Promise<void> | null = null
let controller: AbortController | null = null
let audio: HTMLAudioElement | null = null
let objectUrl = ''
let loadedClip: { text: string; accent: SpeechAccent; ownerId: string } | null = null
let serial = 0
let channel: BroadcastChannel | null = null

function isAccent(value: unknown): value is SpeechAccent {
  return value === 'us' || value === 'gb'
}

function releaseAudio() {
  if (audio) {
    audio.onended = null
    audio.onerror = null
    audio.pause()
    audio.removeAttribute('src')
    try { audio.load() } catch { /* an already-detached element may reject load(); nothing to recover. */ }
    audio = null
  }
  if (objectUrl) {
    URL.revokeObjectURL(objectUrl)
    objectUrl = ''
  }
  loadedClip = null
}

function cancelLocal() {
  serial += 1
  controller?.abort()
  controller = null
  releaseAudio()
  speech.state = 'idle'
  speech.error = ''
}

function channelPort(): BroadcastChannel | null {
  if (typeof BroadcastChannel === 'undefined') return null
  if (!channel) {
    channel = new BroadcastChannel(CHANNEL_NAME)
    channel.onmessage = event => {
      const data = event.data as { type?: string; ownerId?: string } | null
      if (!data || data.type !== 'stop') return
      // An empty owner means "another tab started speaking": silence this tab too.
      if (!data.ownerId) {
        if (speech.ownerId) cancelLocal()
        return
      }
      if (!speech.ownerId || speech.ownerId !== data.ownerId) return
      cancelLocal()
    }
  }
  return channel
}

function stopOtherTabs(ownerId: string) {
  try { channelPort()?.postMessage({ type: 'stop', ownerId }) } catch { /* cross-tab stop is best effort */ }
}

export function loadSpeechStatus(): Promise<void> {
  if (statusRequest) return statusRequest
  if (voiceStatus.loaded) return Promise.resolve()
  voiceStatus.loading = true
  voiceStatus.error = ''
  statusRequest = (async () => {
    try {
      const response = await fetch('/api/speech/status', { headers: { Accept: 'application/json' } })
      if (!response.ok) throw new SpeechRequestError('status ' + response.status)
      const data = await response.json() as { accents?: unknown; available?: unknown }
      const accents = Array.isArray(data.accents) ? data.accents.filter(isAccent) : []
      voiceStatus.accents = accents
      voiceStatus.available = accents.length > 0 && data.available !== false
      voiceStatus.loaded = true
    } catch {
      voiceStatus.available = false
      voiceStatus.error = '发音不可用'
    } finally {
      voiceStatus.loading = false
      statusRequest = null
    }
  })()
  return statusRequest
}

export async function speak(text: string, accent: SpeechAccent, ownerId: string) {
  const value = text.trim()
  if (!value || !ownerId) return
  const canReuse = Boolean(
    loadedClip
    && audio
    && loadedClip.text === value
    && loadedClip.accent === accent
    && loadedClip.ownerId === ownerId,
  )
  if (canReuse && audio) {
    const token = serial
    stopOtherTabs('')
    // A blocked autoplay keeps the clip so the next click can start it without re-synthesizing.
    speech.ownerId = ownerId
    speech.accent = accent
    speech.state = 'loading'
    speech.error = ''
    try {
      await audio.play()
      if (token === serial && speech.ownerId === ownerId) speech.state = 'playing'
    } catch {
      if (token === serial && speech.ownerId === ownerId) {
        speech.state = 'error'
        speech.error = AUTOPLAY_BLOCKED
      }
    }
    return
  }

  cancelLocal()
  stopOtherTabs('')

  serial += 1
  const token = serial
  controller = new AbortController()
  speech.ownerId = ownerId
  speech.requestId = 'speech-' + token
  speech.accent = accent
  speech.state = 'loading'
  speech.error = ''

  try {
    const response = await fetch('/api/speech/synthesize', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', Accept: 'audio/wav' },
      body: JSON.stringify({ text: value, accent, request_id: speech.requestId }),
      signal: controller.signal,
    })
    if (!response.ok) {
      let message = FALLBACK_ERROR
      try {
        const data = await response.json() as { detail?: unknown }
        if (typeof data.detail === 'string' && data.detail.trim()) message = data.detail
      } catch { /* non-JSON error bodies fall back to the generic message. */ }
      throw new SpeechRequestError(message)
    }
    const blob = await response.blob()
    if (token !== serial) return
    objectUrl = URL.createObjectURL(blob)
    audio = new Audio(objectUrl)
    audio.preload = 'auto'
    loadedClip = { text: value, accent, ownerId }
    audio.onended = () => {
      if (speech.ownerId !== ownerId) return
      speech.state = 'idle'
      speech.error = ''
      releaseAudio()
    }
    audio.onerror = () => {
      if (speech.ownerId !== ownerId) return
      speech.state = 'error'
      speech.error = '离线发音播放失败'
    }
    try {
      await audio.play()
      if (token === serial && speech.ownerId === ownerId) speech.state = 'playing'
    } catch {
      if (token === serial && speech.ownerId === ownerId) {
        speech.state = 'error'
        speech.error = AUTOPLAY_BLOCKED
      }
    }
  } catch (error) {
    if (token !== serial) return
    if (error instanceof DOMException && error.name === 'AbortError') return
    if (speech.ownerId !== ownerId) return
    speech.state = 'error'
    speech.error = error instanceof SpeechRequestError ? error.message : FALLBACK_ERROR
  } finally {
    if (token === serial) controller = null
  }
}

export async function stop(ownerId: string) {
  if (!ownerId || speech.ownerId !== ownerId) return
  const wasActive = speech.state !== 'idle'
  cancelLocal()
  if (wasActive) stopOtherTabs(ownerId)
}
