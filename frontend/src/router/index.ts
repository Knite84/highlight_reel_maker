import { createRouter, createWebHistory } from 'vue-router'

const router = createRouter({
  history: createWebHistory(),
  routes: [
    { path: '/', name: 'projects', component: () => import('@/views/ProjectsView.vue') },
    { path: '/projects/:id', name: 'project', component: () => import('@/views/ProjectView.vue') },
    { path: '/settings', name: 'settings', component: () => import('@/views/SettingsView.vue') },
  ],
})

export default router
