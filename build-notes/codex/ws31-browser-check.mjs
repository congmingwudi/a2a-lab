import process from "node:process";
import { readFileSync, writeFileSync } from "node:fs";

const { chromium } = await import(process.env.PLAYWRIGHT_MODULE);

const base = process.argv[2] || "http://127.0.0.1:8200";
const outDir = "build-notes/codex/screenshots/ws31";
const results = {};

function check(condition, message) {
  if (!condition) throw new Error(message);
}

async function signIn(page, username, password) {
  check(password, `Missing password for ${username}`);
  await page.goto(base, { waitUntil: "domcontentloaded" });
  const response = await page.evaluate(async ({ username: user, password: secret }) => {
    const r = await fetch("/api/login", {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ username: user, password: secret }),
    });
    return { ok: r.ok, status: r.status, data: await r.json() };
  }, { username, password });
  check(response.ok, `Sign-in failed for ${username}: HTTP ${response.status}`);
  await page.evaluate(({ token, user }) => {
    localStorage.setItem("a2a-user-jwt", token);
    localStorage.setItem("a2a-user", JSON.stringify(user));
    localStorage.setItem("a2a-side-open", "1");
  }, response.data);
  await page.reload({ waitUntil: "domcontentloaded" });
  await page.waitForSelector("#memory-presentation-section", { state: "attached" });
  if (await page.locator("main").evaluate(el => el.classList.contains("side-closed"))) {
    await page.locator(".side-handle").click();
  }
  if (!await page.locator("#accExperiments").evaluate(el => el.open)) {
    await page.locator("#accExperiments > summary").click();
  }
  await page.waitForFunction(() => labConfig && labConfig.memory_customers
    && Object.keys(labConfig.memory_customers).length > 0 && scenarios.length > 0);
  await page.waitForSelector("#memory-presentation-section", { state: "visible" });
}

async function enterFromSection(page) {
  await page.locator("#memory-presentation-section").focus();
  await page.keyboard.press("Enter");
  await page.waitForSelector(".memory-present");
  check(await page.locator('.memory-scene-link[aria-current="step"]').count() === 1,
    "Exactly one scene must be current");
}

async function currentScene(page) {
  return page.evaluate(() => view && view.type === "memory-presentation" ? Number(view.scene) : -1);
}

async function setScene(page, scene) {
  await page.locator(`.memory-scene-link`).nth(scene).click();
  await page.waitForFunction(expected => view && view.type === "memory-presentation"
    && Number(view.scene) === expected, scene);
}

const browser = await chromium.launch({
  channel: "chrome",
  headless: true,
});

