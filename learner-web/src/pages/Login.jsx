import { useState } from "react";
import { resetPassword, signIn, signUp } from "../auth.js";

const COPY = {
  in: { title: "Welcome back", button: "Sign in", switchText: "New to GLAI?", switchTo: "up", switchLabel: "Create an account" },
  up: { title: "Create your account", button: "Create account", switchText: "Already have an account?", switchTo: "in", switchLabel: "Sign in" },
  reset: { title: "Reset your password", button: "Send reset link", switchText: "Remembered it?", switchTo: "in", switchLabel: "Sign in" },
};

export default function Login() {
  const [mode, setMode] = useState("in");
  const [form, setForm] = useState({ name: "", email: "", password: "" });
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState(null);
  const [note, setNote] = useState(null);
  const set = (k) => (e) => setForm({ ...form, [k]: e.target.value });
  const c = COPY[mode];

  const submit = async (e) => {
    e.preventDefault();
    setError(null);
    setNote(null);
    setBusy(true);
    try {
      if (mode === "in") await signIn(form.email.trim(), form.password);
      else if (mode === "up") {
        if ((await signUp(form.email.trim(), form.password, form.name.trim())) === "confirm") {
          setNote(`We sent a confirmation link to ${form.email.trim()}. Open it, then sign in here.`);
          setMode("in");
        }
      } else {
        await resetPassword(form.email.trim());
        setNote("If there's an account for that email, a reset link is on its way.");
        setMode("in");
      }
    } catch (err) {
      setError(err.message);
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="auth">
      <section className="auth-pitch">
        <div className="logo">GLAI <span className="muted">Learn</span></div>
        <h1>Learn by doing real work — and prove it.</h1>
        <ul className="auth-points">
          <li><b>A roadmap built for you</b><span>Tell us the skill and your weekly hours; we plan milestones and write tasks.</span></li>
          <li><b>Real tasks, checked properly</b><span>Every task is tested and your code gets a review.</span></li>
          <li><b>Proof recruiters can see</b><span>Finished work builds your skill graph and global rank on GLAI Hire.</span></li>
        </ul>
      </section>

      <form className="card auth-card" onSubmit={submit}>
        <h2>{c.title}</h2>
        {note && <p className="auth-note">{note}</p>}
        {mode === "up" && (
          <label><span className="label-text">Your name</span>
            <input value={form.name} onChange={set("name")} autoComplete="name" required autoFocus /></label>
        )}
        <label><span className="label-text">Email</span>
          <input type="email" value={form.email} onChange={set("email")} autoComplete="email" required autoFocus={mode !== "up"} /></label>
        {mode !== "reset" && (
          <label><span className="label-text">Password</span>
            <input type="password" value={form.password} onChange={set("password")} required minLength={mode === "up" ? 8 : undefined}
              autoComplete={mode === "up" ? "new-password" : "current-password"} />
            {mode === "up" && <span className="muted small">At least 8 characters.</span>}
          </label>
        )}
        {error && <p className="error small">{error}</p>}
        <button type="submit" className="button primary auth-submit" disabled={busy}>{busy ? "One moment…" : c.button}</button>
        {mode === "in" && <button type="button" className="text-btn" onClick={() => { setMode("reset"); setError(null); }}>Forgot password?</button>}
        <p className="auth-switch muted small">{c.switchText}{" "}
          <button type="button" className="text-btn" onClick={() => { setMode(c.switchTo); setError(null); }}>{c.switchLabel}</button></p>
      </form>
    </div>
  );
}
