export default async function globalSetup() {
  const APP_URL = process.env.APP_URL ?? 'https://frontend-manojsilwals-projects.vercel.app';
  const API_URL = process.env.API_URL ?? 'http://localhost:8000';

  const appResponse = await fetch(APP_URL);
  if (!appResponse.ok) throw new Error(`APP_URL not reachable: ${appResponse.status}`);

  const healthResponse = await fetch(`${API_URL}/health`).catch(() => null);
  if (!healthResponse || !healthResponse.ok) {
    console.warn('WARNING: API health endpoint not reachable. API-level tests may fail.');
  }

  console.log('✅ Global setup complete');
}
