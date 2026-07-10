# Agent Notes

## Review Page DOM Verification

When checking whether the GitHub Pages review UI actually renders a change, prefer a real browser DOM check over only inspecting JSON or JavaScript text.

Use a temporary Playwright install so the project dependencies stay unchanged:

```bash
tmpdir=$(mktemp -d)
cd "$tmpdir"
npm init -y >/dev/null 2>&1
npm install -q playwright
npx playwright install chromium
```

Serve the generated site from `docs/` and inspect it with Chromium:

```bash
cd /panfs/panmt22/users/hmanabe/yomi-corpus/docs
python -m http.server 8765 --bind 127.0.0.1 >/tmp/yomi-dom-http.log 2>&1 &
server=$!
```

Then run a small Playwright script against `http://127.0.0.1:8765/review/?mode=unified&v=<cache-buster>` and query the DOM, for example:

```javascript
import { chromium } from "playwright";

const browser = await chromium.launch({ headless: true });
const page = await browser.newPage();
await page.goto("http://127.0.0.1:8765/review/?mode=unified&v=dom-check", {
  waitUntil: "domcontentloaded",
  timeout: 60000,
});
await page.waitForFunction(
  () => document.querySelectorAll(".workflow-doc-tile, .workflow-resolved-row, .task-doc-title").length > 0,
  null,
  { timeout: 60000 },
);
console.log(await page.evaluate(() => ({
  tileTexts: [...document.querySelectorAll(".workflow-doc-tile strong")].map((e) => e.textContent.trim()),
  resolvedTexts: [...document.querySelectorAll(".workflow-resolved-row strong")].map((e) => e.textContent.trim()),
  packTitle: document.querySelector("#pack-title")?.textContent.trim(),
})));
await browser.close();
```

Cleanup:

```bash
kill "$server"
rm -rf "$tmpdir"
```

Notes:

- `jsdom` is not sufficient for this page because the review app is loaded as an ES module and did not execute reliably in `jsdom`.
- System Firefox/geckodriver was unreliable on the cluster during testing; a temporary Playwright Chromium install worked.
- Remote GitHub Pages checks can time out from the cluster. A local `docs/` server exercises the same generated files that `publish-review` publishes.
- `publish-review --dry-run` regenerates `docs/review/*` in the main worktree. Revert generated side effects before committing unless the commit is intentionally about tracked generated artifacts.
