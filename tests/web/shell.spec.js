import { expect, test } from "@playwright/test";

test("a unit repo is listed and its page renders with its own stylesheet", async ({ page }) => {
  const errors = [];
  const failed = new Set();
  page.on("pageerror", (error) => errors.push(String(error)));
  page.on("console", (message) => {
    const expected = message.location().url.includes("/api/demo/");
    if (message.type() === "error" && !expected) errors.push(message.text());
  });
  page.on("response", (response) => {
    if (response.status() >= 400) {
      failed.add(`${response.status()} ${new URL(response.url()).pathname}`);
    }
  });

  await page.goto("/");
  const nav = page.locator("#unit-nav");
  await expect(nav.locator('a.nav-unit[data-unit="demo"]')).toContainText("Демо");
  await expect(nav.locator("a.nav-unit")).toHaveCount(2);

  await page.goto("/#/demo");
  const panel = page.locator(".demo-panel");
  await expect(panel).toBeVisible();
  await expect(panel).toHaveCSS("border-left-width", "4px");
  await expect(page.locator('link[data-ui="demo"]')).toHaveAttribute(
    "href",
    /\/js\/units\/demo\/style\.css$/,
  );
  await expect(page.locator(".demo-answer")).toContainText("Служба не ответила");

  expect([...failed]).toEqual(["503 /api/demo/hello"]);
  expect(errors).toEqual([]);
});
