import { test, expect } from "@playwright/test";

// R59: the docs site renders every page, its links resolve, search works, and the API reference lists endpoints.
const PAGES = ["quickstart", "pings", "linux-agent", "kubernetes", "import", "alerts", "run-checks", "maintenance", "oncall", "sso", "scim",
  "audit-streaming", "security", "datadog", "terraform", "how-it-works", "deploy"];

test("docs index lists every page and links resolve", async ({ page, request }) => {
  test.setTimeout(120_000);   // crawls every docs page and checks each link
  await page.goto("/docs");
  await expect(page.getByRole("heading", { name: "JobWatch documentation" })).toBeVisible();
  for (const slug of PAGES) await expect(page.locator(`main a[href="/docs/${slug}"], a[href="/docs/${slug}"]`).first()).toBeAttached();
  // every internal /docs link on every page answers 200
  const seen = new Set<string>();
  for (const slug of ["", ...PAGES.map((s) => "/" + s), "/api"]) {
    await page.goto(`/docs${slug}`);
    const hrefs = await page.locator("a[href^='/docs']").evaluateAll((as) => as.map((a) => (a as HTMLAnchorElement).getAttribute("href")!.split("#")[0]));
    for (const h of hrefs) seen.add(h);
  }
  for (const h of seen) expect((await request.get(h)).status(), h).toBe(200);
});

test("a doc page renders headings, code, tables and an on-page table of contents", async ({ page }) => {
  await page.goto("/docs/pings");
  await expect(page.getByRole("heading", { level: 1, name: "Pings and heartbeat API" })).toBeVisible();
  await expect(page.locator(".doc-content table")).toHaveCount(1);
  await expect(page.locator(".doc-content pre").first()).toContainText("/ping/<token>");
  await expect(page.locator("#json-heartbeat-api")).toBeAttached();
  await expect(page.getByRole("heading", { level: 1 })).not.toContainText("(R");     // round labels stripped
});

test("search finds pages by section heading", async ({ page, isMobile }) => {
  await page.goto("/docs/quickstart");
  if (isMobile) await page.getByRole("button", { name: "Browse the docs" }).click();
  await page.locator('input[placeholder="Search the docs"]:visible').first().fill("Splunk");
  const results = page.locator('ul[aria-label="Search results"]:visible').first();
  await expect(results.getByRole("link").first()).toContainText("Streaming the audit log");
});

test("API reference lists endpoints from the OpenAPI document", async ({ page }) => {
  await page.goto("/docs/api");
  await expect(page.getByRole("heading", { level: 1, name: "API reference" })).toBeVisible();
  await expect(page.getByText("/api/v1/jobs/{job_id}/executions")).toBeVisible();
  await expect(page.getByText("/api/v1/audit-streams").first()).toBeVisible();
});

// R61: Datadog-docs-style helpers.
test("tabs, breadcrumbs, callouts, further reading and copy buttons", async ({ page }) => {
  await page.goto("/docs/pings");
  await expect(page.getByRole("navigation", { name: "Documentation areas" }).getByRole("link", { name: "Guides" })).toHaveAttribute("aria-current", "page");
  const crumbs = page.getByRole("navigation", { name: "Breadcrumb" });
  await expect(crumbs).toContainText("Get started");
  await expect(page.locator(".callout-warning")).toContainText("works like a password");
  await expect(page.getByRole("heading", { name: "Further reading" })).toBeVisible();
  await expect(page.locator(".copy-btn").first()).toBeVisible();
  await page.getByRole("link", { name: "API reference" }).first().click();
  await expect(page).toHaveURL(/\/docs\/api$/);
});

test("your-hosts selector rewrites code samples and is remembered", async ({ page }) => {
  await page.goto("/docs/pings");
  const sample = page.locator(".doc-content pre code").first();
  await expect(sample).toContainText("ingest.example.com");
  await page.getByRole("button", { name: "Use your JobWatch hosts" }).click();
  await page.getByLabel("Ingest host").fill("https://ingest.acme.io/");
  await page.getByRole("button", { name: "Done" }).click();
  await expect(sample).toContainText("ingest.acme.io");
  await expect(sample).not.toContainText("example.com");
  await page.goto("/docs/import");                                     // another page: still applied
  await expect(page.locator(".doc-content").getByText("https://ingest.acme.io/hc/").first()).toBeVisible();
  await page.getByRole("button", { name: /Samples use/ }).click();
  await page.getByRole("button", { name: "Use the examples" }).click();
  await expect(page.locator(".doc-content")).toContainText("<ingest>/hc/");
});

test("llms.txt indexes every docs page", async ({ request }) => {
  const r = await request.get("/llms.txt");
  expect(r.status()).toBe(200);
  const body = await r.text();
  expect(body).toContain("# WeCrew JobWatch");
  for (const s of ["quickstart", "pings", "linux-agent", "datadog", "deploy"]) expect(body).toContain(`(/docs/${s})`);
  expect(body).toContain("(/docs/api)");
});