try {
  const operatorContext = await browser.newContext({
    viewport: { width: 1440, height: 1024 },
    deviceScaleFactor: 1,
  });
  const operator = await operatorContext.newPage();
  const consoleErrors = [];
  const pageErrors = [];
  const resourceFailures = [];
  operator.on("console", msg => {
    if (msg.type() === "error" && !msg.text().startsWith("Failed to load resource")) {
      consoleErrors.push(msg.text());
    }
  });
  operator.on("pageerror", error => pageErrors.push(String(error)));
  operator.on("response", response => {
    if (response.status() >= 400) {
      const url = new URL(response.url());
      resourceFailures.push(`${response.status()} ${url.pathname}`);
    }
  });

  await signIn(operator, "ryan", process.env.A2ALAB_MASTER_PASSWORD);
  consoleErrors.length = 0;
  pageErrors.length = 0;
  resourceFailures.length = 0;
  await operator.selectOption("#designTheme", "canvas");
  await operator.locator("#themeLight").click();

  await enterFromSection(operator);
  check(await currentScene(operator) === 0, "Section entry must start at scene 1");
  results.section_entry = "pass";

  await operator.locator(".memory-present-controls .next").click();
  check(await currentScene(operator) === 1, "Next must advance to scene 2");
  check(await operator.locator("#memoryPresentationStatus").textContent()
    .then(text => text.startsWith("Scene 2:")), "Scene change must update the live-region text");
  check(await operator.locator("#memoryPresentationStatus").getAttribute("aria-atomic") === "true",
    "Scene live region must announce atomically");
  await setScene(operator, 7);
  await operator.locator("#memoryPresentationStage").focus();
  await operator.keyboard.press("ArrowLeft");
  check(await currentScene(operator) === 6, "ArrowLeft must move to scene 7");
  await operator.keyboard.press("ArrowRight");
  check(await currentScene(operator) === 7, "ArrowRight must move to scene 8");
  await operator.waitForTimeout(50); // let the scene-change focus frame settle
  await operator.locator(".memory-scene-link").nth(7).focus();
  await operator.evaluate(() => renderMain());
  check(await operator.locator(".memory-scene-link").nth(7)
    .evaluate(el => document.activeElement === el),
  "An unforced background render must preserve presentation focus");
  check(await operator.locator(".memory-present-controls .next").isDisabled(),
    "Next must be disabled at the last scene");
  results.scene_controller = "pass";

  await setScene(operator, 0);
  await operator.locator("#guideBtn").click();
  await operator.locator("#gdInput").focus();
  await operator.keyboard.press("Escape");
  check(await currentScene(operator) === 0,
    "Escape in a text-entry control must not leave the presentation");
  await operator.locator("#guideDrawer .x").click();
  const decisionChip = operator.locator(".memory-present .dchip").first();
  await decisionChip.waitFor({ state: "visible" });
  await decisionChip.click();
  await operator.waitForSelector(".dpopover");
  await operator.keyboard.press("Escape");
  await operator.waitForSelector(".dpopover", { state: "detached" });
  check(await currentScene(operator) === 0, "First Escape must close the popover, not the presentation");
  await operator.keyboard.press("Escape");
  await operator.waitForSelector(".memory-present", { state: "detached" });
  check(await operator.locator("#memory-presentation-section").evaluate(el => document.activeElement === el),
    "Exit must restore focus to the section launcher");
  results.escape_precedence_and_focus = "pass";

  for (let cycle = 0; cycle < 2; cycle += 1) {
    await enterFromSection(operator);
    check(await operator.locator("main.presentation-mode").count() === 1,
      "Presentation suppression must be singular");
    await operator.locator(".memory-present-top button").click();
    await operator.waitForSelector(".memory-present", { state: "detached" });
  }
  results.reentry = "pass";

  const memoryGroup = operator.locator(".nav-group").filter({ hasText: "Agent Memory" });
  if (!await memoryGroup.evaluate(el => el.classList.contains("open"))) {
    await memoryGroup.locator(".gtitle").click();
  }
  await operator.waitForSelector("#memory-presentation-customer-memory-claude");
  await operator.locator("#memory-presentation-customer-memory-claude").click();
  await operator.waitForSelector(".memory-present");
  await operator.keyboard.press("Escape");
  check(await operator.locator("#memory-presentation-customer-memory-claude")
    .evaluate(el => document.activeElement === el),
  "Scenario shortcut exit must restore focus to its launcher");
  results.scenario_shortcut = "pass";

  await operator.evaluate(() => openExperiment("scenario", "customer-memory-claude"));
  await operator.waitForSelector('select[aria-label="Customer persona"]');
  await operator.selectOption('select[aria-label="Customer persona"]', "jordan");
  await operator.locator("#memory-presentation-tab-customer-memory-claude").click();
  await operator.waitForSelector(".memory-present");
  await operator.keyboard.press("Escape");
  check(await operator.locator("#memory-presentation-tab-customer-memory-claude")
    .evaluate(el => document.activeElement === el),
  "Experiment-tab exit must restore focus to its launcher");
  await operator.locator("#memory-presentation-tab-customer-memory-claude").click();
  await operator.waitForSelector(".memory-present");
  check(await operator.locator("#memoryPresentationStatus").count() === 1,
    "Persistent presentation live region must remain attached");
  await setScene(operator, 7);
  check(await operator.locator(".memory-ready-row").first().textContent()
    .then(text => text.includes("Jordan")), "Presentation must preserve the selected Jordan customer");
  const turnsBefore = await operator.locator(".turn.user").count();
  await operator.locator(".memory-launchers .primary").click();
  await operator.waitForFunction(() => view && view.type === "experiment"
    && view.name === "customer-memory-claude");
  check(await operator.locator("#chatInput").inputValue().then(value => value.includes("Remember this")),
    "Claude handoff must load the seeded prompt");
  check(await operator.locator('input[name="memoryMode"][value="on"]').isChecked(),
    "Claude handoff must enable Memory");
  check(await operator.locator('select[aria-label="Customer persona"]').inputValue() === "jordan",
    "Claude handoff must preserve the non-default Jordan customer");
  check(await operator.locator(".turn.user").count() === turnsBefore,
    "Claude handoff must not send automatically");
  await operator.evaluate(() => goBack());
  await operator.waitForFunction(() => view && view.type === "memory-presentation"
    && Number(view.scene) === 7);
  results.claude_handoff_and_back = "pass";
  results.experiment_tab_shortcut = "pass";

  await operator.locator(".memory-launchers .ghost").click();
  await operator.waitForFunction(() => view && view.type === "experiment"
    && view.name === "customer-memory-openai");
  check(await operator.locator("#chatInput").inputValue().then(value => value.includes("Recall my rollout")),
    "OpenAI handoff must load the seeded prompt");
  check(await operator.locator('input[name="memoryMode"][value="on"]').isChecked(),
    "OpenAI handoff must enable Memory");
  results.openai_handoff = "pass";

  await operator.evaluate(() => goBack());
  await operator.waitForSelector(".memory-present");
  await operator.evaluate(() => {
    localStorage.setItem("a2a-side-open", "1");
    labUser = null;
    renderUserBtn();
    applyAuthGating();
  });
  check(await operator.locator("main.presentation-mode").count() === 1,
    "Session expiry must not leave presentation suppression stale");
  await operator.evaluate(() => {
    labUser = JSON.parse(localStorage.getItem("a2a-user"));
    renderUserBtn();
    applyAuthGating();
  });
  await operator.locator(".memory-present-top button").click();
  check(await operator.locator("main.presentation-mode").count() === 0,
    "Leaving after auth restoration must remove presentation suppression");
  check(await operator.evaluate(() => localStorage.getItem("a2a-side-open")) === "1",
    "Presentation/auth transitions must preserve the saved panel preference");
  check(!await operator.locator("main").evaluate(el => el.classList.contains("side-closed")),
    "Leaving after sign-in restoration must restore the saved open panel preference");
  results.auth_transition = "pass";

  await enterFromSection(operator);
  await operator.emulateMedia({ reducedMotion: "reduce" });
  const reducedMotion = await operator.locator(".memory-present-stage").evaluate(el => {
    const style = getComputedStyle(el);
    return {
      matched: matchMedia("(prefers-reduced-motion: reduce)").matches,
      transition: style.transitionDuration,
      animation: style.animationDuration,
    };
  });
  const allNearZero = value => value.split(",").every(item => Number.parseFloat(item) <= 0.001);
  check(reducedMotion.matched && allNearZero(reducedMotion.transition)
    && allNearZero(reducedMotion.animation),
    `Reduced-motion preference must remove transitions and animations: ${JSON.stringify(reducedMotion)}`);
  results.reduced_motion_and_live_region = "pass";
  await operator.emulateMedia({ reducedMotion: "no-preference" });
  await setScene(operator, 7);
  await operator.screenshot({ path: `${outDir}/implementation-canvas-light-desktop.png` });
  await operator.locator("#themeDark").click();
  await operator.screenshot({ path: `${outDir}/implementation-canvas-dark-desktop.png` });
  await operator.setViewportSize({ width: 390, height: 844 });
  await operator.locator("#themeLight").click();
  await operator.screenshot({ path: `${outDir}/implementation-canvas-light-narrow.png` });
  await operator.locator("#themeDark").click();
  await operator.screenshot({ path: `${outDir}/implementation-canvas-dark-narrow.png` });
  check(await operator.locator(".memory-present-top button").isVisible(), "Narrow Exit must remain visible");
  check(await operator.locator(".memory-present-controls").isVisible(), "Narrow controls must remain visible");
  results.responsive_and_themes = "pass";

  check(pageErrors.length === 0, `Uncaught page errors: ${pageErrors.join(" | ")}`);
  check(consoleErrors.length === 0, `Console errors: ${consoleErrors.join(" | ")}`);
  results.operator_console_errors = [];
  results.background_http_failures = [...new Set(resourceFailures)];

  const sourceVisual = readFileSync(`${outDir}/agent-memory-presentation-target.png`).toString("base64");
  const implementationVisual = readFileSync(`${outDir}/implementation-canvas-light-desktop.png`).toString("base64");
  const comparison = await operatorContext.newPage();
  await comparison.setViewportSize({ width: 1600, height: 700 });
  await comparison.setContent(`<!doctype html><style>
    *{box-sizing:border-box}body{margin:0;padding:18px;background:#ececec;font:14px system-ui;color:#111}
    main{display:grid;grid-template-columns:1fr 1fr;gap:18px}figure{margin:0;background:white;border:1px solid #bbb}
    figcaption{padding:9px 12px;font-weight:700;border-bottom:1px solid #ddd}img{display:block;width:100%;height:auto}
  </style><main>
    <figure><figcaption>Approved visual target</figcaption><img src="data:image/png;base64,${sourceVisual}"></figure>
    <figure><figcaption>Browser-rendered implementation</figcaption><img src="data:image/png;base64,${implementationVisual}"></figure>
  </main>`);
  await comparison.screenshot({ path: `${outDir}/comparison-target-vs-implementation.png`, fullPage: true });
  await comparison.close();
  await operatorContext.close();

  const viewerContext = await browser.newContext({ viewport: { width: 1440, height: 1024 } });
  const viewer = await viewerContext.newPage();
  const viewerErrors = [];
  viewer.on("pageerror", error => viewerErrors.push(String(error)));
  await signIn(viewer, "vic", process.env.A2ALAB_VIEWER_PASSWORD);
  await enterFromSection(viewer);
  await setScene(viewer, 7);
  check(await viewer.locator(".memory-launchers .primary").textContent().then(text => text.includes("details")),
    "Viewer CTA must say it opens details");
  await viewer.locator(".memory-launchers .primary").click();
  await viewer.waitForFunction(() => view && view.type === "experiment");
  check(await viewer.locator(".tab.on").textContent().then(text => text.trim() === "Details"),
    "Viewer handoff must land on Details");
  check(await viewer.locator("#chatInput").count() === 0, "Viewer must not get the run composer");
  check(viewerErrors.length === 0, `Viewer page errors: ${viewerErrors.join(" | ")}`);
  results.viewer_role = "pass";
  await viewerContext.close();

  const signedOutContext = await browser.newContext({ viewport: { width: 1440, height: 1024 } });
  const signedOut = await signedOutContext.newPage();
  await signedOut.goto(base, { waitUntil: "domcontentloaded" });
  check(await signedOut.locator("#memory-presentation-section").count() === 0,
    "Signed-out shell must not expose the presentation entry");
  await signedOut.evaluate(() => openMemoryPresentation("memory-presentation-section"));
  check(await signedOut.locator(".memory-present").count() === 0,
    "Direct signed-out call must not enter presentation");
  await signedOut.waitForSelector("#userPanel", { state: "visible" });
  check(await signedOut.locator("#userPanel").isVisible(), "Direct signed-out call must invoke sign-in");
  results.signed_out = "pass";
  await signedOutContext.close();

  const report = {
    ok: true,
    base,
    viewport: { desktop: "1440x1024", narrow: "390x844", device_scale_factor: 1 },
    results,
    sent_experiment_turns: false,
  };
  writeFileSync("build-notes/codex/ws31-browser-check.json", `${JSON.stringify(report, null, 2)}\n`);
  process.stdout.write(`${JSON.stringify(report, null, 2)}\n`);
} finally {
  await browser.close();
}
