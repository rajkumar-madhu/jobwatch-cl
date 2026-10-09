import { test, expect } from "@playwright/test";

// R66: the On-call page — active pages with acknowledge, schedules with who is on call, policies.
test("on-call page shows the active page, schedules and policies", async ({ page }) => {
  await page.goto("/oncall");
  const paging = page.getByRole("region", { name: "Paging now" });
  await expect(paging.getByText("customer-report-generator")).toBeVisible();
  await expect(paging.getByText(/paged Ana Rao/)).toBeVisible();
  await expect(paging.getByRole("button", { name: "Acknowledge" })).toBeVisible();
  const schedules = page.getByRole("region", { name: "Schedules" });
  await expect(schedules.getByText("Platform primary", { exact: true })).toBeVisible();
  await expect(schedules.getByText("Asia/Kolkata", { exact: false }).first()).toBeVisible();
  await expect(schedules.getByText("override").first()).toBeVisible();
  const policies = page.getByRole("region", { name: "Escalation policies" });
  await expect(policies.getByText(/1\. Platform primary \(on call\), then wait 15 min/)).toBeVisible();
  await expect(policies.getByText(/2\. Cy Diaz, then wait 15 min/)).toBeVisible();
});

test("new schedule form numbers people in rotation order", async ({ page }) => {
  await page.goto("/oncall");
  await page.getByRole("button", { name: "New schedule" }).click();
  const people = page.getByRole("group", { name: "People, in rotation order" });
  await people.getByText("Ben Ito").click();
  await people.getByText("Ana Rao").click();
  await expect(people.getByText("1. Ben Ito")).toBeVisible();
  await expect(people.getByText("2. Ana Rao")).toBeVisible();
  await expect(page.getByRole("button", { name: "Create schedule" })).toBeDisabled();   // needs name and handoff
});

test("a page link highlights the page to acknowledge", async ({ page }) => {
  await page.goto("/oncall?ack=5e000000-0000-4000-8000-0000000000c1");
  await expect(page.getByRole("region", { name: "Paging now" }).locator(".bg-accent\\/10")).toHaveCount(1);
});
