import { promises as fs } from "fs";
import path from "path";
import { NextResponse } from "next/server";

const FILE = path.join(process.cwd(), "..", "draft_status.json");

async function readStatuses(): Promise<Record<string, string>> {
  try {
    const raw = await fs.readFile(FILE, "utf-8");
    const parsed = JSON.parse(raw);
    return parsed && typeof parsed === "object" ? (parsed as Record<string, string>) : {};
  } catch {
    return {};
  }
}

export async function GET() {
  const statuses = await readStatuses();
  return NextResponse.json({ statuses });
}

export async function POST(req: Request) {
  let body: { file?: unknown; status?: unknown } = {};
  try {
    body = (await req.json()) as { file?: unknown; status?: unknown };
  } catch {
    return NextResponse.json({ error: "badan JSON tidak valid" }, { status: 400 });
  }
  const file = typeof body.file === "string" ? path.basename(body.file) : "";
  const status = typeof body.status === "string" ? body.status : "";
  if (!file.endsWith(".txt") || file !== body.file) {
    return NextResponse.json({ error: "nama file tidak valid" }, { status: 400 });
  }
  if (status !== "" && status !== "dibaca" && status !== "dilamar") {
    return NextResponse.json({ error: "status tidak valid" }, { status: 400 });
  }
  const statuses = await readStatuses();
  if (status) statuses[file] = status;
  else delete statuses[file];
  await fs.writeFile(FILE, JSON.stringify(statuses, null, 2), "utf-8");
  return NextResponse.json({ statuses });
}
