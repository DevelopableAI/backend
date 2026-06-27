#!/usr/bin/env node
const fs = require('node:fs');
const path = require('node:path');

const repoRoot = process.cwd();
const gitDir = path.join(repoRoot, '.git');
const hookSourceDir = path.join(repoRoot, '.developable', 'hooks');
const hookTargetDir = path.join(gitDir, 'hooks');

if (!fs.existsSync(gitDir) || !fs.existsSync(hookSourceDir)) {
  process.exit(0);
}

fs.mkdirSync(hookTargetDir, { recursive: true });

for (const hookName of ['pre-commit', 'pre-push']) {
  const source = path.join(hookSourceDir, hookName);
  const target = path.join(hookTargetDir, hookName);
  if (!fs.existsSync(source)) {
    continue;
  }
  fs.copyFileSync(source, target);
  fs.chmodSync(target, 0o755);
}

console.log('Developable hooks installed.');
