// Builds the Base44 drag-in bundle: index.html + raas.js + raas.css, every font and image inlined so
// nothing depends on how Base44 stores extra files. API host comes from VITE_RAAS_API (default prod).
import react from "@vitejs/plugin-react";
import { fileURLToPath } from "node:url";
import { defineConfig } from "vite";

process.env.VITE_RAAS_API ??= "https://raas-api.sixthsenselabs.xyz";
const here = (p: string) => fileURLToPath(new URL(p, import.meta.url));

export default defineConfig({
  root: here("."),
  base: "./",
  publicDir: false,
  plugins: [
    react(),
    {
      // raas.css points at the site's /fonts/ public dir; resolve those relatively so Vite inlines them.
      name: "inline-raas-fonts",
      enforce: "pre",
      transform(code, id) {
        if (id.replace(/\\/g, "/").endsWith("components/raas/raas.css")) {
          return code.replaceAll('url("/fonts/', 'url("../../../public/fonts/');
        }
      },
    },
  ],
  resolve: { alias: { "@": here("../src") } },
  build: {
    outDir: here(process.env.BASE44_OUT ?? "../dist-base44"),
    emptyOutDir: true,
    assetsInlineLimit: 2_000_000,
    cssCodeSplit: false,
    modulePreload: false,
    rollupOptions: {
      output: {
        entryFileNames: "raas.js",
        codeSplitting: false,
        assetFileNames: (a) => ((a.names?.[0] ?? "").endsWith(".css") ? "raas.css" : "[name][extname]"),
      },
    },
  },
});
