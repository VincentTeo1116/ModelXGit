"""Shared secret-detection rules. Values are never printed, only masked."""
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
PLACEHOLDER = re.compile(r"(?i)(your[_\-]?|xxx|changeme|example|placeholder|dummy|<.*>|\$\{|%VITE_|process\.env|import\.meta\.env|os\.environ)")


def entropy(s):
    if not s:
        return 0.0
    counts = {c: s.count(c) for c in set(s)}
    return -sum((n / len(s)) * math.log2(n / len(s)) for n in counts.values())


HIGH_ENTROPY = re.compile(r"['\"]([A-Za-z0-9+/=_\-]{32,})['\"]")


def mask(value):
    value = value.strip()
    return (value[:5] + "...[masked]") if len(value) > 5 else "[masked]"


def scan_line(line):
    """Return list of (type, masked_value) findings for one line."""
    if "secret-scan: allow" in line:
        return []
    found = []
    for name, rx in PATTERNS:
        for m in rx.finditer(line):
            val = m.group(m.lastindex) if (name == "Hardcoded secret assignment" and m.lastindex) else m.group(0)
            if PLACEHOLDER.search(val):
                continue
            found.append((name, mask(val)))
    if not found:
        for m in HIGH_ENTROPY.finditer(line):
            v = m.group(1)
            if entropy(v) >= 4.3 and not PLACEHOLDER.search(v):
                found.append(("High-entropy string", mask(v)))
    return found
