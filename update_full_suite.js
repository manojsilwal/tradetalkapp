const fs = require('fs');

let fullSuiteTs = fs.readFileSync('tests/e2e/full_suite.spec.ts', 'utf8');

// Ensure that assertNoErrors actually uses expect
fullSuiteTs = fullSuiteTs.replace(
  '// expect(errors).toEqual([]);',
  'expect(errors).toEqual([]);'
);

// We need to whitelist a few more potential noisy errors based on standard react apps
// that might fail randomly during fast execution. But the spec says "assert no errors"
// so we will keep it strict.
fs.writeFileSync('tests/e2e/full_suite.spec.ts', fullSuiteTs);
console.log("Updated assertNoErrors");
