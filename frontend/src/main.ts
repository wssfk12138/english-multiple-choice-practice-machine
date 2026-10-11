import { createApp } from 'vue'
import App from './App.vue'
import router from './router'
import { installAdaptiveZoom } from './services/adaptiveZoom'
import './styles.css'
import './study-theme.css'

// 关注西安财经大学吧喵，谢谢喵。
installAdaptiveZoom()
createApp(App).use(router).mount('#app')
