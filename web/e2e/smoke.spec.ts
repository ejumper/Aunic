import { expect, test } from "@playwright/test";
import { startMockWsServer } from "./mock-ws-server";

let mockServer: Awaited<ReturnType<typeof startMockWsServer>> | null = null;

test.beforeAll(async () => {
  mockServer = await startMockWsServer();
});

test.afterAll(async () => {
  await mockServer?.close();
  mockServer = null;
});

test("app loads and connects to the browser websocket", async ({ page }) => {
  await page.goto("/");
  const toolbar = page.locator(".app-toolbar");

  await expect(page.getByRole("button", { name: "Close file explorer" })).toBeVisible();
  await expect(page.getByRole("status", { name: /Browser WebSocket Connected/ })).toBeVisible();
  await expect(toolbar.getByRole("button", { name: "Settings" })).toBeDisabled();
  await expect(toolbar.getByRole("button", { name: /Included/ })).toBeDisabled();
  await expect(toolbar.getByRole("button", { name: /Plans/ })).toBeDisabled();
});

test("manifest is reachable and parseable", async ({ request }) => {
  const response = await request.get("/manifest.webmanifest");

  expect(response.ok()).toBe(true);
  expect(response.headers()["content-type"]).toMatch(
    /application\/manifest\+json|application\/json/,
  );
  expect(await response.json()).toMatchObject({
    name: "Aunic",
    display: "standalone",
    theme_color: "#101312",
  });
});

test("apple touch icon resolves to a webp asset", async ({ page, request }) => {
  await page.goto("/");

  const href = await page.locator('link[rel="apple-touch-icon"]').getAttribute("href");
  expect(href).toBe("/icons/aunic-pwa.webp");

  const response = await request.get(href ?? "");
  expect(response.ok()).toBe(true);
  expect(response.headers()["content-type"]).toMatch(/image\/webp/);
});

test("file explorer opens a note in the editor", async ({ page }) => {
  await page.goto("/");

  await expect(page.getByText("README.md")).toBeVisible();
  await page.getByText("README.md").click();

  await expect(page.locator(".app-toolbar__filename")).toHaveText("README.md");
  await expect(page.locator(".code-editor-host")).toBeVisible();
});

test("file explorer toggle hides and shows the side panel", async ({ page }) => {
  await page.goto("/");

  await expect(page.getByRole("complementary", { name: "File explorer" })).toBeVisible();
  await page.getByRole("button", { name: "Close file explorer" }).click();

  await expect(page.getByRole("complementary", { name: "File explorer" })).toHaveCount(0);
  await expect(page.getByRole("button", { name: "Open file explorer" })).toBeVisible();

  await page.getByRole("button", { name: "Open file explorer" }).click();
  await expect(page.getByRole("complementary", { name: "File explorer" })).toBeVisible();
});

test("prompt composer is visible for an open note", async ({ page }) => {
  await page.goto("/");

  await page.getByText("README.md").click();

  await expect(page.locator(".prompt-composer")).toBeVisible();
  await expect(page.locator(".context-meter")).toBeVisible();
  await expect(page.getByLabel("Send")).toBeVisible();
});

test("editor scrolls internally while transcript and prompt stay visible", async ({ page }) => {
  await page.goto("/");
  await page.getByText("README.md").click();

  const scroller = page.locator(".code-editor-host .cm-scroller");
  await expect(scroller).toBeVisible();
  await expect(page.locator(".transcript-pane")).toBeVisible();
  await expect(page.locator(".prompt-composer")).toBeVisible();

  const scrollMetrics = await scroller.evaluate((node) => ({
    scrollHeight: node.scrollHeight,
    clientHeight: node.clientHeight,
  }));
  expect(scrollMetrics.scrollHeight).toBeGreaterThan(scrollMetrics.clientHeight);
});

test("transcript is positioned above the prompt composer", async ({ page }) => {
  await page.goto("/");
  await page.getByText("README.md").click();

  const transcriptBox = await page.locator(".transcript-pane").boundingBox();
  const promptBox = await page.locator(".prompt-composer").boundingBox();

  expect(transcriptBox).not.toBeNull();
  expect(promptBox).not.toBeNull();
  expect(transcriptBox!.y + transcriptBox!.height).toBeLessThanOrEqual(promptBox!.y + 1);
});

test("transcript resizes from its top separator", async ({ page }) => {
  await page.goto("/");
  await page.getByText("README.md").click();

  const transcript = page.locator(".transcript-pane");
  const handle = page.getByRole("separator", { name: "Resize transcript" });
  await expect(handle).toBeVisible();
  await expect(transcript).toHaveCSS("resize", "none");

  const before = await transcript.boundingBox();
  const handleBox = await handle.boundingBox();
  expect(before).not.toBeNull();
  expect(handleBox).not.toBeNull();

  await page.mouse.move(handleBox!.x + handleBox!.width / 2, handleBox!.y + handleBox!.height / 2);
  await page.mouse.down();
  await page.mouse.move(handleBox!.x + handleBox!.width / 2, handleBox!.y - 72);
  await page.mouse.up();

  const after = await transcript.boundingBox();
  expect(after).not.toBeNull();
  expect(after!.height).toBeGreaterThan(before!.height + 32);
});
