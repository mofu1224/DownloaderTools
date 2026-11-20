import { defineConfig } from 'vite';

export default defineConfig({
  build: {
    outDir: '../src/downloader_tools/static', emptyOutDir: true,
    lib: { entry: 'src/main.ts', formats: ['es'], fileName: () => 'app.js', cssFileName: 'app' },
    sourcemap: false,
  },
});
