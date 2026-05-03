const fs = require('fs');
let content = fs.readFileSync('tests/e2e/parity.spec.ts', 'utf8');
content = content.replace(
  "test.beforeEach(async ({ page }) => {",
  "// Ensure the test does not time out waiting for locators if a tour blocks it.\ntest.beforeEach(async ({ page }) => {"
);
fs.writeFileSync('tests/e2e/parity.spec.ts', content);
