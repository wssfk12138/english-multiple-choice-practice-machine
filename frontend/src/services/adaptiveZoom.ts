// 自适应缩放（参考主流大屏/桌面 Web 的 zoom 方案）：以 1920 设计宽为基准，
// 窗口宽度变化时整体等比缩放，避免平板比例的版式在窄窗口被挤压。
// 两个配套补偿（缺一不可，均经实机 Chromium 验证）：
// 1. html/body 宽度 = 100vw/缩放：让版式拿到放大后的逻辑宽度再整体缩回，
//    网格、分栏按设计宽度参与布局（zoom 本身不会改变布局视口宽度）；
// 2. --app-vh/--app-vw（styles.css）：CSS zoom 会把 100vh/100vw 一并缩放，
//   视口单位除回 --app-zoom，保证侧栏/练习分栏等始终满高满宽。
const DESIGN_WIDTH = 1920
const MIN_SCALE = 0.62
const MAX_SCALE = 1

export function applyAdaptiveZoom(): number {
  const scale = Math.min(MAX_SCALE, Math.max(MIN_SCALE, window.innerWidth / DESIGN_WIDTH))
  const root = document.documentElement
  root.style.setProperty('--app-zoom', scale === 1 ? '1' : String(scale))
  root.style.zoom = scale === 1 ? '' : String(scale)
  const compensatedWidth = scale === 1 ? '' : `calc(100vw / ${scale})`
  root.style.width = compensatedWidth
  document.body.style.width = compensatedWidth
  return scale
}

export function installAdaptiveZoom(): void {
  applyAdaptiveZoom()
  window.addEventListener('resize', applyAdaptiveZoom)
}
