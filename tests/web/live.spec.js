import { expect, test } from "@playwright/test";

const BOT = {
  name: "bot",
  title: "Торговый бот",
  state: "stopped",
  label: "остановлен",
  pid: null,
  since: null,
  adopted: false,
  can: ["start"],
  confirm: "Это торговый процесс.",
  op: null,
  problem: null,
  output: false,
  live_trading: { started: null, restart: false },
};

function registry(enabled) {
  return {
    v: 1,
    control: true,
    broken: [],
    units: [
      {
        id: "alpha",
        kind: "strategy",
        title: "Альфа",
        glyph: "А",
        sub: "",
        order: 10,
        ui: null,
        places_orders: true,
        legacy_pages: [],
        api: null,
        processes: [BOT],
        status: null,
        status_error: null,
        live_trading: { enabled, changed_at: "2026-10-15T09:30:00+00:00", via: "console" },
      },
    ],
  };
}

test("the live switch names the account and asks before real money", async ({ page }) => {
  const errors = [];
  const posts = [];
  let enabled = false;
  page.on("pageerror", (error) => errors.push(String(error)));
  await page.route("**/api/control/units", (route) => route.fulfill({ json: registry(enabled) }));
  await page.route("**/api/control/units/alpha/live-trading", (route) => {
    const request = route.request();
    posts.push({ body: request.postDataJSON(), console: request.headers()["x-tbot-console"] });
    enabled = request.postDataJSON().enabled;
    return route.fulfill({ json: { unit: "alpha", ...registry(enabled).units[0].live_trading } });
  });

  await page.goto("/");
  const card = page.locator('.dash-card[data-unit="alpha"]');
  await expect(card.locator(".dash-badge.paper")).toHaveText("бумажный счёт");

  await page.goto("/#/alpha/processes");
  const toggle = page.getByRole("switch", { name: "Разрешить заявки на живом счёте" });
  await expect(toggle).toHaveAttribute("aria-checked", "false");
  await toggle.click();
  const dialog = page.locator("dialog.live-confirm");
  await expect(dialog).toContainText("РЕАЛЬНЫЕ ДЕНЬГИ");
  await dialog.getByRole("button", { name: "Отмена" }).click();
  await expect(dialog).toHaveCount(0);
  expect(posts).toEqual([]);

  await toggle.click();
  await dialog.getByRole("button", { name: "Разрешить живой счёт" }).click();
  await expect(toggle).toHaveAttribute("aria-checked", "true");
  await expect(page.locator(".live-panel.on")).toContainText("Сейчас: ЖИВОЙ СЧЁТ");
  expect(posts).toEqual([{ body: { enabled: true }, console: "1" }]);

  await page.goto("/");
  const live = card.locator(".dash-badge.live");
  await expect(live).toHaveText("ЖИВОЙ СЧЁТ");
  await expect(live).toHaveCSS("background-color", "rgb(219, 94, 126)");

  await page.goto("/#/alpha/processes");
  await toggle.click();
  await expect(toggle).toHaveAttribute("aria-checked", "false");
  await expect(dialog).toHaveCount(0);
  expect(posts[1]).toEqual({ body: { enabled: false }, console: "1" });
  expect(errors).toEqual([]);
});
