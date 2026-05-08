"""Vocabulary hints (keyterms) for speech-to-text recognition.

Provides domain-specific and project-specific technical terms to improve
STT accuracy when recognizing technical vocabulary.
"""

import json
import re
import subprocess
from pathlib import Path

# Domain-specific technical terms that STT engines should recognize
DOMAIN_KEYTERMS = [
    # MCP and protocols
    "MCP",
    "SSE",
    "stdio",
    "HTTP",
    "WebSocket",
    "JSON",
    "YAML",
    "TOML",
    # Search and text processing
    "grep",
    "ripgrep",
    "regex",
    "glob",
    "sed",
    "awk",
    # Programming languages
    "Python",
    "TypeScript",
    "JavaScript",
    "Rust",
    "Go",
    "Java",
    "C++",
    "C#",
    "Ruby",
    "PHP",
    "Swift",
    "Kotlin",
    # Testing frameworks
    "pytest",
    "unittest",
    "Jest",
    "Mocha",
    "Cypress",
    "Playwright",
    # Version control
    "git",
    "GitHub",
    "GitLab",
    "Bitbucket",
    "commit",
    "branch",
    "merge",
    "rebase",
    "pull request",
    "PR",
    # Container and orchestration
    "Docker",
    "Kubernetes",
    "kubectl",
    "Helm",
    "Podman",
    "containerd",
    # Package managers
    "npm",
    "yarn",
    "pnpm",
    "pip",
    "pipenv",
    "poetry",
    "uv",
    "cargo",
    "maven",
    "gradle",
    # Build tools
    "webpack",
    "Vite",
    "Rollup",
    "Parcel",
    "esbuild",
    "Babel",
    # Databases
    "PostgreSQL",
    "MySQL",
    "MongoDB",
    "Redis",
    "SQLite",
    "Elasticsearch",
    # Cloud providers
    "AWS",
    "Azure",
    "GCP",
    "S3",
    "EC2",
    "Lambda",
    "CloudFormation",
    # CI/CD
    "Jenkins",
    "CircleCI",
    "Travis",
    "GitHub Actions",
    "GitLab CI",
    # API and web
    "REST",
    "GraphQL",
    "gRPC",
    "OpenAPI",
    "Swagger",
    "API",
    "endpoint",
    # Framework-specific
    "React",
    "Vue",
    "Angular",
    "Django",
    "Flask",
    "FastAPI",
    "Express",
    "Next.js",
    "Nuxt",
    # Development tools
    "VSCode",
    "IntelliJ",
    "tmux",
    "vim",
    "emacs",
    "nano",
    # Code quality
    "ESLint",
    "Prettier",
    "Black",
    "Ruff",
    "Pylint",
    "mypy",
    # Koder-specific
    "Koder",
    "LiteLLM",
    "OpenAI",
    "Anthropic",
    "Claude",
    "GPT",
]


def get_project_keyterms(cwd: str) -> list[str]:
    """Extract project-specific vocabulary terms.

    Args:
        cwd: Current working directory (project root)

    Returns:
        List of project-specific terms extracted from:
        - Git branch name (split on /, -, _)
        - File names in top-level directory
        - Package name from pyproject.toml or package.json
    """
    terms = []

    # Extract from git branch name
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--abbrev-ref", "HEAD"],
            cwd=cwd,
            capture_output=True,
            text=True,
            check=True,
            timeout=5,
        )
        branch_name = result.stdout.strip()
        # Split on common delimiters: /, -, _
        branch_terms = re.split(r"[/_-]", branch_name)
        terms.extend(branch_terms)
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired, FileNotFoundError):
        # Git not available or not in a git repo
        pass

    # Extract from top-level file names
    try:
        project_path = Path(cwd)
        for item in project_path.iterdir():
            if item.is_file():
                # Remove extension and split on delimiters
                name_without_ext = item.stem
                file_terms = re.split(r"[/_-]", name_without_ext)
                terms.extend(file_terms)
    except (OSError, PermissionError):
        pass

    # Extract from pyproject.toml
    try:
        pyproject_path = Path(cwd) / "pyproject.toml"
        if pyproject_path.exists():
            content = pyproject_path.read_text()
            # Simple regex to extract package name
            match = re.search(r'name\s*=\s*["\']([^"\']+)["\']', content)
            if match:
                package_name = match.group(1)
                package_terms = re.split(r"[/_-]", package_name)
                terms.extend(package_terms)
    except (OSError, PermissionError):
        pass

    # Extract from package.json
    try:
        package_json_path = Path(cwd) / "package.json"
        if package_json_path.exists():
            content = package_json_path.read_text()
            data = json.loads(content)
            if "name" in data:
                package_name = data["name"]
                package_terms = re.split(r"[/_-]", package_name)
                terms.extend(package_terms)
    except (OSError, PermissionError, json.JSONDecodeError):
        pass

    # Filter out short terms (1-2 chars) and clean up
    filtered_terms = [term.strip() for term in terms if term.strip() and len(term.strip()) >= 3]

    return filtered_terms


def get_all_keyterms(cwd: str) -> list[str]:
    """Get combined domain and project-specific keyterms, deduplicated.

    Args:
        cwd: Current working directory (project root)

    Returns:
        Deduplicated list of all keyterms (domain + project)
    """
    # Get project terms
    project_terms = get_project_keyterms(cwd)

    # Combine domain and project terms
    all_terms = list(DOMAIN_KEYTERMS) + project_terms

    # Deduplicate (case-insensitive)
    seen = set()
    unique_terms = []
    for term in all_terms:
        term_lower = term.lower()
        if term_lower not in seen:
            seen.add(term_lower)
            unique_terms.append(term)

    return unique_terms
