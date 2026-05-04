export default async function globalSetup() {
  const APP_URL = process.env.APP_URL ?? 'https://frontend-manojsilwals-projects.vercel.app';
  const API_URL = process.env.API_URL ?? (APP_URL.includes('vercel.app') ? 'https://tradetalkapp.onrender.com' : 'http://localhost:8000');

  const appResponse = await fetch(APP_URL);
  if (!appResponse.ok) throw new Error(`APP_URL not reachable: ${appResponse.status}`);



  console.log('✅ Global setup complete');
}
