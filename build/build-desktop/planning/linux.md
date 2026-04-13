# Linux — Build, Sign, and Auto-Update

## Overview

Linux has no centralized code signing or gatekeeper mechanism like macOS/Windows. Distribution is fragmented across package formats, and auto-updates depend on which format you ship.

---

## Package Formats

| Format | Description | Auto-update | Signing |
|--------|-------------|-------------|---------|
| **AppImage** | Single portable file, runs anywhere | Built-in (electron-updater) | Optional GPG |
| **deb** | Debian/Ubuntu package | Via APT repo | GPG-signed repo |
| **rpm** | Fedora/RHEL package | Via YUM/DNF repo | GPG-signed repo |
| **snap** | Canonical's sandboxed format | Snap Store handles it | Snap Store handles it |
| **flatpak** | Freedesktop sandboxed format | Flathub handles it | Flathub handles it |

### Recommendation: AppImage + deb

- **AppImage** — widest compatibility, built-in auto-update via electron-updater, no installation needed
- **deb** — covers Ubuntu/Debian (largest desktop Linux share), professional feel with APT repo

Add `rpm` later if there's demand from Fedora/RHEL users. Skip snap/flatpak initially — they add complexity and the sandboxing can conflict with Build's device client integration.

---

## Building

electron-builder config:

```json
{
  "linux": {
    "target": [
      { "target": "AppImage", "arch": ["x64", "arm64"] },
      { "target": "deb", "arch": ["x64", "arm64"] }
    ],
    "category": "Development",
    "icon": "assets/icons"
  }
}
```

**Icons:** Linux expects icons in multiple sizes. Place PNG files in `assets/icons/`:
```
assets/icons/
  16x16.png
  32x32.png
  48x48.png
  64x64.png
  128x128.png
  256x256.png
  512x512.png
```

electron-builder picks them up automatically by size.

### deb-specific config

```json
{
  "deb": {
    "depends": ["libgtk-3-0", "libnotify4", "libnss3", "libxss1", "libxtst6", "xdg-utils", "libatspi2.0-0", "libuuid1", "libsecret-1-0"],
    "afterInstall": "scripts/after-install.sh",
    "afterRemove": "scripts/after-remove.sh"
  }
}
```

### Build commands

```bash
npx electron-builder --linux --publish never    # local build
npx electron-builder --linux --publish always   # build + upload to GitHub Releases
```

**Cross-compilation:** Building Linux targets on macOS works for AppImage but may fail for deb. Best to build on a Linux runner in CI.

---

## Signing

### AppImage — GPG Signing (Optional)

AppImages can be GPG-signed, but there's no OS-level enforcement. It's primarily for users who manually verify.

```bash
# Sign the AppImage
gpg --detach-sign --armor Build.AppImage

# Produces Build.AppImage.asc
# Users verify with: gpg --verify Build.AppImage.asc Build.AppImage
```

For electron-updater, signing isn't required — updates are verified by checking the `latest-linux.yml` manifest hash.

### deb — APT Repository Signing

If you distribute via an APT repo (recommended for deb), the repo must be GPG-signed.

**Generate a GPG key for the repo:**

```bash
gpg --full-generate-key
# Choose RSA, 4096 bits, no expiration (or long expiration)
# Use a dedicated email like build-releases@getbuild.ing

# Export public key
gpg --armor --export build-releases@getbuild.ing > build-desktop.gpg.key

# Export private key (for CI)
gpg --armor --export-secret-keys build-releases@getbuild.ing | base64
```

**APT repo structure** (hosted on GitHub Pages, S3, or your server):

```
repo/
  dists/
    stable/
      Release         # repo metadata, signed
      Release.gpg     # detached signature
      InRelease       # clearsigned Release (preferred)
      main/
        binary-amd64/
          Packages    # package index
          Packages.gz
        binary-arm64/
          Packages
          Packages.gz
  pool/
    main/
      build-desktop_1.0.0_amd64.deb
      build-desktop_1.0.0_arm64.deb
```

**User installation instructions:**

