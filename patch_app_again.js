const fs = require('fs');
let content = fs.readFileSync('frontend/src/App.jsx', 'utf8');

// Replace ConsumerUI with UnifiedDashboardUI
content = content.replace(
  /const ConsumerUI = React.lazy\(\(\) => import\('\.\/ConsumerUI'\)\)/,
  "const ConsumerUI = React.lazy(() => import('./UnifiedDashboardUI'))"
);

// Replace standard "Valuation Dashboard" sidebar item with "Dashboard"
content = content.replace(
  /<span>Valuation Dashboard<\/span>/,
  '<span>Dashboard</span>'
);

// Remove the Decision Terminal nav button
const regex = /<button\s+className={`nav-btn \${activeTab === 'decision_terminal' \? 'active' : ''}`}[\s\S]*?<\/button>/;
content = content.replace(regex, '');

fs.writeFileSync('frontend/src/App.jsx', content);
