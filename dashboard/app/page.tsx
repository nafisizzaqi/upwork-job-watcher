"use client";
import { useCallback, useEffect, useMemo, useRef, useState, type ChangeEvent } from "react";

type Rec = {
  run_id: string; run_at: string; job_id: string; title: string;
  country: string; budget: number | null; budget_kind: string;
  budget_unknown: boolean; proposals_tier: string; age_hours: number | null; posted_at: string | null;
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
  passed: Rec[];
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
    case "drafted": return "bg-emerald-400/10 text-emerald-200 ring-white/[0.08]";
    case "rejected_stage1": return "bg-rose-400/10 text-rose-200 ring-white/[0.08]";
    case "rejected_stage2": return "bg-orange-400/10 text-orange-200 ring-white/[0.08]";
    case "skipped_centrality": return "bg-amber-400/10 text-amber-200 ring-white/[0.08]";
    case "draft_failed": return "bg-red-400/10 text-red-200 ring-white/[0.08]";
    case "unchecked": return "bg-sky-400/10 text-sky-200 ring-white/[0.08]";
    case "passed_search_only": return "bg-violet-400/10 text-violet-200 ring-white/[0.08]";
    default: return "bg-white/[0.04] text-zinc-300 ring-white/[0.08]";
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
  const [view, setView] = useState<"passed" | "runs">("runs");
  const [passedFilter, setPassedFilter] = useState("all");
  const [fetching, setFetching] = useState(false);
  const [lastChecked, setLastChecked] = useState<string | null>(null);
  const [draftStatus, setDraftStatus] = useState<Record<string, string>>({});
  const fetchSeq = useRef(0);

  const load = useCallback(async () => {
    const seq = ++fetchSeq.current;
    setFetching(true);
    try {
      const u = run ? `/api/snapshot?run=${run}` : "/api/snapshot";
      const r = await fetch(u, { cache: "no-store" });
      const j = (await r.json()) as Snap;
      setSnap(j);
      try {
        const rs = await fetch("/api/draft-status", { cache: "no-store" });
        const js = await rs.json();
        setDraftStatus(js.statuses ?? {});
      } catch {
        setDraftStatus({});
      }
      setErr("");
      setLastChecked(new Date().toLocaleTimeString("id-ID", { hour: "2-digit", minute: "2-digit", second: "2-digit", hour12: false }));
    } catch {
      setErr("Gagal mengambil data. Pastikan npm run dev jalan.");
    } finally {
      setTimeout(() => {
        if (fetchSeq.current === seq) setFetching(false);
      }, 600);
    }
  }, [run]);

  useEffect(() => {
    load();
  }, [load]);



  const latestRun = snap?.runs[0] ?? null;
  const lastRunAgeMin = latestRun
    ? Math.max(0, Math.floor((Date.now() - Date.parse(latestRun.run_at)) / 60000))
    : null;
  const lastRunHint =
    lastRunAgeMin === null
      ? "Belum ada jalan tercatat."
      : lastRunAgeMin < 60
        ? `Baru ${lastRunAgeMin} mnt lalu — belum 1 jam, tunggu dulu.`
        : lastRunAgeMin < 120
          ? `Sudah ${Math.floor(lastRunAgeMin / 60)} jam ${lastRunAgeMin % 60} mnt lalu — jeda 1-2 jam.`
          : `Sudah ${Math.floor(lastRunAgeMin / 60)} jam lalu — aman dijalankan.`;

  const counts = useMemo(() => {
    const c: Record<string, number> = {};
    for (const j of snap?.jobs ?? []) c[j.verdict] = (c[j.verdict] ?? 0) + 1;
    return c;
  }, [snap]);

  const passedCounts = useMemo(() => {
    const list = snap?.passed ?? [];
    return {
      total: list.length,
      drafted: list.filter((j) => j.verdict === "drafted").length,
      skipped: list.filter((j) => j.verdict === "skipped_centrality").length,
    };
  }, [snap]);

  const passedJobs = useMemo(() => {
    const qq = q.trim().toLowerCase();
    return (snap?.passed ?? []).filter(
      (j) =>
        (passedFilter === "all" || j.verdict === passedFilter) &&
        (!qq || `${j.title} ${j.country} ${(j.keywords || []).join(" ")}`.toLowerCase().includes(qq))
    );
  }, [snap, passedFilter, q]);

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

  async function setDraftMark(file: string, status: string) {
    const name = file.split("/").pop() ?? file;
    const next = status === (draftStatus[name] ?? "") ? "" : status;
    setDraftStatus((prev) => {
      const copy = { ...prev };
      if (next) copy[name] = next;
      else delete copy[name];
      return copy;
    });
    try {
      await fetch("/api/draft-status", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ file: name, status: next }),
      });
    } catch {
      // status tetap di state lokal; tersimpan saat klik berikutnya
    }
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
    <div className="min-h-screen bg-[#0a0a0b] text-zinc-100">
      <header className="sticky top-0 z-20 border-b border-white/[0.07] bg-[#0a0a0b]/80 backdrop-blur">
        <div className="mx-auto flex max-w-6xl flex-wrap items-center gap-4 px-4 py-4">
          <h1 className="text-[15px] font-semibold tracking-tight">Upwork Job Watcher</h1>
          <span className="flex items-center gap-2 text-xs text-zinc-400">
            Manual, update saat Refresh
          </span>
          <span className="ml-auto text-xs text-zinc-400">
            {snap ? `Data: ${fmtTime(snap.generated_at)} WIB` : "Memuat..."}
          </span>
          <span className="flex items-center gap-1.5 text-xs text-zinc-400" aria-live="polite">
            {fetching ? (
              <>
                <span className="h-3 w-3 animate-spin rounded-full border border-zinc-500 border-t-transparent" />
                Mengambil data…
              </>
            ) : (
              <>Cek terakhir {lastChecked ?? "—"}</>
            )}
          </span>
          <button onClick={load} className="inline-flex items-center gap-1.5 rounded-lg bg-white/[0.05] px-3 py-1.5 text-xs font-medium hover:bg-white/[0.09]">
            <span aria-hidden className={fetching || !snap ? "inline-block animate-spin" : "inline-block"}>↻</span>
            Refresh
          </button>
          <span className={lastRunAgeMin !== null && lastRunAgeMin < 60 ? "text-xs text-amber-300" : "text-xs text-zinc-400"}>
            Terakhir dijalankan: {latestRun ? `${fmtTime(latestRun.run_at)} WIB` : "—"} · {lastRunHint}
          </span>
        </div>
      </header>

      <main className="mx-auto max-w-6xl px-4 py-8 sm:px-6">
        {err && <p className="mb-4 rounded-lg bg-rose-500/10 px-4 py-3 text-sm text-rose-300">{err}</p>}
        {snap && !snap.history_exists && (
          <div className="mb-4 rounded-lg bg-amber-500/10 px-4 py-3 text-sm text-amber-200">
            Belum ada riwayat. Jalanin patch pencatat riwayat dan satu jalan scanner dulu, atau cek `jobs_live.json`
            (job live terakhir: {snap.live_count}). Dashboard ini cuma baca file lokal, tanpa mengubah scanner.
          </div>
        )}

        <div className="mb-4 flex gap-2">
          <button
            onClick={() => setView("passed")}
            className={`rounded-md px-3 py-1.5 text-sm font-medium transition-colors ${view === "passed" ? "bg-white text-zinc-950" : "bg-transparent text-zinc-400 hover:bg-white/[0.05] hover:text-zinc-100"}`}
          >
            Lolos ({passedCounts.total})
          </button>
          <button
            onClick={() => setView("runs")}
            className={`rounded-md px-3 py-1.5 text-sm font-medium transition-colors ${view === "runs" ? "bg-white text-zinc-950" : "bg-transparent text-zinc-400 hover:bg-white/[0.05] hover:text-zinc-100"}`}
          >
            Per jalan
          </button>
        </div>

        {view === "passed" && (
          <div>
            <div className="mb-5 grid animate-fade-up grid-cols-2 gap-4 md:grid-cols-3">
              <div className="rounded-lg border border-white/[0.07] bg-[#121214] p-5">
                <p className="text-xs text-zinc-400">Total lolos</p>
                <p className="mt-1 text-[28px] leading-8 font-semibold tracking-tight">{passedCounts.total}</p>
              </div>
              <div className="rounded-lg border border-white/[0.07] bg-[#121214] p-5">
                <p className="text-xs text-zinc-400">Draft</p>
                <p className="mt-1 text-[28px] leading-8 font-semibold tracking-tight">{passedCounts.drafted}</p>
              </div>
              <div className="rounded-lg border border-white/[0.07] bg-[#121214] p-5">
                <p className="text-xs text-zinc-400">Skip</p>
                <p className="mt-1 text-[28px] leading-8 font-semibold tracking-tight">{passedCounts.skipped}</p>
              </div>
            </div>

            <div className="mb-4 flex flex-wrap items-center gap-2">
              <input
                value={q}
                onChange={(e: ChangeEvent<HTMLInputElement>) => setQ(e.target.value)}
                placeholder="Cari judul, negara, keyword..."
                className="min-w-56 flex-1 rounded-lg border border-white/[0.14] bg-[#121214] px-3 py-2 text-sm outline-none placeholder:text-zinc-500"
              />
              {(["all", "drafted", "skipped_centrality"] as const).map((k) => (
                <button
                  key={k}
                  onClick={() => setPassedFilter(k)}
                  className={`rounded-full px-3 py-1.5 text-xs font-medium ring-1 ${passedFilter === k ? "bg-zinc-100 text-zinc-900 ring-zinc-100" : "bg-[#121214] text-zinc-300 ring-zinc-700 hover:bg-white/[0.05]"}`}
                >
                  {k === "all" ? "Semua" : verdictLabel(k)}
                </button>
              ))}
            </div>

            <div className="flex flex-col gap-5">
              {passedJobs.map((j, i) => (
                <article key={j.job_id} style={{ animationDelay: `${Math.min(i, 8) * 45}ms` }} className="rounded-lg border border-white/[0.07] bg-[#121214] p-5 animate-fade-up transition-all duration-200 hover:-translate-y-0.5 hover:border-white/[0.16]">
                  <div className="flex flex-wrap items-start justify-between gap-2">
                    <h2 className="max-w-2xl text-[15px] font-semibold leading-snug tracking-tight">{j.title}</h2>
                    <span className={`inline-block shrink-0 rounded-full px-2.5 py-1 text-xs font-medium ring-1 ${badgeCls(j.verdict)}`}>
                      {verdictLabel(j.verdict)}
                    </span>
                  </div>
                  <p className="mt-1 text-xs text-zinc-400">
                    {j.country || "-"} · {fmtBudget(j)} · Rating {j.rating == null ? "-" : String(j.rating)} · Proposal {j.proposals_tier || "-"} · Umur {j.age_hours == null ? "-" : `${j.age_hours.toFixed(1)} jam`} · Dipost {j.posted_at ? fmtTime(j.posted_at) + " WIB" : "-"} · Lolos {fmtTime(j.run_at)} WIB
                  </p>
                  {(j.keywords || []).length > 0 && (
                    <p className="mt-1 text-xs text-zinc-400">Keyword: {(j.keywords || []).join(", ")}</p>
                  )}
                  {j.reason && <p className="mt-1 text-xs text-amber-300">{j.reason}</p>}
                  <div className="mt-3 flex flex-wrap gap-2">
                    {j.url && (
                      <a href={j.url} target="_blank" rel="noreferrer" className="rounded-lg bg-white/[0.05] px-3 py-1.5 text-xs font-medium hover:bg-white/[0.09]">
                        Buka di Upwork
                      </a>
                    )}
                    <button onClick={() => { setSel(j); setDraft(null); }} className="rounded-lg bg-white/[0.05] px-3 py-1.5 text-xs font-medium hover:bg-white/[0.09]">
                      Lihat detail
                    </button>
                    {(j.drafts || []).map((f) => {
                      const base = f.split("/").pop() ?? f;
                      const label = base.includes(".gemini.") ? "Gemini" : base.includes(".groq.") ? "Groq" : base;
                      const mark = draftStatus[base];
                      return (
                        <button key={f} onClick={() => { setSel(j); openDraft(f); }} className="rounded-md bg-white px-3 py-1.5 text-xs font-medium text-zinc-950 hover:bg-zinc-200">
                          Lihat draft {label}{mark ? ` ✓ ${mark}` : ""}
                        </button>
                      );
                    })}
                  </div>
                </article>
              ))}
              {passedJobs.length === 0 && (
                <p className="rounded-lg border border-white/[0.07] bg-[#121214] px-4 py-8 text-center text-sm text-zinc-400">
                  Belum ada job lolos. Arsip ini terisi begitu ada job yang lolos filter dan dibuatkan draft atau di-skip AI.
                </p>
              )}
            </div>
          </div>
        )}

        {view === "runs" && (
          <>
        <div className="mb-5 grid grid-cols-2 gap-4 md:grid-cols-5">
          {cards.map((c, i) => (
            <div key={c.label} style={{ animationDelay: `${Math.min(i, 8) * 45}ms` }} className="rounded-lg border border-white/[0.07] bg-[#121214] p-5 animate-fade-up">
              <p className="text-xs text-zinc-400">{c.label}</p>
              <p className="mt-1 text-[28px] leading-8 font-semibold tracking-tight">{c.value}</p>
            </div>
          ))}
        </div>

        <div className="mb-4 flex flex-wrap items-center gap-2">
          <select value={run ?? ""} onChange={pickRun} className="rounded-lg border border-white/[0.14] bg-[#121214] px-3 py-2 text-sm">
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
            className="min-w-56 flex-1 rounded-lg border border-white/[0.14] bg-[#121214] px-3 py-2 text-sm outline-none placeholder:text-zinc-500"
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
                className={`rounded-full px-3 py-1.5 text-xs font-medium ring-1 ${active ? "bg-zinc-100 text-zinc-900 ring-zinc-100" : "bg-[#121214] text-zinc-300 ring-zinc-700 hover:bg-white/[0.05]"}`}
              >
                {v.label} ({n})
              </button>
            );
          })}
        </div>
        <div className="overflow-x-auto rounded-lg border border-white/[0.07]">
          <table className="w-full min-w-[900px] text-left text-sm">
            <thead>
              <tr className="bg-[#121214] text-xs uppercase tracking-wide text-zinc-400">
                <th className="px-4 py-3.5">Job</th>
                <th className="px-4 py-3.5">Negara</th>
                <th className="px-4 py-3.5">Budget</th>
                <th className="px-4 py-3.5">Tier</th>
                <th className="px-4 py-3.5">Umur</th>
                <th className="px-4 py-3.5">Dipost</th>
                <th className="px-4 py-3.5">Rating</th>
                <th className="px-4 py-3.5">Vonis</th>
                <th className="px-4 py-3.5">Alasan</th>
              </tr>
            </thead>
            <tbody>
              {jobs.map((j) => (
                <tr
                  key={j.job_id + j.run_id}
                  onClick={() => { setSel(j); setDraft(null); }}
                  className={`cursor-pointer border-t border-white/[0.07] transition-colors hover:bg-white/[0.03] ${sel?.job_id === j.job_id ? "bg-[#121214]" : ""}`}
                >
                  <td className="max-w-72 px-4 py-3.5">
                    <p className="line-clamp-2 font-medium leading-snug">{j.title}</p>
                    <p className="truncate text-xs text-zinc-400">{(j.keywords || []).join(", ")}</p>
                  </td>
                  <td className="px-4 py-3.5">{j.country || "-"}</td>
                  <td className="px-4 py-3.5">{fmtBudget(j)}</td>
                  <td className="px-4 py-3.5">{j.proposals_tier || "-"}</td>
                  <td className="px-4 py-3.5">{j.age_hours == null ? "-" : `${j.age_hours.toFixed(1)} jam`}</td>
                  <td className="whitespace-nowrap px-4 py-3.5">{j.posted_at ? fmtTime(j.posted_at) + " WIB" : "-"}</td>
                  <td className="px-4 py-3.5">{j.rating == null ? "-" : String(j.rating)}</td>
                  <td className="px-4 py-3.5">
                    <span className={`inline-block rounded-full px-2.5 py-1 text-xs font-medium ring-1 ${badgeCls(j.verdict)}`}>
                      {verdictLabel(j.verdict)}
                    </span>
                  </td>
                  <td className="max-w-64 truncate px-4 py-3 text-zinc-400" title={j.reason}>{j.reason || "-"}</td>
                </tr>
              ))}
              {jobs.length === 0 && (
                <tr><td colSpan={9} className="px-4 py-8 text-center text-zinc-400">Belum ada job buat filter ini.</td></tr>
              )}
            </tbody>
          </table>
        </div>
        <p className="mt-3 text-xs text-zinc-500">Data lokal dari laptop ini. Draft tidak pernah terkirim otomatis; semua apply tetap manual di Upwork.</p>
          </>
        )}
      </main>

      {sel && (
        <>
          <div className="fixed inset-0 z-30 animate-fade-in bg-black/60" onClick={() => setSel(null)} />
          <aside className="fixed inset-y-0 right-0 z-40 w-full max-w-xl overflow-y-auto border-l border-white/[0.07] bg-[#121214] p-5 animate-slide-in">
            <div className="flex items-start justify-between gap-5">
              <h2 className="text-base font-bold">{sel.title}</h2>
              <button onClick={() => setSel(null)} className="rounded-lg bg-white/[0.05] px-3 py-1.5 text-xs hover:bg-white/[0.09]">Tutup</button>
            </div>
            {sel.url && (
              <a href={sel.url} target="_blank" rel="noreferrer" className="mt-1 inline-block text-sm text-zinc-100 underline decoration-white/20 underline-offset-4 hover:decoration-white/40">
                Buka di Upwork
              </a>
            )}
            <div className="mt-4 grid grid-cols-2 gap-4 text-sm">
              <div><p className="text-xs text-zinc-400">Negara</p><p>{sel.country || "-"}</p></div>
              <div><p className="text-xs text-zinc-400">Budget</p><p>{fmtBudget(sel)}</p></div>
              <div><p className="text-xs text-zinc-400">Tier proposal</p><p>{sel.proposals_tier || "-"}</p></div>
              <div><p className="text-xs text-zinc-400">Umur</p><p>{sel.age_hours == null ? "-" : `${sel.age_hours.toFixed(1)} jam`}</p></div>
              <div><p className="text-xs text-zinc-400">Dipost</p><p>{sel.posted_at ? fmtTime(sel.posted_at) + " WIB" : "-"}</p></div>
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
                    <button key={f} onClick={() => openDraft(f)} className="rounded-lg bg-white/[0.05] px-3 py-1.5 text-xs font-medium hover:bg-white/[0.09]">
                      Lihat draft {label}
                    </button>
                  );
                })}
              </div>
              {draftLoading && <p className="mt-3 animate-pulse text-sm text-zinc-400">Memuat draft...</p>}
              {draft && (
                <div key={draft.name} className="mt-3 rounded-lg border border-white/[0.14] bg-[#0a0a0b] p-5 animate-fade-in">
                  <p className="mb-2 text-xs text-zinc-400">{draft.name}</p>
                  <div className="mb-3 flex flex-wrap items-center gap-2">
                    <button onClick={() => setDraftMark(draft.name, "dibaca")} className={`rounded-md px-3 py-1.5 text-xs font-medium ${draftStatus[draft.name] === "dibaca" ? "bg-emerald-400 text-zinc-950" : "bg-white/[0.05] hover:bg-white/[0.09]"}`}>
                      {draftStatus[draft.name] === "dibaca" ? "✓ Dibaca" : "Tandai dibaca"}
                    </button>
                    <button onClick={() => setDraftMark(draft.name, "dilamar")} className={`rounded-md px-3 py-1.5 text-xs font-medium ${draftStatus[draft.name] === "dilamar" ? "bg-sky-400 text-zinc-950" : "bg-white/[0.05] hover:bg-white/[0.09]"}`}>
                      {draftStatus[draft.name] === "dilamar" ? "✓ Dilamar" : "Tandai dilamar"}
                    </button>
                  </div>
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
