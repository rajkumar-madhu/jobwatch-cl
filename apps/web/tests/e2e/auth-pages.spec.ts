import { test, expect } from "@playwright/test";

// R51: sign-in and sign-up only choose a route; credentials are entered on Keycloak.
// The mock API (tools/ui-review) offers three social providers and says @acme.com has SSO.
test.describe("auth pages", () => {
  test("sign-in shows configured providers and routes email by SSO discovery", async ({ page }) => {
    await page.goto("/login");
    await expect(page.getByRole("heading", { name: "Sign in to JobWatch" })).toBeVisible();
    await expect(page.getByRole("link", { name: "Continue with GitHub" })).toHaveAttribute("href", /\/auth\/login\?.*idp=github/);
    // a domain with SSO goes to /auth/sso
    let target = "";
    await page.route("**/auth/sso**", (r) => { target = r.request().url(); return r.fulfill({ status: 200, body: "ok" }); });
    await page.getByLabel("Work email").fill("ana@acme.com");
    await page.getByRole("button", { name: "Continue" }).click();
    await expect.poll(() => target).toContain("/auth/sso?");
    expect(target).toContain("email=ana%40acme.com");
  });

  test("sign-in without SSO goes to the password step with the email filled in", async ({ page }) => {
    let target = "";
    await page.route("**/auth/login?**", (r) => { target = r.request().url(); return r.fulfill({ status: 200, body: "ok" }); });
    await page.goto("/login?next=/jobs");
    await page.getByLabel("Work email").fill("bob@example.org");
    await page.getByRole("button", { name: "Continue" }).click();
    await expect.poll(() => target).toContain("login_hint=bob%40example.org");
    expect(target).toContain("next=%2Fjobs");
  });

  test("sign-up offers providers and sends email sign-ups to registration", async ({ page }) => {
    await page.goto("/signup");
    await expect(page.getByRole("heading", { name: "Start monitoring free" })).toBeVisible();
    await expect(page.getByRole("link", { name: "Continue with Google" })).toHaveAttribute("href", /\/auth\/signup\?.*idp=google/);
    let target = "";
    await page.route("**/auth/signup?**", (r) => { target = r.request().url(); return r.fulfill({ status: 200, body: "ok" }); });
    await page.getByLabel("Work email").fill("new@example.org");
    await page.getByRole("button", { name: "Create account" }).click();
    await expect.poll(() => target).toContain("email=new%40example.org");
    expect(target).toContain("next=%2Fonboarding");
  });

  test("landing calls to action lead to sign-up", async ({ page }) => {
    await page.goto("/welcome");
    await expect(page.getByRole("link", { name: "Start monitoring free" }).first()).toHaveAttribute("href", "/signup");
    await expect(page.getByRole("link", { name: "Sign in" }).first()).toHaveAttribute("href", "/login");
  });
});
