// Keep the browser's readable base size. Layouts, fluid spacing and container
// queries adapt to available space instead of shrinking a 1920px canvas.
// Reference: https://github.com/twbs/rfs and Bootstrap's RFS documentation.
export function applyAdaptiveZoom(): number {
  const root = document.documentElement
  root.style.removeProperty('zoom')
  root.style.removeProperty('width')
  document.body.style.removeProperty('width')
  root.style.setProperty('--app-zoom', '1')
  root.style.setProperty('--app-vh', window.innerHeight + 'px')
  root.style.setProperty('--app-vw', root.clientWidth + 'px')
  return 1
}

export function installAdaptiveZoom(): void {
  applyAdaptiveZoom()
  window.addEventListener('resize', applyAdaptiveZoom)
  window.addEventListener('orientationchange', applyAdaptiveZoom)
}
