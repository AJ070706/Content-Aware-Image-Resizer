import { defineConfig } from 'vite';
// Relative asset URLs let pywebview load the packaged UI from a local file.
export default defineConfig({ base: './' });
