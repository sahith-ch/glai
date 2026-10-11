import { createRoot } from "react-dom/client";
import App from "./App.jsx";
// fonts are bundled (no network needed - works offline / in the desktop app)
import "@fontsource/ibm-plex-sans/400.css";
import "@fontsource/ibm-plex-sans/500.css";
import "@fontsource/ibm-plex-sans/600.css";
import "@fontsource/ibm-plex-sans/700.css";
import "@fontsource/ibm-plex-serif/500.css";
import "@fontsource/ibm-plex-serif/600.css";
import "@fontsource/jetbrains-mono/400.css";
import "./styles.css";

createRoot(document.getElementById("root")).render(<App />);
