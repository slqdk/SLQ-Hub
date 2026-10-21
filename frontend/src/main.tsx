import { createRoot } from "react-dom/client";
import "@fontsource/barlow/400.css";
import "@fontsource/barlow/400-italic.css";
import "@fontsource/barlow/500.css";
import "@fontsource/barlow/600.css";
import "@fontsource/barlow-semi-condensed/500.css";
import "@fontsource/barlow-semi-condensed/600.css";
import "@fontsource/jetbrains-mono/400.css";
import "katex/dist/katex.min.css";
import "./styles.css";
import { App } from "./App";
import { AppProvider } from "./state";

createRoot(document.getElementById("root")!).render(
  <AppProvider>
    <App />
  </AppProvider>,
);

if ("serviceWorker" in navigator && (location.protocol === "https:" || location.hostname === "localhost")) {
  window.addEventListener("load", () => {
    navigator.serviceWorker?.register("/sw.js").catch(() => undefined);
  });
}
