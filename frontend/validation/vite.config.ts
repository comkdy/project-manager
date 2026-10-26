import {defineConfig} from 'vite';
import react from '@vitejs/plugin-react';
export default defineConfig({plugins:[react()],build:{outDir:'../.local/render-validation-dist',emptyOutDir:false,chunkSizeWarningLimit:1800,rollupOptions:{input:'validation/dashboard-render.html'}}});
