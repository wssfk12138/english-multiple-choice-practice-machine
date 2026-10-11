// 自适应缩放（参考主流大屏/桌面 Web 的 zoom 方案）：以 1920 设计宽为基准，
// 窗口宽度变化时整体等比缩放，避免平板比例的版式在窄窗口被挤压。
// 配套补偿（均由 JS 直接写入像素值，不依赖 calc 除法——部分浏览器引擎
// 不支持 calc(长度 / var 数字)，会导致页面高度塌陷出现黑屏）：
// 1. html/body 宽度 = 视口宽/缩放：让版式拿到放大后的逻辑宽度再整体缩回，
//    网格、分栏按设计宽度参与布局（zoom 本身不会改变布局视口宽度）；
// 2. --app-vh/--app-vw = 视口高宽/缩放（像素值）：CSS zoom 会把 100vh/100vw
//    一并缩放，styles.css 中的视口单位全部走这两个变量保持满高满宽。
// 浏览器不支持 CSS zoom 时整体跳过（回退为不缩放的原始布局）。
const DESIGN_WIDTH = 1920
const MIN_SCALE = 0.62
const MAX_SCALE = 1

function zoomSupported(): boolean {
  return typeof CSS !== 'undefined' && typeof CSS.supports === 'function' && CSS.supports('zoom', '0.5')
}

export function applyAdaptiveZoom(): number {
  if (!zoomSupported()) return 1
  const scale = Math.min(MAX_SCALE, Math.max(MIN_SCALE, window.innerWidth / DESIGN_WIDTH))
  const root = document.documentElement
  root.style.setProperty('--app-zoom', scale === 1 ? '1' : String(scale))
  root.style.zoom = scale === 1 ? '' : String(scale)
  // 视口单位补偿一律用 JS 预先算好的像素值，禁止在 CSS 里做除法。
  root.style.setProperty('--app-vh', `${Math.round(window.innerHeight / scale)}px`)
  root.style.setProperty('--app-vw', `${Math.round(window.innerWidth / scale)}px`)
  const compensatedWidth = scale === 1 ? '' : `calc(100vw / ${scale})`
  root.style.width = compensatedWidth
  document.body.style.width = compensatedWidth
  return scale
}

export function installAdaptiveZoom(): void {
  applyAdaptiveZoom()
  window.addEventListener('resize', applyAdaptiveZoom)
  window.addEventListener('orientationchange', applyAdaptiveZoom)
}
