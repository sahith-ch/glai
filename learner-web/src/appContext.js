import { createContext, useContext, useEffect, useRef, useState } from "react";
import { apiFetch } from "./auth.js";

// The selected roadmap and a way to reload everything after a submission changes the plan.
export const AppContext = createContext({ roadmap: null, refresh: () => {} });
export const useApp = () => useContext(AppContext);

// Poll a background job until it finishes.
export function useJob(jobId, onDone) {
  const [job, setJob] = useState(null);
  const doneRef = useRef(onDone);
  doneRef.current = onDone;
  useEffect(() => {
    if (!jobId) return undefined;
    let live = true;
    const tick = async () => {
      const r = await apiFetch(`/api/jobs/${jobId}`);
      if (!live || !r.ok) return;
      const next = await r.json();
      setJob(next);
      if (next.status === "running") setTimeout(tick, 1200);
      else if (next.status === "done") doneRef.current?.(next.result);
    };
    tick();
    return () => {
      live = false;
    };
  }, [jobId]);
  return job;
}

export async function postJSON(url, body) {
  const r = await apiFetch(url, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
  const data = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error(data.error || `Error ${r.status}`);
  return data;
}
