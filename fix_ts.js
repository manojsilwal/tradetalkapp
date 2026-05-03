const fs = require('fs');

let content = fs.readFileSync('tests/e2e/full_suite.spec.ts', 'utf8');
content = content.replace(/if \('.*?' !== '\/'\) \{/g, "if (true) {");
content = content.replace(/if \('\/' !== '\/'\) \{/g, "if (false) {"); // handled separately actually, dashboard route handles this. let's just do a simpler fix
fs.writeFileSync('tests/e2e/full_suite.spec.ts', content);
