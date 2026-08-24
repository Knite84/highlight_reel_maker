import { createPinia } from 'pinia'
import { VueQueryPlugin } from '@tanstack/vue-query'
import { createApp } from 'vue'
import App from './App.vue'
import router from './router'
import './style.css'

createApp(App)
  .use(createPinia())
  .use(router)
  .use(VueQueryPlugin, {
    queryClientConfig: {
      defaultOptions: { queries: { retry: 1, staleTime: 5000, refetchOnWindowFocus: false } },
    },
  })
  .mount('#app')
