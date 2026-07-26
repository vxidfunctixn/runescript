import { defineConfig } from "vite";

export default defineConfig({
  // Ścieżki względne w dist/ — strona działa też otwarta z pliku lub z podkatalogu.
  base: "./",
  server: {
    open: true,
    // Nasłuchuj na wszystkich interfejsach, nie tylko na localhoście — dzięki temu
    // Vite wypisze też adres w sieci lokalnej (Network: http://192.168.x.x:5173)
    // i podgląd otworzysz z telefonu czy innego komputera w tym samym wifi.
    host: true,
    // Artefakty czcionki generuje build_font.py, a nie Vite — obserwujemy je,
    // żeby po ponownym zbudowaniu fontu przeładować podgląd.
    watch: { ignored: ["**/__pycache__/**"] },
  },
  // to samo dla podglądu builda (yarn preview)
  preview: {
    host: true,
  },
  build: {
    outDir: "dist",
    emptyOutDir: true,
  },
});
