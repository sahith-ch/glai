import { useEffect, useState } from "react";
import { loadProfile, saveProfile, signedInEmail } from "../auth.js";

/** Your public details: what recruiters see on GLAI Hire. First run (setup) and later edits. */
export default function Profile({ setup, onSaved }) {
  const [form, setForm] = useState(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState(null);
  const [saved, setSaved] = useState(false);

  useEffect(() => {
    loadProfile().then((row) => {
      const p = row?.profile || {};
      const email = signedInEmail();
      setForm({
        name: p.name || row?.name || "", headline: p.headline || "", location: p.location || "",
        showEmail: Boolean(p.contact) || setup, is_public: row ? row.is_public !== false : true, email,
      });
    }, (e) => setError(e.message));
  }, [setup]);

  const set = (k) => (e) => setForm({ ...form, [k]: e.target.type === "checkbox" ? e.target.checked : e.target.value });
  const submit = async (e) => {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      await saveProfile({ name: form.name.trim(), headline: form.headline.trim(), location: form.location.trim(),
        contact: form.showEmail ? form.email : "", is_public: form.is_public });
      setSaved(true);
      onSaved?.();
    } catch (err) {
      setError(err.message);
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className={setup ? "setup" : ""}>
      <header className="page-head">
        <span className="eyebrow">{setup ? "One last step" : "Profile"}</span>
        <h1>{setup ? "How should recruiters see you?" : "Your public profile"}</h1>
        <p className="lede">Your skill graph, verified tasks and rank come from your work. These details sit next to them on GLAI Hire.</p>
      </header>
      {!form && !error && <p className="muted">Loading…</p>}
      {form && (
        <form className="card form profile-form" onSubmit={submit}>
          <label className="full"><span className="label-text">Name</span>
            <input value={form.name} onChange={set("name")} required autoFocus={setup} /></label>
          <label className="full"><span className="label-text">Headline</span>
            <input value={form.headline} onChange={set("headline")} placeholder="e.g. Aspiring data analyst" maxLength={80} /></label>
          <label className="full"><span className="label-text">City</span>
            <input value={form.location} onChange={set("location")} placeholder="e.g. Hyderabad" maxLength={60} /></label>
          <label className="full toggle"><input type="checkbox" checked={form.is_public} onChange={set("is_public")} />
            <span><b>Show my profile to recruiters</b><span className="muted small">Appear in GLAI Hire search and the public leaderboard.</span></span></label>
          <label className="full toggle"><input type="checkbox" checked={form.showEmail} onChange={set("showEmail")} disabled={!form.is_public} />
            <span><b>Let recruiters email me</b><span className="muted small">Shows {form.email} on your public profile.</span></span></label>
          {error && <p className="error small full">{error}</p>}
          <div className="form-foot full">
            <button type="submit" className="button primary" disabled={busy}>{busy ? "Saving…" : setup ? "Continue" : "Save"}</button>
            {saved && !setup && <span className="muted small">Saved.</span>}
          </div>
        </form>
      )}
      {error && !form && <p className="error">{error}</p>}
    </div>
  );
}
