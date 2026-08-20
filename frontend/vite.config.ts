import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'

export default defineConfig({
  plugins: [vue()],
  server: {
    port: 5173,
    proxy: {
      '/api': 'http://127.0.0.1:8765',
    },
  },
  build: {
    rollupOptions: {
      output: {
        // 代码分割：把第三方依赖拆成独立 chunk，利用浏览器缓存
        manualChunks: {
          'vue-vendor': ['vue', 'vue-router'],
          'ui-vendor': ['lucide-vue-next', '@formkit/auto-animate', 'vue-draggable-next'],
        },
      },
    },
  },
})
