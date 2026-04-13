# Windows — Build, Sign, and Auto-Update

## Overview

Windows requires **code signing** to avoid SmartScreen warnings and enterprise blocks. Auto-updates on Windows use either Squirrel.Windows or NSIS differential updates via `electron-updater`.

---

## Prerequisites

- A **code signing certificate** — either:
  - **Azure Trusted Signing** (~$10/month, recommended for CI) — no hardware token needed
  - **OV certificate** from a CA (DigiCert, Sectigo, SSL.com) — ~$200-500/year, hardware token required since June 2023
  - **EV certificate** — ~$300-600/year, immediate SmartScreen trust, hardware token required

### Recommendation: Azure Trusted Signing

Best fit for Build because:
- No hardware token management — works natively in CI
- Cheap ($10/month)
- Microsoft's own service → better SmartScreen treatment
- Integrates directly with GitHub Actions via official action

### Setup Azure Trusted Signing

1. Create an Azure account (if needed)
2. Create a **Trusted Signing account** in Azure Portal
3. Create a **certificate profile** (choose "Public Trust" for code signing)
4. Set up a **managed identity** or service principal for CI access
5. Note your: endpoint URL, account name, certificate profile name

---

## Building

electron-builder targets for Windows:

```json
{
  "win": {
    "target": [
      { "target": "nsis", "arch": ["x64", "arm64"] }
    ],
    "icon": "assets/icon.ico"
  },
  "nsis": {
    "oneClick": true,
    "perMachine": false,
    "allowToChangeInstallationDirectory": false,
    "deleteAppDataOnUninstall": false
  }
}
```

**Architecture:** Build separate `x64` and `arm64` installers. Windows on ARM can run x64 binaries via emulation, but native arm64 is noticeably faster.

**Installer: NSIS vs Squirrel**

| | NSIS | Squirrel |
|---|---|---|
| Install location | Program Files (per-machine) or AppData (per-user) | AppData only (per-user) |
| Admin required | Configurable | Never |
| Delta updates | Via blockmap (electron-updater) | Built-in |
| Customization | Highly customizable | Minimal |
| Uninstaller | Add/Remove Programs entry | Add/Remove Programs entry |

**Recommendation:** NSIS — more standard, more control, and electron-updater handles differential updates via blockmaps anyway.

### Build command

```bash
npx electron-builder --win --publish never    # local build
npx electron-builder --win --publish always   # build + upload to GitHub Releases
```

**Note:** Cross-compilation from macOS/Linux to Windows works for unsigned builds but **not for signing**. Signing must happen on a Windows runner or via cloud signing.

---

## Code Signing

### Option A: Azure Trusted Signing (Recommended)

Does **not** use electron-builder's built-in signing. Instead, sign as a post-build step.

In `electron-builder` config, disable built-in signing:

```json
{
  "win": {
    "signingHashAlgorithms": [],
    "sign": null
  }
}
```

Then use the Azure Trusted Signing GitHub Action (see CI section below).

Alternatively, use a custom sign function in electron-builder:

