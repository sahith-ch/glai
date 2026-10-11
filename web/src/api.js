// Where the hiring API lives. Locally the hire server serves it on the same address ("");
// deployed, the site is static (Vercel) and VITE_API_BASE points at the backend's /hire, e.g.
// https://glai-backend.example.com/hire
const BASE = (import.meta.env.VITE_API_BASE || "").replace(/\/$/, "");

export const api = (path) => `${BASE}${path}`;
