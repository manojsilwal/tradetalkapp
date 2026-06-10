# TradeTalk Android Capacitor Setup & Google Studio Guide

This guide provides the complete documentation, configuration details, and the master prompt for Google Studio (Project IDX, Gemini, or Android Studio) to build and wrap the TradeTalk React+Vite web app into an Android application using Capacitor.js.

---

## 1. TradeTalk Multi-Platform Architecture

```mermaid
graph TD
    subgraph Mobile Device (Android App)
        CapacitorWebView[Capacitor Web View] -- Runs React SPA local assets -- AppUI[React UI]
        NativePlugins[Capacitor Native Plugins] -- Biometrics / Push / Auth -- AndroidOS[Android Native OS]
    end

    subgraph Google Cloud Platform (GCP)
        FastAPIBackend[FastAPI API on Cloud Run]
        SQLiteDB[(SQLite DB / Cloud Storage)]
    end

    AppUI -- API requests with JWT -- FastAPIBackend
    AppUI -- Native Google Login ID Token -- FastAPIBackend
    FastAPIBackend -- Read/Write -- SQLiteDB
```

### Key Integration Parameters:
- **Production API URL**: `https://tradetalk-api-933081724691.us-central1.run.app`
- **Frontend App Host**: Vite (default dev on `http://localhost:5173`)
- **Capacitor Host (Android)**: `http://localhost` (serves compiled web assets locally inside WebView)
- **Authentication**: JWT token stored in `localStorage` as `k2_token`, verified on FastAPI via `Authorization: Bearer <token>`
- **Google Sign-In**: Authenticates using Google OAuth ID tokens via the `/auth/google` POST request.

---

## 2. Master Google Studio Prompt

Copy and paste the prompt below into Google AI Studio, Gemini, or Android Studio's Gemini bot to generate the exact integration files and boilerplate needed for your workspace.

````markdown
You are an expert Android developer. I need to build a high-performance Android app wrapper for my existing React+Vite web application using Capacitor.js and Google Studio. Here are all the details of my setup:

### Codebase Details
1. **Frontend App**: Built with React (v19) and Vite (v7). The app builds assets to `/dist` and communicates with the API via `/src/api.js`.
2. **API Backend**: FastAPI (Python) running on GCP Cloud Run. Production API URL is `https://tradetalk-api-933081724691.us-central1.run.app`.
3. **Authentication**: 
   - Uses JWT tokens stored in localStorage (`k2_token`).
   - Uses Google OAuth via `@react-oauth/google` on the web, exchanging Google ID tokens at `/auth/google` for a JWT.
   - Contains fallback manual credential sign-in and signup (`/auth/login-manual` and `/auth/signup`).
4. **CORS Configuration**: The FastAPI backend allows `http://localhost` (with optional ports) via origin regex matching `allow_origin_regex=r"https://.*\.vercel\.app|http://(localhost|127\.0\.0\.1)(:\d+)?"`.

### My Goal
I want to use Capacitor.js to package the React UI and compile it to Android, ensuring that:
1. API requests use the production API URL instead of relative paths or dev defaults.
2. Google Sign-In is configured natively using the `@capacitor-community/google-signin` plugin to avoid "disallowed_useragent" errors when running inside a mobile WebView.
3. The app is set up correctly in Android Studio, utilizing Google Play Services and OAuth credentials.

### Please Provide:
1. **Capacitor Init Config**: The exact `capacitor.config.json` that sets `webDir` to `dist`, enables hardware acceleration, and configures the native Google Sign-in plugin.
2. **Environment Variable Injection**: Script additions for `package.json` to automatically compile the Vite build with `VITE_API_BASE_URL` set to the GCP Cloud Run production URL during the Capacitor sync phase.
3. **Native Google Sign-In Bridge**: A React component/hook implementation that integrates `@capacitor-community/google-signin` with our existing `AuthContext.jsx` (which expects a Google ID token passed to `/auth/google`).
4. **GCP Console Setup Guide**: Instructions on creating the Android OAuth Client ID in Google Cloud Platform Console (including how to extract SHA-1 keystore fingerprints for debug and production releases).
5. **Android Native Manifest Adjustments**: The `AndroidManifest.xml` updates and Gradle config changes required to support cleartext traffic (for testing against a local backend on `http://10.0.2.2:8000`) and declare the Google Client ID metadata.
````

