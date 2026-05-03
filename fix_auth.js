const fs = require('fs');

let content = fs.readFileSync('frontend/src/AuthContext.jsx', 'utf8');

content = content.replace(
  "console.error('Auto dev login failed', e);",
  `if (!e.message?.includes('Failed to fetch')) {
                        console.error('Auto dev login failed', e);
                    }`
);

fs.writeFileSync('frontend/src/AuthContext.jsx', content);
