import { test, expect } from "@playwright/test";

// R65: the Kubernetes page shows time zones, annotation problems and CronJobs excluded with jobwatch.io/ignore.
test("CronJob time zone, annotation warning and not-monitored badge", async ({ page }) => {
  await page.goto("/kubernetes");
  await expect(page.getByText("Asia/Kolkata")).toBeVisible();
  await expect(page.getByText(/jobwatch\.io\/grace: 'soon' is not a duration/)).toBeVisible();
  const ignored = page.getByRole("link", { name: /scratch-cleanup/ });
  await expect(ignored.getByText("not monitored")).toBeVisible();
  await expect(ignored.getByText("ignored")).toBeVisible();
});