```bash
# Add the GPG key
curl -fsSL https://repo.getbuild.ing/build-desktop.gpg.key | sudo gpg --dearmor -o /usr/share/keyrings/build-desktop.gpg

# Add the repository
echo "deb [signed-by=/usr/share/keyrings/build-desktop.gpg] https://repo.getbuild.ing/apt stable main" | sudo tee /etc/apt/sources.list.d/build-desktop.list

# Install
sudo apt update && sudo apt install build-desktop
```

### Practical Take

GPG signing for AppImage is nice-to-have. APT repo signing is necessary if you set up a deb repo, but you can defer the APT repo entirely and just publish `.deb` files on GitHub Releases for manual download. Start simple.

---

## Auto-Updates

### AppImage — electron-updater (Recommended)

Works identically to macOS/Windows:

```js
const { autoUpdater } = require("electron-updater");

app.whenReady().then(() => {
  autoUpdater.checkForUpdatesAndNotify();
});
```

**How it works:**
1. Checks GitHub Releases for `latest-linux.yml`
2. Downloads the new `.AppImage`
3. Replaces the current AppImage file
4. Relaunches the app

**Important:** The user must have the AppImage in a writable location. If it's in a read-only path, the update will fail. Document this.

### deb — APT Repository

If users install via APT:

```bash
sudo apt update && sudo apt upgrade build-desktop
```

This is manual (or handled by the user's unattended-upgrades config). The app itself doesn't manage deb updates.

**Hybrid approach:** You can still use electron-updater in deb-installed apps to notify users of updates, even if you can't auto-install (it would need root). Show a notification: "A new version is available. Run `sudo apt update && sudo apt upgrade build-desktop` to update."

### Differential Updates

electron-updater supports blockmap-based differential updates for AppImage, same as Windows NSIS. The `.blockmap` file is generated automatically.

---

## CI/CD (GitHub Actions)

```yaml
name: Build Linux

on:
  push:
    tags: ["v*"]

jobs:
  build-linux:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4

      - uses: actions/setup-node@v4
        with:
          node-version: 20

      - run: npm ci

      - name: Build and publish
        env:
          GH_TOKEN: ${{ secrets.GITHUB_TOKEN }}
        run: npx electron-builder --linux --publish always
```

**arm64 builds:** GitHub Actions doesn't have native arm64 Linux runners (yet). Options:
- Use QEMU emulation (slow but works):
  ```yaml
  - uses: docker/setup-qemu-action@v3
    with:
      platforms: arm64
  ```
- Cross-compile from x64 (works for most Electron apps)
- Use a self-hosted arm64 runner

For now, cross-compiling x64 → arm64 with electron-builder usually works:

```bash
npx electron-builder --linux --arm64 --publish always
```

### Optional: Update APT Repository

If you set up an APT repo, add a step to update it after building:

```yaml
      - name: Update APT repo
        run: |
          # Import GPG key
          echo "${{ secrets.GPG_PRIVATE_KEY }}" | base64 -d | gpg --import

          # Copy deb to repo pool
          cp dist/*.deb repo/pool/main/

          # Regenerate package index
          cd repo
          dpkg-scanpackages pool/main /dev/null | gzip > dists/stable/main/binary-amd64/Packages.gz

          # Sign the Release file
          apt-ftparchive release dists/stable > dists/stable/Release
          gpg --default-key build-releases@getbuild.ing -abs -o dists/stable/Release.gpg dists/stable/Release
          gpg --default-key build-releases@getbuild.ing --clearsign -o dists/stable/InRelease dists/stable/Release
```

This is a simplified example — in practice, use a tool like `reprepro` or `aptly` to manage the repo.

---

## Checklist

- [ ] Create multi-resolution icon PNGs
- [ ] Configure AppImage and deb targets
- [ ] Add electron-updater to main process (shared with macOS/Windows)
- [ ] Configure publish target (GitHub Releases)
- [ ] Create release workflow (Ubuntu runner)
- [ ] Test: AppImage runs on Ubuntu, Fedora, Arch
- [ ] Test: deb installs cleanly on Ubuntu/Debian
- [ ] Test: AppImage auto-update works
- [ ] Decide on APT repo (defer or set up)
- [ ] If APT repo: generate GPG key, set up hosting, write user install instructions
- [ ] Test: arm64 builds work (cross-compile or QEMU)
