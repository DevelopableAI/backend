from pathlib import Path


DEFAULT_GITIGNORE_CONTENT = """\
# Dependencies
node_modules/
package-lock.json
**/package-lock.json

# Python cache
__pycache__/
**/__pycache__/

# Build output
dist/

# Environment — never commit secrets
.env

# TypeScript build info
*.tsbuildinfo
*.js.map

# Deployment state
.developable/

# OS
.DS_Store
Thumbs.db

# Logs
*.log
npm-debug.log*

# Prisma generated client (regenerated on install)
prisma/generated/
"""


REQUIRED_GITIGNORE_PATTERNS = [
    "package-lock.json",
    "**/package-lock.json",
    "__pycache__/",
    "**/__pycache__/",
    ".developable/",
]


def ensure_required_gitignore_patterns(gitignore_path: Path) -> bool:
    """
    Ensure the generated project ignores required Developable artifacts.

    Returns True when the file changed, False when it was already compliant.
    """
    if gitignore_path.exists():
        content = gitignore_path.read_text()
    else:
        content = DEFAULT_GITIGNORE_CONTENT

    lines = content.splitlines()
    existing = {line.strip() for line in lines}
    missing = [pattern for pattern in REQUIRED_GITIGNORE_PATTERNS if pattern not in existing]
    if not missing:
        return False

    if lines and lines[-1].strip():
        lines.append("")
    if missing:
        lines.append("# Developable managed ignores")
        lines.extend(missing)
    gitignore_path.write_text("\n".join(lines) + "\n")
    return True
