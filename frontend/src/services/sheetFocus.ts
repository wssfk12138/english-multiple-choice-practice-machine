/** Keep keyboard traversal inside an open sheet and return to its trigger. */
export function focusSheet(sheet: HTMLElement | null, opener: HTMLElement | null): () => void {
  if (!sheet) return () => {}
  const controls = () => Array.from(sheet.querySelectorAll<HTMLElement>(
    'button:not(:disabled),input:not(:disabled),select:not(:disabled),textarea:not(:disabled),a[href],[tabindex="0"]',
  )).filter(element => element.getClientRects().length > 0)
  const keydown = (event: KeyboardEvent) => {
    if (event.key !== 'Tab') return
    const items = controls()
    const first = items[0], last = items[items.length - 1]
    if (!first) { event.preventDefault(); return }
    if (event.shiftKey && document.activeElement === first) {
      event.preventDefault(); last?.focus()
    } else if (!event.shiftKey && document.activeElement === last) {
      event.preventDefault(); first.focus()
    }
  }
  sheet.addEventListener('keydown', keydown)
  ;(sheet.querySelector<HTMLElement>('input[type="search"]') || controls()[0])?.focus({ preventScroll: true })
  return () => {
    sheet.removeEventListener('keydown', keydown)
    if (opener?.isConnected) opener.focus({ preventScroll: true })
  }
}
