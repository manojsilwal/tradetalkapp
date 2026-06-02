export default async function globalSetup() {
  const APP_URL = process.env.APP_URL ?? 'https://frontend-manojsilwals-projects.vercel.app';
  const API_URL = process.env.API_URL ?? process.env.TRADETALK_API_BASE ?? 'http://localhost:8000';

  const appResponse = await fetch(APP_URL);
  if (!appResponse.ok) throw new Error(`APP_URL not reachable: ${appResponse.status}`);



  console.log('✅ Global setup complete');
}
