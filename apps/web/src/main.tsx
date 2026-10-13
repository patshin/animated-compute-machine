import { StrictMode } from "react";
import { createRoot } from "react-dom/client";

import { App } from "./App";
import "@agent-trace/design-tokens/tokens.css";
import "./styles.css";

const root = document.getElementById("root");

if (!root) {
  throw new Error("Missing #root mount point");
}

createRoot(root).render(
  <StrictMode>
    <App />
  </StrictMode>
);