```js
// scripts/sign-windows.js
exports.default = async function (configuration) {
  // Call Azure SignTool CLI
  // configuration.path is the file to sign
  const { execSync } = require("child_process");
  execSync(
    `AzureSignTool sign ` +
      `-kvu "${process.env.AZURE_KEY_VAULT_URL}" ` +
      `-kvt "${process.env.AZURE_TENANT_ID}" ` +
      `-kvi "${process.env.AZURE_CLIENT_ID}" ` +
      `-kvs "${process.env.AZURE_CLIENT_SECRET}" ` +
      `-kvc "${process.env.AZURE_CERT_NAME}" ` +
      `-tr http://timestamp.digicert.com ` +
      `-td sha256 ` +
      `"${configuration.path}"`,
    { stdio: "inherit" }
  );
};
```

Wire it in:

```json
{
  "win": {
    "sign": "scripts/sign-windows.js"
  }
}
```

### Option B: OV/EV Certificate with Cloud Signing (SSL.com eSigner)

```json
{
  "win": {
    "certificateSubjectName": "Your Company Name",
    "signingHashAlgorithms": ["sha256"],
    "sign": "scripts/sign-windows.js"
  }
}
```

With SSL.com eSigner:

```js
// scripts/sign-windows.js
exports.default = async function (configuration) {
  const { execSync } = require("child_process");
  execSync(
    `esigner sign ` +
      `-username "${process.env.SSL_COM_USERNAME}" ` +
      `-password "${process.env.SSL_COM_PASSWORD}" ` +
      `-totp_secret "${process.env.SSL_COM_TOTP}" ` +
      `-input "${configuration.path}"`,
    { stdio: "inherit" }
  );
};
```

---

## SmartScreen

SmartScreen evaluates reputation based on:
- Certificate type (EV gets immediate trust, OV builds over time)
- Download volume
- How long the certificate has been in use

**With Azure Trusted Signing:** Microsoft has indicated that apps signed through their service get favorable SmartScreen treatment, though they don't guarantee instant trust.

**With a new OV cert:** Expect SmartScreen warnings for the first few weeks/months until reputation builds. Users will see "Windows protected your PC" and have to click "More info" → "Run anyway".

**Mitigations:**
- Submit the app to Microsoft for analysis: https://www.microsoft.com/en-us/wdsi/filesubmission
- Encourage early adopters to click through (each successful run builds reputation)
- Consider starting with an EV cert if the warnings are unacceptable

---

## Auto-Updates

### Setup

Same `electron-updater` package as macOS:

```bash
npm install electron-updater
```

Main process code is identical to macOS — `electron-updater` handles platform differences internally.

### How It Works on Windows (NSIS)

1. App checks GitHub Releases for `latest.yml`
2. Downloads the new installer `.exe` (or uses blockmap for differential download)
3. On `quitAndInstall()`: closes the app, runs the installer silently, relaunches

### Differential Updates (Blockmaps)

electron-builder automatically generates `.blockmap` files alongside the installer. On update:
- Client downloads only the changed blocks (not the full installer)
- Significantly reduces download size for minor updates
- Falls back to full download if blockmap comparison fails

This is automatic — no extra config needed. Just make sure `--publish always` uploads the `.blockmap` files to the release.

### Publishing

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

Auto-update works without signing on Windows, but users get SmartScreen warnings on every update — effectively unusable. **Signing is required in practice.**

---

## CI/CD (GitHub Actions)

```yaml
name: Build Windows

on:
  push:
    tags: ["v*"]

jobs:
  build-win:
    runs-on: windows-latest
    steps:
      - uses: actions/checkout@v4

      - uses: actions/setup-node@v4
        with:
          node-version: 20

      - run: npm ci

      - name: Build
        env:
          GH_TOKEN: ${{ secrets.GITHUB_TOKEN }}
        run: npx electron-builder --win --publish always

      - name: Sign with Azure Trusted Signing
        uses: azure/trusted-signing-action@v0.5.0
        with:
          azure-tenant-id: ${{ secrets.AZURE_TENANT_ID }}
          azure-client-id: ${{ secrets.AZURE_CLIENT_ID }}
          azure-client-secret: ${{ secrets.AZURE_CLIENT_SECRET }}
          endpoint: ${{ secrets.AZURE_ENDPOINT }}
          trusted-signing-account-name: ${{ secrets.AZURE_ACCOUNT_NAME }}
          certificate-profile-name: ${{ secrets.AZURE_CERT_PROFILE }}
          files-folder: dist/
          files-folder-filter: exe
          file-digest: SHA256
          timestamp-rfc3161: http://timestamp.acs.microsoft.com
          timestamp-digest: SHA256
```

**Note:** The above signs post-build. For signing during build (so the installer itself is also signed), use the custom `sign` function approach described in the Code Signing section.

### Required GitHub Secrets

| Secret | Value |
|--------|-------|
| `AZURE_TENANT_ID` | Azure AD tenant ID |
| `AZURE_CLIENT_ID` | Service principal client ID |
| `AZURE_CLIENT_SECRET` | Service principal secret |
| `AZURE_ENDPOINT` | Trusted Signing endpoint URL |
| `AZURE_ACCOUNT_NAME` | Trusted Signing account name |
| `AZURE_CERT_PROFILE` | Certificate profile name |

---

## Checklist

- [ ] Choose signing approach (Azure Trusted Signing recommended)
- [ ] Set up Azure Trusted Signing account and certificate profile
- [ ] Create service principal for CI access
- [ ] Create `.ico` icon file (256x256 minimum, multi-resolution recommended)
- [ ] Configure NSIS installer options
- [ ] Add custom sign script (if using Azure or cloud HSM)
- [ ] Add electron-updater to main process
- [ ] Configure publish target (GitHub Releases)
- [ ] Add GitHub Actions secrets
- [ ] Create release workflow (Windows runner)
- [ ] Test: unsigned build triggers SmartScreen warning
- [ ] Test: signed build passes SmartScreen (may take time with OV)
- [ ] Test: auto-update downloads and installs correctly
- [ ] Test: differential update via blockmap works
