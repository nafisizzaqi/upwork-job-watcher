import { promises as fs } from "fs";
import path from "path";
import { NextResponse } from "next/server";


export async function GET(req: Request) {
  const { searchParams } = new URL(req.url);
  const file = searchParams.get("file") ?? "";
  const name = path.basename(file);
  if (!name.endsWith(".txt") || name !== file) {
    return NextResponse.json({ error: "nama file tidak valid" }, { status: 400 });
  }
  try {
    const content = await fs.readFile(path.join(process.cwd(), "..", "drafts", name), "utf-8");
    return NextResponse.json({ name, content });
  } catch {
    return NextResponse.json({ error: "file tidak ditemukan" }, { status: 404 });
  }
}
