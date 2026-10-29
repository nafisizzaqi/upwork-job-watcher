import { promises as fs } from "fs";
import path from "path";
import { NextResponse } from "next/server";


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

async function readLines(file: string): Promise<Rec[]> {
  try {
    const txt = await fs.readFile(file, "utf-8");
    const out: Rec[] = [];
    for (const line of txt.split("\n")) {
      const t = line.trim();
      if (!t) continue;
      try { out.push(JSON.parse(t)); } catch { /* lewati baris rusak */ }
    }
    return out;
  } catch { return []; }
}

export async function GET(req: Request) {
  const base = path.join(process.cwd(), "..");
  const recs = await readLines(path.join(base, "dashboard_history.jsonl"));
  const byRun = new Map<string, { run_at: string; jobs: Rec[] }>();
  for (const r of recs) {
    const g = byRun.get(r.run_id) ?? { run_at: r.run_at, jobs: [] };
    g.jobs.push(r);
    byRun.set(r.run_id, g);
  }
  const runs = [...byRun.entries()]
    .map(([run_id, g]) => ({ run_id, run_at: g.run_at, total: g.jobs.length }))
    .sort((a, b) => (a.run_id < b.run_id ? 1 : -1))
    .slice(0, 10);
  const { searchParams } = new URL(req.url);
  const want = searchParams.get("run");
  const latest = runs[0];
  const sel = runs.find((r) => r.run_id === want) ?? latest;
  const jobs = sel ? byRun.get(sel.run_id)?.jobs ?? [] : [];
  let liveCount = 0;
  try {
    const live = JSON.parse(await fs.readFile(path.join(base, "jobs_live.json"), "utf-8"));
    liveCount = Array.isArray(live) ? live.length : (live?.jobs?.length ?? 0);
  } catch { /* abaikan */ }
  return NextResponse.json({
    generated_at: new Date().toISOString(),
    history_exists: recs.length > 0,
    live_count: liveCount,
    runs,
    selected_run: sel?.run_id ?? null,
    jobs,
  });
}