---

## 3. Step-by-Step Implementation Guide

Follow these terminal commands to initialize the wrapper on your workspace.

### Step 3.1: Install Capacitor in the Frontend Folder
Run these commands in your project root workspace:

```bash
# Navigate to the frontend directory
cd frontend

# Install Capacitor Core, CLI, and the Android Platform wrapper
npm install @capacitor/core @capacitor/cli @capacitor/android

# Install the native Google Sign-In helper plugin (essential for Android OAuth)
npm install @capacitor-community/google-signin
```

### Step 3.2: Initialize Capacitor Config
Initialize the Capacitor project inside the `frontend` folder:

```bash
npx cap init TradeTalk com.manojsilwal.tradetalkapp --web-dir=dist
```

This will create a `capacitor.config.json` file. Update it to include the Google Sign-in plugin client IDs:

```json
{
  "appId": "com.manojsilwal.tradetalkapp",
  "appName": "TradeTalk",
  "webDir": "dist",
  "bundledWebRuntime": false,
  "plugins": {
    "GoogleAuth": {
      "providers": ["google"],
      "clientId": "YOUR_WEB_CLIENT_ID.apps.googleusercontent.com",
      "forceCodeForRefreshToken": true
    }
  }
}
```

### Step 3.3: Add Android Platform & Sync
Add the native Android project directory structure:

```bash
# Add the android folder
npx cap add android
```

Whenever you make frontend changes in React/Vite:
```bash
# 1. Compile the React assets (injecting production API URL)
VITE_API_BASE_URL=https://tradetalk-api-933081724691.us-central1.run.app npm run build

# 2. Sync the compiled dist/ assets into the Android native build directory
npx cap sync
```

### Step 3.4: Open in Android Studio
Use the Capacitor CLI to launch Android Studio with the newly created native project:

```bash
npx cap open android
```

---

## 4. GCP & Google OAuth Console Setup

To enable Google Sign-in on Android, you must correlate the Android app signature with your GCP project.

1. **Get your SHA-1 Fingerprint**:
   Run this command in the `frontend/android` folder to retrieve your debug signing key fingerprint:
   ```bash
   ./gradlew signingReport
   ```
   Look for the `SHA1` fingerprint under the `debug` variant configuration.

2. **Register in GCP Credentials Console**:
   - Go to [Google Cloud Console Credentials](https://console.cloud.google.com/apis/credentials).
   - Click **Create Credentials** -> **OAuth client ID**.
   - Select **Application type**: `Android`.
   - Set **Package name**: `com.manojsilwal.tradetalkapp`.
   - Paste the **SHA-1 certificate fingerprint** you copied in Step 1.
   - Click **Create**.

3. **Link Web & Android Clients**:
   - The API uses your Web Client ID (`GOOGLE_CLIENT_ID` environment variable on Cloud Run) to verify incoming ID tokens.
   - Ensure the Android client and Web client are created under the **same GCP project**. Google will automatically link the audience checks.

---

## 5. Integrating Google Sign-In in React

Replace the standard web `@react-oauth/google` popup with a hybrid client-check inside `AuthContext.jsx`:

```javascript
import { Capacitor } from '@capacitor/core';
import { GoogleAuth } from '@capacitor-community/google-signin';

// Inside your login flow:
const handleGoogleLogin = async () => {
  if (Capacitor.isNativePlatform()) {
    // Native Mobile Login flow
    const user = await GoogleAuth.signIn();
    const idToken = user.authentication.idToken;
    await login(idToken); // send idToken to FastAPI /auth/google
  } else {
    // Standard web login flow
  }
};
```
This guarantees a smooth user experience across both web and native mobile environments.
