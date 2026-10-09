import { test, expect } from "@playwright/test";

// R63: maintenance windows on the Alerting page.
test("lists windows with repeat, scope, next occurrence and active state", async ({ page }) => {
  await page.goto("/alerting");
  const section = page.getByRole("region", { name: "Maintenance windows" });
  await expect(section.getByText("Sunday database patching")).toBeVisible();
  await expect(section.getByText(/Weekly on Sun · tags db · Asia\/Kolkata/)).toBeVisible();
  await expect(section.getByText(/^Next:/)).toBeVisible();
  await expect(section.getByText("Active now")).toBeVisible();
  await expect(section.getByRole("button", { name: "End now" })).toHaveCount(1);       // one-off and active only
});

test("the form offers weekdays only for weekly windows", async ({ page }) => {
  await page.goto("/alerting");
  const section = page.getByRole("region", { name: "Maintenance windows" });
  await section.getByRole("button", { name: "Schedule maintenance" }).click();
  await expect(section.getByText("On", { exact: true })).toHaveCount(0);
  await section.getByLabel("Repeats").selectOption("weekly");
  await expect(section.getByText("Sun", { exact: true })).toBeVisible();
  await expect(section.getByRole("button", { name: "Save window" })).toBeDisabled();       // needs a start time
});
