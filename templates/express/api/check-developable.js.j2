#!/usr/bin/env node
const fs = require('node:fs');
const path = require('node:path');
const ts = require('typescript');

const repoRoot = process.cwd();
const developableDir = path.join(repoRoot, '.developable');

function loadJson(relativePath) {
  const fullPath = path.join(developableDir, relativePath);
  return JSON.parse(fs.readFileSync(fullPath, 'utf8'));
}

function fail(message) {
  console.error(`Developable conformance failed: ${message}`);
}

function collectImports(sourceFile) {
  const imports = [];
  ts.forEachChild(sourceFile, (node) => {
    if (ts.isImportDeclaration(node) && ts.isStringLiteral(node.moduleSpecifier)) {
      imports.push(node.moduleSpecifier.text);
    }
  });
  return imports;
}

function collectClassNames(sourceFile) {
  const classNames = [];
  ts.forEachChild(sourceFile, (node) => {
    if (ts.isClassDeclaration(node) && node.name) {
      classNames.push(node.name.text);
    }
  });
  return classNames;
}

function hasForbiddenNewExpression(sourceFile, suffixes) {
  let found = false;
  function visit(node) {
    if (ts.isNewExpression(node) && node.expression && suffixes.length > 0) {
      const text = node.expression.getText(sourceFile);
      if (suffixes.some((suffix) => text.endsWith(suffix))) {
        found = true;
        return;
      }
    }
    ts.forEachChild(node, visit);
  }
  visit(sourceFile);
  return found;
}

function layerFor(relativePath) {
  if (relativePath.startsWith('src/routes/')) return 'routes';
  if (relativePath.startsWith('src/controllers/')) return 'controllers';
  if (relativePath.startsWith('src/services/')) return 'services';
  if (relativePath.startsWith('src/repositories/')) return 'repositories';
  if (relativePath.startsWith('src/adapters/')) return 'adapters';
  if (relativePath.startsWith('src/bootstrap/')) return 'bootstrap';
  return 'other';
}

function main() {
  const contractPath = path.join(developableDir, 'contract.json');
  if (!fs.existsSync(contractPath)) {
    console.log('Developable contract not present; skipping conformance check.');
    return;
  }

  const managedFiles = loadJson('manifests/managed-files.json').paths || [];
  const dependencyRules = loadJson('manifests/dependency-rules.json');
  const signatures = loadJson('manifests/ast-signatures.json');
  const errors = [];

  for (const relativePath of managedFiles) {
    const absolutePath = path.join(repoRoot, relativePath);
    if (!fs.existsSync(absolutePath)) {
      errors.push(`Managed file missing: ${relativePath}`);
      continue;
    }

    const sourceText = fs.readFileSync(absolutePath, 'utf8');
    const sourceFile = ts.createSourceFile(absolutePath, sourceText, ts.ScriptTarget.Latest, true, ts.ScriptKind.TS);
    const imports = collectImports(sourceFile);
    const layer = layerFor(relativePath);

    for (const fragment of dependencyRules.forbidden_imports_by_layer?.[layer] || []) {
      if (imports.some((entry) => entry.includes(fragment)) || sourceText.includes(fragment)) {
        errors.push(`${relativePath} imports forbidden dependency fragment '${fragment}'`);
      }
    }

    for (const fragment of dependencyRules.required_imports_by_layer?.[layer] || []) {
      if (!imports.some((entry) => entry.includes(fragment)) && !sourceText.includes(fragment)) {
        errors.push(`${relativePath} is missing required dependency fragment '${fragment}'`);
      }
    }

    if (hasForbiddenNewExpression(sourceFile, dependencyRules.forbidden_new_by_layer?.[layer] || [])) {
      errors.push(`${relativePath} constructs forbidden concrete dependencies`);
    }

    if (layer === 'controllers' && !imports.some((entry) => entry.includes('../contracts/'))) {
      errors.push(`${relativePath} must depend on service contracts`);
    }
  }

  for (const [relativePath, className] of Object.entries(signatures.required_classes || {})) {
    const absolutePath = path.join(repoRoot, relativePath);
    if (!fs.existsSync(absolutePath)) {
      errors.push(`Required class file missing: ${relativePath}`);
      continue;
    }
    const sourceText = fs.readFileSync(absolutePath, 'utf8');
    const sourceFile = ts.createSourceFile(absolutePath, sourceText, ts.ScriptTarget.Latest, true, ts.ScriptKind.TS);
    if (!collectClassNames(sourceFile).includes(className)) {
      errors.push(`${relativePath} must define class ${className}`);
    }
  }

  if (errors.length > 0) {
    for (const error of errors) {
      fail(error);
    }
    process.exit(1);
  }

  console.log('Developable conformance passed.');
}

main();
