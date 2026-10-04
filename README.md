# Owchowch personal war room

Owchowch personal war room is a Torn rank-war dashboard for faction roster status, hospital-release countdowns, chain timing, and manually grouped targets. It is available as a web app, installable PWA, and Windows desktop app.

## Deploy to Render

This project includes a Render Blueprint at `render.yaml`. To deploy:

1. Commit the project files to a GitHub or GitLab repository.
2. In Render, create a new **Blueprint** and connect that repository. Render will read `render.yaml` and create the web service.
3. When the service is live, open its HTTPS URL and choose **API settings** to connect your Torn API key.

The service binds to Render's `PORT`, uses `/api/health` for its health check, and needs no third-party Python packages. The API key is entered in the browser and forwarded to this service in a POST body; the app does not write it to disk or include it in request logs. A Render deployment still processes that traffic, so deploy only to an account/server you trust. The service URL is public unless you add access controls; do not commit API keys or share the key with anyone.

## Install the Windows desktop app

The Windows desktop installer is built for Windows 10/11, 64-bit. It includes the app and a private Python runtime, so Python does not need to be installed separately. The installer creates Start Menu and desktop shortcuts. When built from this project, the installer is written to `downloads/windows-installer/Owchowch-Personal-War-Room-Setup-1.0.0.exe`.

To build it on Windows:

1. Install Node.js 22.12 or newer.
2. Open a terminal in the project folder and run `npm ci`.
3. Run `npm run package:win`. The build downloads the official Python 3.13.16 embeddable runtime and verifies its SHA-256 before packaging.
4. Run the generated setup `.exe` and follow the installer prompts.

The desktop app opens in its own window and runs the API bridge on `127.0.0.1` only. Your board remains in the Windows app's local profile; the API key stays in browser session storage. The installer is unsigned, so Windows may show an unknown-publisher warning. Only install a build you trust.

## Standalone bundle / local run

1. Download and extract `owchowch-personal-war-room-standalone.zip` from the app's **Downloads** tab.
2. Run `python3 app_server.py` from the extracted folder.
3. Open `http://localhost:8000` and choose **API settings**.
4. Enter a Torn API key with access to your faction information, wars/ranked wars, faction members, and faction chain. The opponent ID is discovered from active ranked-war API data; you don't enter it manually.

The app polls Torn once per minute. It detects the active war opponent, loads that faction's roster/status, and reads the chain for the faction attached to the API key. It uses Torn's official API v2 routes with v1 fallbacks where available. The key is held in browser tab session storage and sent in a POST body to the app server; the server doesn't write it to disk or log the request body. Run the bridge only on a computer/server you trust. The Arena live preview is hosted remotely, so use your local or trusted server for real API credentials.

## Install on a phone

Serve the app from an HTTPS address (or `localhost` on the same device):

- **Android:** Open the app URL in Chrome and tap **Install app** when offered. If no prompt appears, open Chrome's menu and select **Install app** or **Add to Home screen**.
- **iPhone/iPad:** Open the app URL in **Safari**, tap **Share**, then **Add to Home Screen**. iOS doesn't support a website-triggered install prompt.

This is an installable PWA, not a signed Play Store APK or App Store IPA. Its shell can open offline after installation; live Torn API sync still requires a reachable app server and internet connection. For actual store packages, native signing and developer accounts are required.

## Downloads and target groups

The **All war targets** tab lists the complete tracked roster with profile links, status, level, range lane, filters, and search. Choosing **Hospitalized** sorts targets by soonest release first; on the grouped board they are sorted within each manual range lane. The **Downloads** tab exports a full JSON board backup, target-roster CSV, hospital-watch CSV, and standalone app bundle. API keys are never included. Range lanes and the **In range** flag remain manual; the faction API doesn't determine your battle-stat matchups. Imported members start in the Even lane and marked in range so you can adjust them to your war intel.
