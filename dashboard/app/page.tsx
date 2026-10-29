"use client";
import { useCallback, useEffect, useMemo, useState, type ChangeEvent } from "react";

type Rec = {
  run_id: string; run_at: string; job_id: string; title: string;
  country: string; budget: number | null; budget_kind: string;
  budget_unknown: boolean; proposals_tier: string; age_hours: number | null;
  rating: number | string | null; verification_status: string;
  keywords: string[]; url: string; verdict: string; reason: string;
  hire_rate: number | null; jobs_posted: number | null; unhired: number | null;
  invites: number | null; hired: number | null; offered: number | null;
  preferred: string[]; spend: string | null; drafts: string[];
};

type RunInfo = { run_id: string; run_at: string; total: number };
type Snap = {
  generated_at: string; history_exists: boolean; live_count: number;
  runs: RunInfo[]; selected_run: string | null; jobs: Rec[];
};

const VERDICTS = [
  { key: "all", label: "Semua" },
  { key: "drafted", label: "Draft" },
  { key: "rejected_stage1", label: "Tolak tahap 1" },
  { key: "rejected_stage2", label: "Tolak tahap 2" },
  { key: "skipped_centrality", label: "Skip" },
  { key: "already_seen", label: "Sudah diproses" },
  { key: "draft_failed", label: "Draft gagal" },
  { key: "unchecked", label: "Belum dicek" },
  { key: "passed_search_only", label: "Lolos (tes)" },
];

function badgeCls(v: string) {
  switch (v) {
    case "drafted": return "bg-emerald-500/15 text-emerald-300 ring-emerald-500/30";
    case "rejected_stage1": return "bg-rose-500/15 text-rose-300 ring-rose-500/30";
    case "rejected_stage2": return "bg-orange-500/15 text-orange-300 ring-orange-500/30";
    case "skipped_centrality": return "bg-amber-500/15 text-amber-300 ring-amber-500/30";
    case "draft_failed": return "bg-red-500/15 text-red-300 ring-red-500/30";
    case "unchecked": return "bg-sky-500/15 text-sky-300 ring-sky-500/30";
    case "passed_search_only": return "bg-violet-500/15 text-violet-300 ring-violet-500/30";
    default: return "bg-zinc-500/15 text-zinc-300 ring-zinc-500/30";
  }
}

function verdictLabel(v: string) {
  return VERDICTS.find((x) => x.key === v)?.label ?? v;
}

function fmtTime(iso: string) {
  try {
    return new Date(iso).toLocaleString("id-ID", { timeZone: "Asia/Jakarta", day: "2-digit", month: "short", hour: "2-digit", minute: "2-digit" });
  } catch { return iso; }
}

function fmtBudget(j: Rec) {
  if (j.budget == null) return "tidak dinyatakan";
  return `$${Number(j.budget).toLocaleString("en-US")}${j.budget_kind ? " " + j.budget_kind : ""}`;
}

