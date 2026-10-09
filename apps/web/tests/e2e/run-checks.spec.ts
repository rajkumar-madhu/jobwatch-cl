import { test, expect } from "@playwright/test";

// R54: job page shows the job's run checks, a run's metrics, and why a run that exited 0 failed.
test("run checks and a failed check are shown on the job page", async ({ page, request }) => {
  const api = process.env.E2E_API_URL ?? "http://localhost:8000";
  const jobs = (await (await request.get(`${api}/api/v1/jobs`)).json()).items;
  const job = jobs.find((j: any) => j.name === "nightly-database-backup");
  await page.route("**/api/v1/executions/*", async (route) => {
    if (route.request().url().includes("/compare/")) return route.continue();
    const resp = await route.fetch();
    const body = await resp.json();
    body.execution = { ...body.execution, status: "failed", exit_code: 0, metrics: { size_bytes: 0 },
      meta: { assertion_failures: ["metric.size_bytes > 0 (was 0)"] } };
    await route.fulfill({ response: resp, json: body });
  });
  await page.goto(`/jobs/${job.id}`);
  const checks = page.getByRole("region", { name: "Run checks" });
  await expect(checks.getByText("metric.size_bytes > 0")).toBeVisible();
  await expect(checks.getByText("duration < 2h")).toBeVisible();
  await expect(page.getByText("Exited successfully but failed its run checks")).toBeVisible();
  await expect(page.getByText("metric.size_bytes > 0 (was 0)")).toBeVisible();
  await expect(page.getByText("size_bytes = 0")).toBeVisible();
});
