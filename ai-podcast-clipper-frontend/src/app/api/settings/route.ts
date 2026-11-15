import { NextResponse } from "next/server";
import { readSettings, saveSettings } from "~/lib/app-settings";

export async function GET() {
  return NextResponse.json(await readSettings());
}

/** Saves a partial update, e.g. {"currency": "inr"}; returns the full settings. */
export async function PUT(request: Request) {
  let body: unknown;
  try {
    body = await request.json();
  } catch {
    return NextResponse.json({ error: "Expected JSON" }, { status: 400 });
  }
  return NextResponse.json(await saveSettings(body));
}
