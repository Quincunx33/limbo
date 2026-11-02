<script setup lang="ts">
import { ref } from "vue";

const count = ref<number>(0);
</script>

<template>
  <main class="card">
    <h1>Hello from Limbo + Vue</h1>
    <button @click="count++">Clicked {{ count }} times</button>
  </main>
</template>

<style scoped>
.card { font-family: system-ui, sans-serif; padding: 2rem; color: #243043; }
button { padding: 0.6rem 0.9rem; cursor: pointer; }
</style>
