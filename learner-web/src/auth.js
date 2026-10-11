// Sign-in for the deployed app (Supabase Auth, email + password) and the one way every page calls the API.
//
// Deployed: VITE_SUPABASE_URL / VITE_SUPABASE_KEY turn sign-in on, VITE_API_BASE points at the backend, and each
// request carries the learner's access token - the backend reads and writes the database as that learner.
// Locally (`python main.py web`) none are set: no sign-in, same-origin API, exactly as before.
import { useEffect, useState } from "react";

const SUPABASE = (import.meta.env.VITE_SUPABASE_URL || "").replace(/\/$/, "");
const KEY = import.meta.env.VITE_SUPABASE_KEY || "";
const BASE = (import.meta.env.VITE_API_BASE || "").replace(/\/$/, "");
export const authEnabled = Boolean(SUPABASE && KEY);

const STORE = "glai.session";
let session = read();
const listeners = new Set();

function read() {
  try { return JSON.parse(localStorage.getItem(STORE)) || null; } catch { return null; }
}
function write(next) {
  session = next;
  try { next ? localStorage.setItem(STORE, JSON.stringify(next)) : localStorage.removeItem(STORE); } catch { /* private mode */ }
  listeners.forEach((fn) => fn(session));
}
const fromToken = (t) => ({
  access_token: t.access_token, refresh_token: t.refresh_token,
  expires_at: t.expires_at || Math.floor(Date.now() / 1000) + Number(t.expires_in || 3600), user: t.user,
});

/** The current session (null when signed out); re-renders on sign-in and sign-out. */
export function useSession() {
  const [s, setS] = useState(session);
  useEffect(() => {
    listeners.add(setS);
    return () => listeners.delete(setS);
  }, []);
  return s;
}

async function auth(path, body, token) {
  const r = await fetch(`${SUPABASE}/auth/v1/${path}`, {
    method: body === undefined ? "GET" : "POST",
    headers: { apikey: KEY, "Content-Type": "application/json", ...(token ? { Authorization: `Bearer ${token}` } : {}) },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  const data = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error(friendly(data));
  return data;
}

function friendly(data) {
  const msg = data.msg || data.error_description || data.message || data.error || "Something went wrong";
  if (/invalid login credentials/i.test(msg)) return "That email and password don't match.";
  if (/email not confirmed/i.test(msg)) return "Confirm your email first — check your inbox for the link.";
  if (/already registered|already been registered/i.test(msg)) return "There's already an account with that email. Sign in instead.";
  if (/email rate limit/i.test(msg)) return "We couldn't send the confirmation email right now (the email limit was reached). Try again later.";
  if (/rate limit/i.test(msg)) return "Too many attempts. Wait a minute and try again.";
  return msg;
}

export async function signIn(email, password) {
  write(fromToken(await auth("token?grant_type=password", { email, password })));
}

/** Returns "signed-in", or "confirm" when the project asks new users to confirm their email first. */
export async function signUp(email, password, name) {
  const data = await auth("signup", { email, password, data: { name } });
  if (data.access_token) {
    write(fromToken(data));
    return "signed-in";
  }
  return "confirm";
}

export async function resetPassword(email) {
  await auth("recover", { email });
}

export async function signOut() {
  const token = session?.access_token;
  write(null);
  if (token) auth("logout", {}, token).catch(() => {});
}

let refreshing = null;
async function refresh() {
  if (!session?.refresh_token) return null;
  refreshing ||= auth("token?grant_type=refresh_token", { refresh_token: session.refresh_token })
    .then((t) => { write(fromToken(t)); return session; })
    .catch(() => { write(null); return null; })
    .finally(() => { refreshing = null; });
  return refreshing;
}

async function token() {
  if (!session) return null;
  if (session.expires_at - 60 < Date.now() / 1000) await refresh();
  return session?.access_token || null;
}

// A confirmation or password-reset link lands here with the session in the URL fragment.
export async function takeSessionFromUrl() {
  if (!authEnabled || !window.location.hash.includes("access_token=")) return;
  const p = new URLSearchParams(window.location.hash.slice(1));
  window.history.replaceState({}, "", window.location.pathname + window.location.search);
  try {
    const user = await auth("user", undefined, p.get("access_token"));
    write(fromToken({ access_token: p.get("access_token"), refresh_token: p.get("refresh_token"),
      expires_in: p.get("expires_in"), user }));
  } catch { /* a stale link: they can sign in normally */ }
}

/** fetch() for the GLAI API: adds the backend's address and the learner's token; signs out if it's rejected. */
export async function apiFetch(path, opts = {}) {
  const send = async () => {
    const t = authEnabled ? await token() : null;
    return fetch(`${BASE}${path}`, { ...opts, headers: { ...(opts.headers || {}), ...(t ? { Authorization: `Bearer ${t}` } : {}) } });
  };
  let r = await send();
  if (r.status === 401 && authEnabled && session) {
    r = (await refresh()) ? await send() : r;
    if (r.status === 401) write(null);
  }
  return r;
}

/** The learner's own profile row, straight from the database (null before their first save). */
export async function loadProfile() {
  const t = await token();
  const r = await fetch(`${SUPABASE}/rest/v1/profiles?id=eq.${session.user.id}&select=name,headline,location,is_public,profile`, {
    headers: { apikey: KEY, Authorization: `Bearer ${t}` },
  });
  if (!r.ok) throw new Error(friendly(await r.json().catch(() => ({}))));
  return (await r.json())[0] || null;
}

/** Save the learner's public details on their own profile row (row-level security allows only their own).
 *  Merges into the stored profile, so stats and the skill graph the backend keeps there are untouched. */
export async function saveProfile({ name, headline, location, contact, is_public }) {
  const t = await token();
  const row = await loadProfile();
  const now = new Date().toISOString().slice(0, 19);
  const profile = { created: now, ...(row?.profile || {}), name, headline, location, contact, updated: now };
  const r = await fetch(`${SUPABASE}/rest/v1/profiles?on_conflict=id`, {
    method: "POST",
    headers: { apikey: KEY, Authorization: `Bearer ${t}`, "Content-Type": "application/json",
      Prefer: "resolution=merge-duplicates,return=minimal" },
    body: JSON.stringify({ id: session.user.id, name, headline, location, is_public, profile }),
  });
  if (!r.ok) throw new Error(friendly(await r.json().catch(() => ({}))));
}

export const signedInEmail = () => session?.user?.email || "";
