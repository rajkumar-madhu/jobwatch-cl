import { expect, test } from "@playwright/test";

// R38: the Logs page rendered its (already-capped-at-200) result set as one flat, unbroken list
// with no date/hour grouping and no way to reach anything past the cap. Fixed with hour-grouping
// headings and a "Load more" control that walks `until` backwards through time. The mock returns
// 276 synthetic rows spaced 5 minutes apart across >20 hours, so a 30-day window pulls all of them
// across two pages (200 + 76) and gives the grouping logic more than one hour bucket to draw.
test("logs page groups by hour and can load more than the first page", async ({ page }) => {
  await page.goto("/logs");
  await page.getByRole("combobox").nth(4).selectOption("720"); // "Last 30 days"
  await page.getByRole("button", { name: "Search" }).click();

  const headings = page.getByTestId("log-hour-heading");
  await expect(headings.first()).toBeVisible({ timeout: 10_000 });
  const headingCountBefore = await headings.count();
  expect(headingCountBefore).toBeGreaterThan(1); // more than one hour bucket across >20h of data

  const entries = page.locator("details");
  const countBefore = await entries.count();
  expect(countBefore).toBe(200); // first page, capped

  const loadMore = page.getByRole("button", { name: "Load more" });
  await expect(loadMore).toBeVisible();
  await loadMore.click();

  await expect(entries).toHaveCount(276, { timeout: 10_000 }); // both pages, 200 + 76
  // all 276 rows fetched — nothing left to page through
  await expect(page.getByRole("button", { name: "Load more" })).toHaveCount(0);
});

// R38: on a 390px-class viewport the 5-column jobs table relied on horizontal scroll inside `.tbl`
// to reach "Last run" and "Score" — technically reachable, not actually usable. Desktop keeps the
// table; narrow viewports get the same data as stacked cards instead. One test, both projects: it
// reads its own viewport and asserts whichever layout that width is supposed to show.
test("jobs page shows a table on desktop and stacked cards on narrow viewports, never both", async ({ page }) => {
  await page.goto("/jobs", { waitUntil: "networkidle" });
  const table = page.locator(".tbl").first();
  const firstCard = page.locator('a[href^="/jobs/"]').filter({ hasText: /Score/ }).first();
  const width = page.viewportSize()!.width;

  if (width >= 640) {
    await expect(table).toBeVisible();
    await expect(firstCard).toBeHidden(); // present in the DOM (sm:hidden), not shown
  } else {
    await expect(table).toBeHidden();
    await expect(firstCard).toBeVisible();
  }
});
