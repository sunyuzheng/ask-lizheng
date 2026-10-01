import { defineConfig } from 'vite';

// Every supported browser reads WOFF2, so the WOFF fallbacks in @fontsource
// CSS are dropped and never bundled.
const woff2Only = {
  name: 'woff2-only',
  enforce: 'pre',
  transform(code, id) {
    if (id.includes('@fontsource') && id.endsWith('.css')) return code.replace(/,\s*url\([^)]+\.woff\) format\('woff'\)/g, '');
  },
};

export default defineConfig({plugins: [woff2Only], server: {proxy: {'/api': 'http://127.0.0.1:8000', '/health': 'http://127.0.0.1:8000'}}});
