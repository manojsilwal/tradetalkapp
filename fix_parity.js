const fs = require('fs');

let content = fs.readFileSync('tests/e2e/parity.spec.ts', 'utf8');

// VIX target parent node needs to go up 2 levels or find the adjacent number div.
// Our debug log showed just "CBOE ^VIX Volatility" instead of the numbers, meaning we didn't go up high enough.

content = content.replace(
  "const vixCard = page.locator('text=CBOE ^VIX Volatility').locator('..');",
  "const vixCard = page.locator('text=CBOE ^VIX Volatility').locator('..').locator('..');"
);

content = content.replace(
  "const xlkCard = page.locator('text=XLK').locator('..');",
  "const xlkCard = page.locator('text=XLK').locator('..').locator('..');"
);

fs.writeFileSync('tests/e2e/parity.spec.ts', content);