export default function Page() {
  const [snap, setSnap] = useState<Snap | null>(null);
  const [run, setRun] = useState<string | null>(null);
  const [verdict, setVerdict] = useState("all");
  const [q, setQ] = useState("");
  const [sel, setSel] = useState<Rec | null>(null);
  const [draft, setDraft] = useState<{ name: string; content: string } | null>(null);
  const [draftLoading, setDraftLoading] = useState(false);
  const [err, setErr] = useState("");

  const load = useCallback(async () => {
    try {
      const u = run ? `/api/snapshot?run=${run}` : "/api/snapshot";
      const r = await fetch(u, { cache: "no-store" });
      const j = (await r.json()) as Snap;
      setSnap(j);
      setErr("");
    } catch {
      setErr("Gagal mengambil data. Pastikan npm run dev jalan.");
    }
  }, [run]);

  useEffect(() => {
    load();
    const t = setInterval(load, 5000);
    return () => clearInterval(t);
  }, [load]);
  const counts = useMemo(() => {
    const c: Record<string, number> = {};
    for (const j of snap?.jobs ?? []) c[j.verdict] = (c[j.verdict] ?? 0) + 1;
    return c;
  }, [snap]);

  const jobs = useMemo(() => {
    const list = snap?.jobs ?? [];
    const qq = q.trim().toLowerCase();
    return list.filter(
      (j) =>
        (verdict === "all" || j.verdict === verdict) &&
        (!qq || `${j.title} ${j.country} ${(j.keywords || []).join(" ")}`.toLowerCase().includes(qq))
    );
  }, [snap, verdict, q]);

  async function openDraft(file: string) {
    const name = file.split("/").pop() ?? file;
    setDraftLoading(true);
    try {
      const r = await fetch(`/api/draft?file=${encodeURIComponent(name)}`);
      const j = await r.json();
      setDraft(r.ok ? { name: j.name, content: j.content } : { name, content: "Gagal memuat: " + (j.error ?? "") });
    } catch {
      setDraft({ name, content: "Gagal memuat draft." });
    }
    setDraftLoading(false);
  }

  function pickRun(e: ChangeEvent<HTMLSelectElement>) {
    setRun(e.target.value || null);
    setSel(null);
    setDraft(null);
  }

  const cards = [
    { label: "Dicari", value: snap?.jobs.length ?? 0 },
    { label: "Draft", value: counts["drafted"] ?? 0 },
    { label: "Skip", value: counts["skipped_centrality"] ?? 0 },
    { label: "Tolak tahap 1", value: counts["rejected_stage1"] ?? 0 },
    { label: "Tolak tahap 2", value: counts["rejected_stage2"] ?? 0 },
  ];

  return (
    <div className="min-h-screen bg-zinc-950 text-zinc-100">
      <header className="sticky top-0 z-20 border-b border-zinc-800 bg-zinc-950/90 backdrop-blur">
        <div className="mx-auto flex max-w-7xl flex-wrap items-center gap-3 px-4 py-4">
          <h1 className="text-lg font-bold">Upwork Job Watcher</h1>
          <span className="flex items-center gap-2 text-xs text-zinc-400">
            <span className="h-2 w-2 animate-pulse rounded-full bg-emerald-400" />
            Live, update tiap 5 detik
          </span>
          <span className="ml-auto text-xs text-zinc-400">
            {snap ? `Data: ${fmtTime(snap.generated_at)} WIB` : "Memuat..."}
          </span>
          <button onClick={load} className="rounded-lg bg-zinc-800 px-3 py-1.5 text-xs font-medium hover:bg-zinc-700">
            Refresh
          </button>
        </div>
      </header>

      <main className="mx-auto max-w-7xl px-4 py-6">
        {err && <p className="mb-4 rounded-lg bg-rose-500/10 px-4 py-3 text-sm text-rose-300">{err}</p>}
        {snap && !snap.history_exists && (
          <div className="mb-4 rounded-lg bg-amber-500/10 px-4 py-3 text-sm text-amber-200">
            Belum ada riwayat. Jalanin patch pencatat riwayat dan satu jalan scanner dulu, atau cek `jobs_live.json`
            (job live terakhir: {snap.live_count}). Dashboard ini cuma baca file lokal, tanpa mengubah scanner.
          </div>
        )}

        <div className="mb-5 grid grid-cols-2 gap-3 md:grid-cols-5">
          {cards.map((c) => (
            <div key={c.label} className="rounded-xl border border-zinc-800 bg-zinc-900 p-4">
              <p className="text-xs text-zinc-400">{c.label}</p>
              <p className="mt-1 text-2xl font-bold">{c.value}</p>
            </div>
          ))}
        </div>

        <div className="mb-4 flex flex-wrap items-center gap-2">
          <select value={run ?? ""} onChange={pickRun} className="rounded-lg border border-zinc-700 bg-zinc-900 px-3 py-2 text-sm">
            <option value="">Jalan terakhir</option>
            {(snap?.runs ?? []).map((r) => (
              <option key={r.run_id} value={r.run_id}>
                {fmtTime(r.run_at)} WIB — {r.total} job
              </option>
            ))}
          </select>
          <input
            value={q}
            onChange={(e: ChangeEvent<HTMLInputElement>) => setQ(e.target.value)}
            placeholder="Cari judul, negara, keyword..."
            className="min-w-56 flex-1 rounded-lg border border-zinc-700 bg-zinc-900 px-3 py-2 text-sm outline-none placeholder:text-zinc-500"
          />
        </div>

        <div className="mb-4 flex flex-wrap gap-2">
          {VERDICTS.map((v) => {
            const n = v.key === "all" ? snap?.jobs.length ?? 0 : counts[v.key] ?? 0;
            const active = verdict === v.key;
            return (
              <button
                key={v.key}
                onClick={() => setVerdict(v.key)}
                className={`rounded-full px-3 py-1.5 text-xs font-medium ring-1 ${active ? "bg-zinc-100 text-zinc-900 ring-zinc-100" : "bg-zinc-900 text-zinc-300 ring-zinc-700 hover:bg-zinc-800"}`}
              >
                {v.label} ({n})
              </button>
            );
          })}
        </div>
        <div className="overflow-x-auto rounded-xl border border-zinc-800">
          <table className="w-full min-w-[900px] text-left text-sm">
            <thead>
              <tr className="bg-zinc-900 text-xs uppercase tracking-wide text-zinc-400">
                <th className="px-4 py-3">Job</th>
                <th className="px-4 py-3">Negara</th>
                <th className="px-4 py-3">Budget</th>
                <th className="px-4 py-3">Tier</th>
                <th className="px-4 py-3">Umur</th>
                <th className="px-4 py-3">Rating</th>
                <th className="px-4 py-3">Vonis</th>
                <th className="px-4 py-3">Alasan</th>
              </tr>
            </thead>
            <tbody>
              {jobs.map((j) => (
                <tr
                  key={j.job_id + j.run_id}
                  onClick={() => { setSel(j); setDraft(null); }}
                  className={`cursor-pointer border-t border-zinc-800 hover:bg-zinc-900/60 ${sel?.job_id === j.job_id ? "bg-zinc-900" : ""}`}
                >
                  <td className="max-w-72 px-4 py-3">
                    <p className="truncate font-medium">{j.title}</p>
                    <p className="truncate text-xs text-zinc-400">{(j.keywords || []).join(", ")}</p>
                  </td>
                  <td className="px-4 py-3">{j.country || "-"}</td>
                  <td className="px-4 py-3">{fmtBudget(j)}</td>
                  <td className="px-4 py-3">{j.proposals_tier || "-"}</td>
                  <td className="px-4 py-3">{j.age_hours == null ? "-" : `${j.age_hours.toFixed(1)} jam`}</td>
                  <td className="px-4 py-3">{j.rating == null ? "-" : String(j.rating)}</td>
                  <td className="px-4 py-3">
                    <span className={`inline-block rounded-full px-2.5 py-1 text-xs font-medium ring-1 ${badgeCls(j.verdict)}`}>
                      {verdictLabel(j.verdict)}
                    </span>
                  </td>
                  <td className="max-w-64 truncate px-4 py-3 text-zinc-400" title={j.reason}>{j.reason || "-"}</td>
                </tr>
              ))}
              {jobs.length === 0 && (
                <tr><td colSpan={8} className="px-4 py-8 text-center text-zinc-400">Belum ada job buat filter ini.</td></tr>
              )}
            </tbody>
          </table>
        </div>
        <p className="mt-3 text-xs text-zinc-500">Data lokal dari laptop ini. Draft tidak pernah terkirim otomatis; semua apply tetap manual di Upwork.</p>
      </main>

      {sel && (
        <>
          <div className="fixed inset-0 z-30 bg-black/60" onClick={() => setSel(null)} />
          <aside className="fixed inset-y-0 right-0 z-40 w-full max-w-xl overflow-y-auto border-l border-zinc-800 bg-zinc-900 p-5">
            <div className="flex items-start justify-between gap-3">
              <h2 className="text-base font-bold">{sel.title}</h2>
              <button onClick={() => setSel(null)} className="rounded-lg bg-zinc-800 px-3 py-1.5 text-xs hover:bg-zinc-700">Tutup</button>
            </div>
            {sel.url && (
              <a href={sel.url} target="_blank" rel="noreferrer" className="mt-1 inline-block text-sm text-sky-400 hover:underline">
                Buka di Upwork
              </a>
            )}
            <div className="mt-4 grid grid-cols-2 gap-3 text-sm">
              <div><p className="text-xs text-zinc-400">Negara</p><p>{sel.country || "-"}</p></div>
              <div><p className="text-xs text-zinc-400">Budget</p><p>{fmtBudget(sel)}</p></div>
              <div><p className="text-xs text-zinc-400">Tier proposal</p><p>{sel.proposals_tier || "-"}</p></div>
              <div><p className="text-xs text-zinc-400">Umur</p><p>{sel.age_hours == null ? "-" : `${sel.age_hours.toFixed(1)} jam`}</p></div>
              <div><p className="text-xs text-zinc-400">Rating</p><p>{sel.rating == null ? "-" : String(sel.rating)}</p></div>
              <div><p className="text-xs text-zinc-400">Verifikasi</p><p>{sel.verification_status || "-"}</p></div>
              <div><p className="text-xs text-zinc-400">Hire rate</p><p>{sel.hire_rate == null ? "-" : `${sel.hire_rate}%`}</p></div>
              <div><p className="text-xs text-zinc-400">Posting / belum hire</p><p>{sel.jobs_posted ?? "-"} / {sel.unhired == null ? "-" : sel.unhired.toFixed(1)}</p></div>
              <div><p className="text-xs text-zinc-400">Invite / hire / offer</p><p>{sel.invites ?? "-"} / {sel.hired ?? "-"} / {sel.offered ?? "-"}</p></div>
              <div><p className="text-xs text-zinc-400">Spend</p><p>{sel.spend ?? "-"}</p></div>
              <div className="col-span-2"><p className="text-xs text-zinc-400">Preferred location</p><p>{(sel.preferred || []).join(", ") || "-"}</p></div>
              <div className="col-span-2"><p className="text-xs text-zinc-400">Vonis</p><p>{verdictLabel(sel.verdict)}</p></div>
              <div className="col-span-2"><p className="text-xs text-zinc-400">Alasan</p><p>{sel.reason || "-"}</p></div>
              <div className="col-span-2"><p className="text-xs text-zinc-400">Keyword cocok</p><p>{(sel.keywords || []).join(", ") || "-"}</p></div>
            </div>

            <div className="mt-5">
              <p className="mb-2 text-sm font-semibold">Draft</p>
              {(sel.drafts || []).length === 0 && <p className="text-sm text-zinc-400">Tidak ada file draft buat job ini.</p>}
              <div className="flex flex-wrap gap-2">
                {(sel.drafts || []).map((f) => {
                  const base = f.split("/").pop() ?? f;
                  const label = base.includes(".gemini.") ? "Gemini" : base.includes(".groq.") ? "Groq" : base;
                  return (
                    <button key={f} onClick={() => openDraft(f)} className="rounded-lg bg-zinc-800 px-3 py-1.5 text-xs font-medium hover:bg-zinc-700">
                      Lihat draft {label}
                    </button>
                  );
                })}
              </div>
              {draftLoading && <p className="mt-3 text-sm text-zinc-400">Memuat draft...</p>}
              {draft && (
                <div className="mt-3 rounded-lg border border-zinc-700 bg-zinc-950 p-4">
                  <p className="mb-2 text-xs text-zinc-400">{draft.name}</p>
                  <pre className="whitespace-pre-wrap text-sm leading-relaxed">{draft.content}</pre>
                </div>
              )}
            </div>
            <p className="mt-5 text-xs text-zinc-500">Periksa kejujuran draft sebelum dipakai. Jangan submit klaim pengalaman, bahasa, atau angka yang tidak bisa dibuktikan.</p>
          </aside>
        </>
      )}
    </div>
  );
}
