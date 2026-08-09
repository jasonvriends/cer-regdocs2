export const runtime = "nodejs";

export async function GET() {
  return Response.json({
    service: "regdocs-atlas-ui",
    status: "ok",
  });
}
