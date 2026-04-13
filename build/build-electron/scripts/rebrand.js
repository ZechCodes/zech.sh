#!/usr/bin/env node
// Rebrand Electron.app → Build.app for macOS dev mode
// Runs as postinstall so the dock/app-switcher shows "Build" instead of "Electron"

const fs = require('fs');
const path = require('path');
const { execSync } = require('child_process');

const dist = path.join(__dirname, '..', 'node_modules', 'electron', 'dist');
const oldApp = path.join(dist, 'Electron.app');
const newApp = path.join(dist, 'Build.app');
const pathFile = path.join(__dirname, '..', 'node_modules', 'electron', 'path.txt');

if (process.platform !== 'darwin') {
  console.log('rebrand: skipping (not macOS)');
  process.exit(0);
}

if (!fs.existsSync(oldApp) && !fs.existsSync(newApp)) {
  console.log('rebrand: no Electron.app found, skipping');
  process.exit(0);
}

// Rename .app bundle
if (fs.existsSync(oldApp)) {
  fs.renameSync(oldApp, newApp);
  console.log('rebrand: renamed Electron.app → Build.app');
}

// Update path.txt so the electron module can find the binary
fs.writeFileSync(pathFile, 'Build.app/Contents/MacOS/Electron');
console.log('rebrand: updated path.txt');

// Patch Info.plist
const plist = path.join(newApp, 'Contents', 'Info.plist');
if (fs.existsSync(plist)) {
  try {
    execSync(`/usr/libexec/PlistBuddy -c "Set :CFBundleName Build" "${plist}"`, { stdio: 'pipe' });
  } catch {
    execSync(`/usr/libexec/PlistBuddy -c "Add :CFBundleName string Build" "${plist}"`, { stdio: 'pipe' });
  }
  try {
    execSync(`/usr/libexec/PlistBuddy -c "Set :CFBundleDisplayName Build" "${plist}"`, { stdio: 'pipe' });
  } catch {
    execSync(`/usr/libexec/PlistBuddy -c "Add :CFBundleDisplayName string Build" "${plist}"`, { stdio: 'pipe' });
  }
  console.log('rebrand: patched Info.plist');
}

// Create en.lproj/InfoPlist.strings
const enLproj = path.join(newApp, 'Contents', 'Resources', 'en.lproj');
fs.mkdirSync(enLproj, { recursive: true });
fs.writeFileSync(
  path.join(enLproj, 'InfoPlist.strings'),
  'CFBundleDisplayName = "Build";\nCFBundleName = "Build";\n'
);
console.log('rebrand: created InfoPlist.strings');

console.log('rebrand: done — dock/app-switcher will show "Build"');
