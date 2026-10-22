import { onBeforeUnmount, ref, type Ref } from 'vue'
import { studySwipe } from '../services/dailyStudy'

// The view owns navigation and validated entries; this module owns only input.
export function useDailyStudySwipe(options: {
  enabled: () => boolean; canMove: (step: number) => boolean;
  navigate: (step: number) => Promise<void>; width: Ref<number>;
  reducedMotion: () => boolean;
}) {
  const offset = ref(0), phase = ref<'idle' | 'tracking' | 'dragging' | 'settling'>('idle')
  const live = ref<{ dx: number; dy: number } | null>(null)
  const pointers = new Set<number>()
  let down: { id: number; x: number; y: number; target: HTMLElement } | null = null
  let timer: ReturnType<typeof setTimeout> | undefined, lostTimer: ReturnType<typeof setTimeout> | undefined
  const selection = () => Boolean(String(getSelection()).trim())
  function release() {
    const start = down; down = null
    if (start?.target.hasPointerCapture?.(start.id)) start.target.releasePointerCapture(start.id)
  }
  function cancel() {
    release(); pointers.clear(); clearTimeout(timer); clearTimeout(lostTimer); offset.value = 0; phase.value = 'idle'; live.value = null
  }
  function reset() { pointers.clear(); cancel() }
  function hold() { release(); pointers.clear(); clearTimeout(timer); clearTimeout(lostTimer); phase.value = 'settling' }
  function rebound() {
    release(); ended = true; offset.value = 0; phase.value = 'settling'; clearTimeout(timer)
    timer = setTimeout(() => { phase.value = 'idle' }, options.reducedMotion() ? 0 : 220)
  }
  function pointerDown(event: PointerEvent) {
    pointers.add(event.pointerId)
    if (pointers.size > 1 || !event.isPrimary) { cancel(); return }
    if (event.button > 0 || !options.enabled() || phase.value !== 'idle' || selection()
      || (event.target as HTMLElement).closest('button,input,textarea,select,a,summary,[contenteditable]:not([contenteditable="false"])')) return
    down = { id: event.pointerId, x: event.clientX, y: event.clientY, target: event.currentTarget as HTMLElement }
    ended = false
    live.value = null
    phase.value = 'tracking'
  }
  function pointerMove(event: PointerEvent) {
    if (!down || event.pointerId !== down.id) return
    const dx = event.clientX - down.x, dy = event.clientY - down.y
    live.value = { dx, dy }
    if (!options.enabled() || selection() || (Math.abs(dy) > 12 && Math.abs(dy) > Math.abs(dx) / 1.6)) { cancel(); return }
    if (phase.value === 'tracking' && Math.abs(dx) > 8 && Math.abs(dx) > Math.abs(dy) * 1.6) {
      phase.value = 'dragging'
      // Capture only after intent is horizontal, so reading/selection can start.
      try { down.target.setPointerCapture(event.pointerId) } catch { /* synthetic events have no active pointer */ }
    }
    if (phase.value === 'dragging') {
      event.preventDefault()
      // Match studySwipe(): left is next and right is previous. Apply boundary
      // resistance to the same side that release will commit.
      const step = dx < 0 ? 1 : -1, bounded = Math.min(Math.abs(dx), options.width.value)
      offset.value = Math.sign(dx) * bounded * (options.canMove(step) ? 1 : .22)
    }
  }
  // A pointerup may bubble through both the card and the window. One gesture
  // must only commit once; a new pointerdown re-arms the guard.
  let ended = false
  async function pointerEnd(event: PointerEvent) {
    pointers.delete(event.pointerId)
    if (!down || event.pointerId !== down.id || ended) return
    const movement = live.value ?? { dx: event.clientX - down.x, dy: event.clientY - down.y }
    clearTimeout(lostTimer)
    if (event.type === 'pointercancel' || pointers.size || selection() || !options.enabled()) { rebound(); return }
    const step = phase.value === 'dragging' ? studySwipe(movement.dx, movement.dy) : 0
    if (!step || !options.canMove(step)) { rebound(); return }
    release(); phase.value = 'settling'
    await options.navigate(step)
    cancel()
  }
  function lostCapture(event: PointerEvent) {
    // Touch begins with implicit capture on a child; transferring it to the
    // card emits a child's lostcapture event while the card still owns it.
    if (down?.id !== event.pointerId || down.target.hasPointerCapture(event.pointerId)) return
    // ReleasePointerCapture runs before pointerup. Defer so a following pointerup
    // can commit the sampled movement instead of being pre-empted by a rebound.
    clearTimeout(lostTimer)
    lostTimer = setTimeout(() => { if (down?.id === event.pointerId) rebound() }, 80)
  }
  onBeforeUnmount(reset)
  return { offset, phase, pointerDown, pointerMove, pointerEnd, lostCapture, cancel, reset, hold }
}
