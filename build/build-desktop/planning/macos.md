# macOS — Build, Sign, and Auto-Update

## Overview

macOS requires **code signing** and **notarization** for apps to run without Gatekeeper warnings. Auto-updates on macOS use Squirrel.Mac under the hood via `electron-updater`.

---

## Prerequisites

- **Apple Developer Program** membership ($99/year) — https://developer.apple.com/programs/
- **Developer ID Application** certificate (for distribution outside the Mac App Store)
- **Apple ID app-specific password** for notarization (generated at appleid.apple.com)

### Generating the Certificate

1. Open Xcode → Settings → Accounts → Manage Certificates
2. Click `+` → "Developer ID Application"
3. Export the certificate as a `.p12` file (you'll need this for CI)
4. Base64-encode it for use as a GitHub Actions secret:
   ```bash
   base64 -i certificate.p12 | pbcopy
   ```

---

## Building

electron-builder targets for macOS:

```json
{
  "mac": {
    "category": "public.app-category.developer-tools",
    "target": [
      { "target": "dmg", "arch": ["universal"] },
      { "target": "zip", "arch": ["universal"] }
    ],
    "icon": "assets/icon.icns"
  },
  "dmg": {
    "sign": false
  }
}
```

**Architecture:** Build as `universal` binary (both Intel and Apple Silicon in one binary). This simplifies distribution — one download for all Macs.

**Targets:**
- `dmg` — for manual download/install from a website
- `zip` — required by `electron-updater` for auto-updates (it downloads the zip, not the dmg)

### Build command

```bash
npx electron-builder --mac --publish never   # local build, no upload
npx electron-builder --mac --publish always   # build + upload to GitHub Releases
```

---

## Code Signing

### Configuration

Add to `package.json` build config or `electron-builder.yml`:

```json
{
  "mac": {
    "hardenedRuntime": true,
    "gatekeeperAssess": false,
    "entitlements": "entitlements.mac.plist",
    "entitlementsInherit": "entitlements.mac.plist"
  }
}
```

### Entitlements

Create `entitlements.mac.plist`:

```xml
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>com.apple.security.cs.allow-jit</key>
    <true/>
    <key>com.apple.security.cs.allow-unsigned-executable-memory</key>
    <true/>
    <key>com.apple.security.cs.allow-dyld-environment-variables</key>
    <true/>
</dict>
</plist>
```

These entitlements are required for Electron's V8 JavaScript engine to function with hardened runtime enabled.

### Environment Variables for CI

| Variable | Description |
|----------|-------------|
| `CSC_LINK` | Base64-encoded `.p12` certificate |
| `CSC_KEY_PASSWORD` | Password for the `.p12` file |

electron-builder picks these up automatically and signs the app.

### Local Signing

If the certificate is installed in your Keychain, electron-builder finds it automatically — no env vars needed. It uses the first "Developer ID Application" identity it finds.

---

## Notarization

Required since macOS 10.15 Catalina. Without notarization, Gatekeeper shows a hard block (not just a warning).

### Setup

Install the notarization package:

```bash
npm install --save-dev @electron/notarize
```

Create `scripts/notarize.js`:

```js
const { notarize } = require("@electron/notarize");

exports.default = async function notarizing(context) {
  const { electronPlatformName, appOutDir } = context;
  if (electronPlatformName !== "darwin") return;

  const appName = context.packager.appInfo.productFilename;

  await notarize({
    appBundleId: "ing.getbuild.app",
    appPath: `${appOutDir}/${appName}.app`,
    appleId: process.env.APPLE_ID,
    appleIdPassword: process.env.APPLE_APP_PASSWORD,
    teamId: process.env.APPLE_TEAM_ID,
  });
};
```

Wire it into electron-builder:

```json
{
  "afterSign": "scripts/notarize.js"
}
```

### Environment Variables for CI

| Variable | Description |
|----------|-------------|
| `APPLE_ID` | Your Apple ID email |
| `APPLE_APP_PASSWORD` | App-specific password (not your Apple ID password) |
| `APPLE_TEAM_ID` | Your 10-character Apple Developer Team ID |

### Notarization Timing

- Takes 1-5 minutes per submission
- The `@electron/notarize` package waits for completion and staples the ticket automatically
- If it times out, you can check status manually:
  ```bash
  xcrun notarytool history --apple-id you@email.com --team-id TEAMID --password app-password
  ```

---

## Auto-Updates

### Setup

Install electron-updater:

```bash
npm install electron-updater
```

In the main process:

```js
const { autoUpdater } = require("electron-updater");
const log = require("electron-log");

autoUpdater.logger = log;

app.whenReady().then(() => {
  // Check on launch
  autoUpdater.checkForUpdatesAndNotify();

  // Check periodically (every 4 hours)
  setInterval(() => {
    autoUpdater.checkForUpdatesAndNotify();
  }, 4 * 60 * 60 * 1000);
});

autoUpdater.on("update-downloaded", (info) => {
  // Optionally prompt user or auto-install on quit
  // autoUpdater.quitAndInstall();
});
```

### How It Works on macOS

1. App checks GitHub Releases for `latest-mac.yml`
2. Compares version in the manifest to the running app version
3. Downloads the `.zip` asset in the background
4. Extracts and replaces the `.app` bundle
5. On next launch (or `quitAndInstall()`), the new version runs

### Publishing

electron-builder generates `latest-mac.yml` automatically when publishing:

```bash
npx electron-builder --mac --publish always
```

Or configure the publish target:

```json
{
  "publish": {
    "provider": "github",
    "owner": "ZechCodes",
    "repo": "build-desktop"
  }
}
```

### Signing Requirement

Auto-update **will not work** on macOS without code signing. Squirrel.Mac validates the signature of the update before applying it.

---

## CI/CD (GitHub Actions)

```yaml
name: Build macOS

on:
  push:
    tags: ["v*"]

jobs:
  build-mac:
    runs-on: macos-latest
    steps:
      - uses: actions/checkout@v4

      - uses: actions/setup-node@v4
        with:
          node-version: 20

      - run: npm ci

      - name: Build and publish
        env:
          GH_TOKEN: ${{ secrets.GITHUB_TOKEN }}
          CSC_LINK: ${{ secrets.MAC_CSC_LINK }}
          CSC_KEY_PASSWORD: ${{ secrets.MAC_CSC_KEY_PASSWORD }}
          APPLE_ID: ${{ secrets.APPLE_ID }}
          APPLE_APP_PASSWORD: ${{ secrets.APPLE_APP_PASSWORD }}
          APPLE_TEAM_ID: ${{ secrets.APPLE_TEAM_ID }}
        run: npx electron-builder --mac --publish always
```

### Required GitHub Secrets

| Secret | Value |
|--------|-------|
| `MAC_CSC_LINK` | Base64-encoded `.p12` certificate |
| `MAC_CSC_KEY_PASSWORD` | Certificate password |
| `APPLE_ID` | Apple ID email |
| `APPLE_APP_PASSWORD` | App-specific password |
| `APPLE_TEAM_ID` | Team ID |

---

## Checklist

- [ ] Enroll in Apple Developer Program
- [ ] Generate Developer ID Application certificate
- [ ] Export certificate as `.p12` and base64-encode for CI
- [ ] Create app-specific password at appleid.apple.com
- [ ] Add entitlements plist
- [ ] Add notarize afterSign script
- [ ] Add electron-updater to main process
- [ ] Configure publish target (GitHub Releases)
- [ ] Add GitHub Actions secrets
- [ ] Create release workflow
- [ ] Test: unsigned build is blocked by Gatekeeper
- [ ] Test: signed + notarized build opens cleanly
- [ ] Test: auto-update downloads and applies correctly
