import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import App from "./App";
// Locally bundled fonts (no CDN): Inter for the interface, Vazirmatn as the Persian/Arabic
// fallback, JetBrains Mono for raw code and log views only.
import "@fontsource-variable/inter/wght.css";
import "@fontsource-variable/vazirmatn/wght.css";
import "@fontsource-variable/jetbrains-mono/wght.css";
import "./styles/app.css";

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <App />
  </StrictMode>,
);
