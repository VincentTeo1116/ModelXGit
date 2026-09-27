"""Shared secret-detection rules. Values are never printed, only masked."""
import hashlib
import math
import re

PATTERNS = [
    ("AWS access key", re.compile(r"\b(AKIA|ASIA)[0-9A-Z]{16}\b")),
    ("Google API key", re.compile(r"\bAIza[0-9A-Za-z\-_]{35}\b")),
    ("GitHub token", re.compile(r"\b(ghp|gho|ghu|ghs|ghr)_[0-9A-Za-z]{36,}\b|\bgithub_pat_[0-9A-Za-z_]{40,}\b")),
    ("OpenAI-style key", re.compile(r"\bsk-[A-Za-z0-9_\-]{20,}\b")),
    ("Slack token", re.compile(r"\bxox[abprs]-[0-9A-Za-z\-]{10,}\b")),
    ("Stripe key", re.compile(r"\b(sk|rk)_(live|test)_[0-9A-Za-z]{16,}\b")),
    ("JWT / Supabase key", re.compile(r"\beyJ[A-Za-z0-9_\-]{10,}\.[A-Za-z0-9_\-]{10,}\.[A-Za-z0-9_\-]{10,}\b")),
    ("Private key block", re.compile(r"-----BEGIN (RSA |EC |DSA |OPENSSH |PGP )?PRIVATE KEY-----")),
    ("Service account key (JSON)", re.compile(r'"type"\s*:\s*"service_account"')),
    ("SMTP / mail credentials", re.compile(r"(?i)\b(smtp|mail)[_\-]?(pass(word)?|user(name)?|pwd)\b\s*[:=]\s*['\"]?[^\s'\"]{4,}")),
    ("App-specific password", re.compile(r"(?i)(pass|pwd|app)[\w\-]*\s*[:=]\s*['\"]?[a-z]{4} ?[a-z]{4} ?[a-z]{4} ?[a-z]{4}\b")),
    ("Connection string with password", re.compile(r"(?i)\b(postgres(ql)?|mysql|mongodb(\+srv)?|redis|amqp)://[^:\s/]+:[^@\s]+@")),
    ("Hardcoded secret assignment", re.compile(
        r"(?i)\b[\w\-]*(api[_\-]?key|secret|token|passwd|password|client[_\-]?secret|access[_\-]?key)[\w\-]*\b\s*[:=]\s*['\"]([^'\"\s]{8,})['\"]")),
]

SENSITIVE_FILES = [
    ".env", ".env.*", "*.env", "serviceAccountKey.json", "*serviceAccount*.json",
    "credentials.json", "*.pem", "*.key", "*.p12", "*.pfx", "id_rsa", "id_ed25519",
]
SAFE_ENV_SUFFIXES = (".example", ".sample", ".template", ".dist")
# Lockfiles are full of integrity hashes that look like secrets; skip their contents.
LOCKFILES = {
    "package-lock.json", "npm-shrinkwrap.json", "yarn.lock", "pnpm-lock.yaml", "bun.lockb",
    "poetry.lock", "Pipfile.lock", "uv.lock", "Cargo.lock", "composer.lock", "Gemfile.lock", "go.sum",
}


def is_lockfile(path):
    return path.replace("\\", "/").rsplit("/", 1)[-1] in LOCKFILES
# A value is a placeholder only if it clearly looks like one. Searching for words
# like "xxx" anywhere in it would skip real keys that contain them by chance
# (a real Supabase key containing "XxX" was missed this way).
PLACEHOLDER_MARKERS = re.compile(r"<[^>]*>|\$\{[^}]*\}|%[A-Z0-9_]+%|process\.env|import\.meta\.env|os\.environ")
PLACEHOLDER_WORDS = re.compile(
    r"(?i)^(your|example|dummy|change[_\-]?me|placeholder|replace[_\-]?me)([_\-.\s].*|[a-z_\-.\s]*)$")
PLACEHOLDER_FILL = re.compile(r"(?i)^([a-z]{1,6}[_\-])?[x*_.\-]{3,}$")  # xxxx, sk-XXXXXXXX, ghp_****


def is_placeholder(value):
    v = value.strip().strip("'\"")
    return bool(PLACEHOLDER_MARKERS.search(v) or PLACEHOLDER_WORDS.match(v) or PLACEHOLDER_FILL.match(v))


def entropy(s):
    if not s:
        return 0.0
    counts = {c: s.count(c) for c in set(s)}
    return -sum((n / len(s)) * math.log2(n / len(s)) for n in counts.values())


HIGH_ENTROPY = re.compile(r"['\"]([A-Za-z0-9+/=_\-]{32,})['\"]")


def mask(value):
    value = value.strip()
    return (value[:5] + "...[masked]") if len(value) > 5 else "[masked]"


def fingerprint(value):
    """Stable ID of the full value, for de-duplication only. Never print or store it."""
    return hashlib.sha256(value.strip().encode("utf-8", "replace")).hexdigest()


def scan_line_full(line):
    """Return list of (type, masked_value, fingerprint) findings for one line.
    Use the fingerprint (not the masked value) to tell findings apart: many
    different keys share the same first 5 characters (every JWT starts "eyJhb")."""
    if "secret-scan: allow" in line:
        return []
    found = []
    for name, rx in PATTERNS:
        for m in rx.finditer(line):
            val = m.group(m.lastindex) if (name == "Hardcoded secret assignment" and m.lastindex) else m.group(0)
            if is_placeholder(val):
                continue
            found.append((name, mask(val), fingerprint(val)))
    if not found:
        for m in HIGH_ENTROPY.finditer(line):
            v = m.group(1)
            if entropy(v) >= 4.3 and not is_placeholder(v):
                found.append(("High-entropy string", mask(v), fingerprint(v)))
    return found


def scan_line(line):
    """Return list of (type, masked_value) findings for one line."""
    return [(kind, masked) for kind, masked, _ in scan_line_full(line)]
