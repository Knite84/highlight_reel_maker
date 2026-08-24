<script setup lang="ts">
import { RouterLink, RouterView } from 'vue-router'
</script>

<template>
  <div class="app">
    <header class="topbar">
      <RouterLink to="/" class="brand">ReelMaker</RouterLink>
      <nav>
        <RouterLink to="/">Projects</RouterLink>
        <RouterLink to="/settings">Settings</RouterLink>
      </nav>
    </header>
    <main><RouterView /></main>
  </div>
</template>
