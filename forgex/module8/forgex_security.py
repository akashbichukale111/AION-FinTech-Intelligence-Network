"""FORGEX Module 08 - Security & Supply Chain Intelligence Engine.

Read-only static analysis. Never executes repository code, never sends data
off-box unless osv_online is explicitly enabled (and then only package name /
version / ecosystem are sent). Secrets are redacted before anything is stored.
"""
from __future__ import annotations

import fnmatch, hashlib, json, math, os, re, sqlite3, sys, tarfile, threading, time, tomllib, uuid, zipfile
from contextlib import closing
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Callable, NamedTuple, Optional

TOOL_VERSION = "forgex-security/0.1.0"
MAX_FILE_BYTES = 1_000_000
MAX_FILES = 50_000
SKIP_DIRS = {".git", "node_modules", "venv", ".venv", "__pycache__", "dist", "build", "target", ".tox", ".idea", "vendor"}
SEV_W = {"critical": 10, "high": 7, "medium": 4, "unknown": 4, "low": 2, "info": 0.5}
CONF_W = {"high": 1.0, "medium": 0.6, "low": 0.3}
BUCKETS = ("confirmed", "potential", "informational", "false_positive_candidate")

# ───────────────────────── safe filesystem ─────────────────────────

def resolve_repo(path: str, allowed_root: Optional[str] = None) -> Path:
    p = Path(path).resolve(strict=True)
    if not p.is_dir():
        raise ValueError("repository path is not a directory")
    if allowed_root:
        try:
            p.relative_to(Path(allowed_root).resolve())
        except ValueError:
            raise PermissionError("repository path is outside the allowed root")
    return p


def safe_walk(root: Path):
    n = 0
    for dp, dn, fn in os.walk(root, followlinks=False):
        dn[:] = [d for d in dn if d not in SKIP_DIRS and not os.path.islink(os.path.join(dp, d))]
        for f in fn:
            p = Path(dp) / f
            try:
                if p.is_symlink():
                    continue
                p.resolve().relative_to(root)
                if p.stat().st_size > MAX_FILE_BYTES:
                    continue
            except (ValueError, OSError):
                continue
            n += 1
            if n > MAX_FILES:
                return
            yield p, str(p.relative_to(root)).replace("\\", "/")


def safe_extract(archive: str, dest: str, max_bytes: int = 500_000_000, max_files: int = 50_000) -> None:
    """Extract zip/tar with traversal, symlink and zip-bomb protection."""
    d = Path(dest).resolve()
    d.mkdir(parents=True, exist_ok=True)

    def check(name: str):
        t = (d / name).resolve()
        if t != d and not str(t).startswith(str(d) + os.sep):
            raise ValueError(f"archive path traversal blocked: {name}")

    total = n = 0
    if zipfile.is_zipfile(archive):
        with zipfile.ZipFile(archive) as z:
            for i in z.infolist():
                check(i.filename)
                if (i.external_attr >> 16) & 0o170000 == 0o120000:
                    raise ValueError("symlink in archive blocked")
                total += i.file_size; n += 1
                if total > max_bytes or n > max_files:
                    raise ValueError("archive too large")
            z.extractall(d)
    elif tarfile.is_tarfile(archive):
        with tarfile.open(archive) as t:
            for m in t.getmembers():
                check(m.name)
                if m.issym() or m.islnk() or m.isdev():
                    raise ValueError("link/device in archive blocked")
                total += m.size; n += 1
                if total > max_bytes or n > max_files:
                    raise ValueError("archive too large")
            t.extractall(d, filter="data") if hasattr(tarfile, "data_filter") else t.extractall(d)
    else:
        raise ValueError("unsupported archive type")


def git_head(root: Path) -> Optional[str]:
    try:
        h = (root / ".git" / "HEAD").read_text().strip()
        if h.startswith("ref:"):
            ref = h[4:].strip()
            if not re.fullmatch(r"refs/[\w./-]+", ref) or ".." in ref:
                return None
            h = (root / ".git" / ref).read_text().strip()
        return h if re.fullmatch(r"[0-9a-f]{40}", h) else None
    except OSError:
        return None

# ───────────────────────── findings ─────────────────────────

@dataclass
class Finding:
    id: str; rule_id: str; category: str; severity: str; confidence: str; bucket: str
    component: str; file: str; line: int; evidence: str; method: str; impact: str; remediation: str
    exploitability: str = ""; references: list = field(default_factory=list)
    validation_status: str = "unvalidated"; related: list = field(default_factory=list); source: str = "forgex-m08"


def mk(rule_id, category, severity, confidence, bucket, component, file, line, evidence, method,
       impact, remediation, refs=(), exploit="", validation="unvalidated", source="forgex-m08") -> Finding:
    fid = "F-" + hashlib.sha1(f"{rule_id}|{file}|{line}|{evidence}".encode()).hexdigest()[:12]
    return Finding(fid, rule_id, category, severity, confidence, bucket, component, file, line, evidence[:240],
                   method, impact, remediation, exploit, list(refs), validation, [], source)

# ───────────────────────── secrets ─────────────────────────

def redact(v: str) -> str:
    return "[REDACTED]" if len(v) < 12 else f"{v[:2]}…[REDACTED:{len(v)}]"

# (id, category, severity, regex, format_verified, description). Key-format patterns contain NO capture groups.
SECRET_PATTERNS = [(i, c, s, re.compile(r), f, d) for i, c, s, r, f, d in [
    ("SEC-AWS-KEY", "cloud_credential", "critical", r"\b(?:AKIA|ASIA)[0-9A-Z]{16}\b", True, "AWS access key ID"),
    ("SEC-GH-TOKEN", "access_token", "critical", r"\bgh[pousr]_[A-Za-z0-9]{36,}\b", True, "GitHub token"),
    ("SEC-STRIPE", "payment_secret", "critical", r"\b[sr]k_live_[0-9a-zA-Z]{24,}\b", True, "Stripe live secret key"),
    ("SEC-SLACK", "access_token", "high", r"\bxox[baprs]-[0-9A-Za-z-]{10,}\b", True, "Slack token"),
    ("SEC-GOOGLE", "api_key", "high", r"\bAIza[0-9A-Za-z_\-]{35}\b", True, "Google API key"),
    ("SEC-PRIVKEY", "private_key", "critical", r"-----BEGIN (?:RSA |EC |DSA |OPENSSH |PGP )?PRIVATE KEY(?: BLOCK)?-----", True, "Private key material"),
    ("SEC-DBURL", "database_credential", "high", r"\b(?:postgres(?:ql)?|mysql|mongodb(?:\+srv)?|redis|amqp)://[^\s:/@'\"]+:([^\s@'\"]{3,})@", False, "Credentials embedded in connection URL"),
    ("SEC-JWT-SECRET", "jwt_secret", "high", r"""(?i)\b(jwt[_-]?(?:secret|key)|signing[_-]?key)\b["']?\s*[:=]\s*["']([^"'\s]{6,})["']""", False, "JWT signing secret"),
    ("SEC-GENERIC", "hardcoded_credential", "medium", r"""(?i)\b([a-z0-9_.-]*(?:password|passwd|pwd|secret|api[_-]?key|access[_-]?token|auth[_-]?token|client[_-]?secret))["']?\s*[:=]\s*["']([^"'\s]{6,})["']""", False, "Hardcoded credential assignment"),
]]
KV_RX = re.compile(r"""(?i)^\s*(?:export\s+)?(?:ENV\s+|ARG\s+)?([A-Za-z0-9_.-]*(?:password|passwd|pwd|secret|api[_-]?key|access[_-]?token|auth[_-]?token|private[_-]?key|client[_-]?secret)[A-Za-z0-9_.-]*)\s*[=:]\s*["']?([^\s"'#]{6,})""")
PLACEHOLDER = re.compile(r"""(?i)^(?:<.*>|\$\{.*\}|\$\w+|%\(.*\)s|\{\{.*\}\}|x{3,}|\*+|\.{3,}|your[_-].*|none|null|todo|true|false)$""")
TESTVAL = re.compile(r"(?i)test|dummy|example|sample|fake|mock|changeme|placeholder|123456")
TESTPATH = re.compile(r"(?i)(^|/)(tests?|__tests__|spec|fixtures?|examples?|samples?|mocks?|testdata)(/|$)|\.(example|sample|template)$|(^|/)\.env\.(example|sample|template)$")

SECRET_FIX = {
    "private_key": "Treat the key as compromised: revoke/replace it at the issuer, remove it from the repo (and git history), store in a secret manager.",
    "default": "Treat the value as exposed: rotate it at the provider (manual step - FORGEX never rotates credentials), move it to env/secret manager, purge from git history, add pre-commit secret scanning, review provider audit logs.",
}


def scrub(line: str) -> str:
    """Redact any secret-looking value from an evidence line."""
    out = line
    for rid, _, _, rx, _, _ in SECRET_PATTERNS:
        if rid == "SEC-PRIVKEY":
            continue
        def sub(m):
            g = m.lastindex
            return m.group(0).replace(m.group(g), redact(m.group(g))) if g else redact(m.group(0))
        out = rx.sub(sub, out)
    out = KV_RX.sub(lambda m: m.group(0).replace(m.group(2), redact(m.group(2))), out)
    return out.strip()[:240]


def entropy(s: str) -> float:
    return -sum(c / len(s) * math.log2(c / len(s)) for c in (s.count(x) for x in set(s))) if s else 0.0


def scan_secrets(rel: str, lines: list[str], cfg: dict, is_config: bool) -> list[Finding]:
    al = cfg.get("allowlist", {})
    if any(fnmatch.fnmatch(rel, g) for g in al.get("paths", [])):
        return []
    out, test_path = [], bool(TESTPATH.search(rel))
    for i, l in enumerate(lines, 1):
        if len(l) > 2000:
            continue
        hits = []
        for rid, cat, sev, rx, fmt, desc in SECRET_PATTERNS:
            for m in rx.finditer(l):
                hits.append((rid, cat, sev, fmt, desc, m.group(m.lastindex) if m.lastindex else m.group(0)))
        if is_config:
            for m in KV_RX.finditer(l):
                hits.append(("SEC-CONFIG-KV", "hardcoded_credential", "medium", False, "Credential-like value in configuration file", m.group(2)))
        seen = set()
        for rid, cat, sev, fmt, desc, val in hits:
            if val in seen:
                continue
            seen.add(val)
            if rid in al.get("rule_ids", []) or any(re.search(r, l) for r in al.get("regexes", [])):
                continue
            if rid != "SEC-PRIVKEY" and PLACEHOLDER.match(val):
                continue
            ev = scrub(l)
            if test_path or (not fmt and TESTVAL.search(val)) or (fmt and "EXAMPLE" in val.upper()):
                out.append(mk(rid, "test_credential", "low", "medium", "false_positive_candidate", rel, rel, i, ev,
                              "secret-pattern", f"{desc} that looks like a test/placeholder value.",
                              "Confirm it is non-production, then add it to the allowlist.", ["CWE-798"], validation="likely_test_credential"))
                continue
            conf = "high" if fmt else ("medium" if entropy(val) >= 3.0 else "low")
            out.append(mk(rid, cat, sev, conf, "confirmed" if fmt else "potential", rel, rel, i, ev, "secret-pattern",
                          f"{desc} committed to source control may allow unauthorized access.",
                          SECRET_FIX.get(cat, SECRET_FIX["default"]), ["CWE-798", "CWE-259"],
                          validation="format_verified_not_liveness_tested" if fmt else "pattern_match"))
    return out

# ───────────────────────── static code analysis ─────────────────────────

class Rule(NamedTuple):
    id: str; cat: str; sev: str; conf: str; langs: str; rx: re.Pattern
    taint: int; det: bool; impact: str; fix: str; refs: tuple

def _R(i, c, s, cf, l, rx, t, d, imp, fix, refs): return Rule(i, c, s, cf, l, re.compile(rx), t, d, imp, fix, tuple(refs))

# taint: 0 = pattern alone, 1 = user-input nearby raises confidence, 2 = user-input nearby required
SAST = [
 _R("SAST-SQLI-PY", "sql_injection", "high", "medium", "py", r"""\.(?:execute|executemany|raw)\(\s*(?:f["']|["'][^"']*["']\s*(?:%|\+|\.format\())""", 1, False, "Attacker-controlled input may change SQL semantics.", "Use parameterized queries / ORM bindings.", ["CWE-89"]),
 _R("SAST-SQLI-JS", "sql_injection", "high", "medium", "js", r"""\.(?:query|execute|raw)\(\s*(?:`[^`]*\$\{|["'][^"']*["']\s*\+)""", 1, False, "Attacker-controlled input may change SQL semantics.", "Use placeholders ($1 / ?) with bound parameters.", ["CWE-89"]),
 _R("SAST-SQLI-PHP", "sql_injection", "high", "medium", "php", r"""(?:mysqli_query|->query|->exec|pg_query)\s*\([^;]*(?:"[^"]*\$|\.\s*\$)""", 1, False, "Attacker-controlled input may change SQL semantics.", "Use PDO prepared statements.", ["CWE-89"]),
 _R("SAST-SQLI-JAVA", "sql_injection", "high", "medium", "java", r"""(?:executeQuery|executeUpdate|execute|prepareStatement|createQuery|createNativeQuery)\(\s*"[^"]*"\s*\+""", 1, False, "Attacker-controlled input may change SQL semantics.", "Use PreparedStatement with bound parameters.", ["CWE-89"]),
 _R("SAST-CMDI-PY", "command_injection", "high", "medium", "py", r"\bos\.(?:system|popen)\(|subprocess\.\w+\([^)]*shell\s*=\s*True", 1, False, "Shell command execution may run attacker-chosen commands.", "Pass argument lists without shell=True; validate against an allowlist.", ["CWE-78"]),
 _R("SAST-CMDI-JS", "command_injection", "high", "medium", "js", r"""\b(?:exec|execSync)\(\s*(?:`[^`]*\$\{|[^)]*\+)""", 1, False, "Shell command execution may run attacker-chosen commands.", "Use execFile/spawn with argument arrays.", ["CWE-78"]),
 _R("SAST-CMDI-JAVA", "command_injection", "high", "medium", "java", r"Runtime\.getRuntime\(\)\.exec\(|new\s+ProcessBuilder\(", 1, False, "Process execution may run attacker-chosen commands.", "Use fixed command arrays; validate inputs.", ["CWE-78"]),
 _R("SAST-CMDI-PHP", "command_injection", "high", "medium", "php", r"\b(?:system|exec|shell_exec|passthru|popen|proc_open)\s*\(", 1, False, "Shell command execution may run attacker-chosen commands.", "Avoid shell calls; use escapeshellarg and allowlists.", ["CWE-78"]),
 _R("SAST-DESER-PY", "unsafe_deserialization", "high", "medium", "py", r"\b(?:pickle|cPickle|marshal|dill)\.loads?\(|\byaml\.load\((?![^)]*Loader\s*=\s*(?:yaml\.)?(?:Safe|CSafe))", 0, False, "Deserializing untrusted data can lead to code execution.", "Use json / yaml.safe_load; never unpickle untrusted data.", ["CWE-502"]),
 _R("SAST-DESER-JAVA", "unsafe_deserialization", "high", "medium", "java", r"new\s+ObjectInputStream\(", 0, False, "Java deserialization of untrusted data can lead to code execution.", "Use allowlist ObjectInputFilter or a data-only format.", ["CWE-502"]),
 _R("SAST-DESER-PHP", "unsafe_deserialization", "high", "medium", "php", r"\bunserialize\s*\(", 0, False, "PHP object injection via untrusted unserialize().", "Use json_decode or unserialize with allowed_classes=false.", ["CWE-502"]),
 _R("SAST-DESER-JS", "unsafe_deserialization", "high", "medium", "js", r"""require\(["']node-serialize["']\)|\.unserialize\(""", 0, False, "Unsafe deserialization may execute code.", "Use JSON.parse on validated data.", ["CWE-502"]),
 _R("SAST-PATH-PY", "path_traversal", "high", "medium", "py", r"\b(?:open|send_file|send_from_directory)\(|os\.path\.join\(", 2, False, "User-controlled path may read/write files outside intended directory.", "Resolve and verify the path stays under a base directory.", ["CWE-22"]),
 _R("SAST-PATH-JS", "path_traversal", "high", "medium", "js", r"\b(?:readFile|readFileSync|createReadStream|sendFile|writeFile|unlink)\(", 2, False, "User-controlled path may read/write files outside intended directory.", "Normalize and verify paths stay under a base directory.", ["CWE-22"]),
 _R("SAST-PATH-PHP", "path_traversal", "high", "medium", "php", r"\b(?:include|require)(?:_once)?\b|file_get_contents\(|fopen\(|readfile\(", 2, False, "User-controlled path may enable file read / inclusion.", "Use realpath + base-dir check; never include user input.", ["CWE-22", "CWE-98"]),
 _R("SAST-PATH-JAVA", "path_traversal", "high", "medium", "java", r"new\s+File\(|Paths\.get\(|new\s+FileInputStream\(", 2, False, "User-controlled path may read/write files outside intended directory.", "Canonicalize and verify against a base directory.", ["CWE-22"]),
 _R("SAST-CRYPTO-PY", "weak_cryptography", "medium", "medium", "py", r"hashlib\.(?:md5|sha1)\(|AES\.MODE_ECB|\bDES\.new\(", 0, False, "Weak hash/cipher may allow collisions or data recovery.", "Use SHA-256+ (argon2/bcrypt for passwords) and AES-GCM.", ["CWE-327"]),
 _R("SAST-CRYPTO-JAVA", "weak_cryptography", "medium", "medium", "java", r"""MessageDigest\.getInstance\(\s*"(?:MD5|SHA-?1)"|Cipher\.getInstance\(\s*"(?:DES|DESede|RC4|AES(?:/ECB[^"]*)?)\"""", 0, False, "Weak hash/cipher may allow collisions or data recovery.", "Use SHA-256+ and AES/GCM/NoPadding.", ["CWE-327"]),
 _R("SAST-CRYPTO-JS", "weak_cryptography", "medium", "medium", "js", r"""createHash\(\s*["'](?:md5|sha1)["']|createCipher\(""", 0, False, "Weak hash/cipher may allow collisions or data recovery.", "Use SHA-256+ and createCipheriv with AES-GCM.", ["CWE-327"]),
 _R("SAST-CRYPTO-PHP", "weak_cryptography", "medium", "medium", "php", r"\b(?:md5|sha1)\s*\(|mcrypt_", 0, False, "Weak hash/cipher; unsuitable for passwords or integrity.", "Use password_hash() / hash('sha256') / sodium.", ["CWE-327", "CWE-328"]),
 _R("SAST-TLS-OFF", "insecure_authentication", "high", "high", "any", r"""verify\s*=\s*False|rejectUnauthorized\s*:\s*false|ssl\._create_unverified_context|CURLOPT_SSL_VERIFYPEER\s*,\s*(?:false|0)|NoopHostnameVerifier|ALLOW_ALL_HOSTNAME_VERIFIER|NODE_TLS_REJECT_UNAUTHORIZED\s*=\s*["']?0""", 0, True, "Disabled TLS verification enables man-in-the-middle attacks.", "Enable certificate and hostname verification.", ["CWE-295"]),
 _R("SAST-JWT-WEAK", "insecure_authentication", "high", "high", "any", r"""algorithms\s*=\s*\[\s*["']none["']|verify_signature["']\s*:\s*False|jwt\.decode\([^)]*verify\s*=\s*False|\balg["']?\s*[:=]\s*["']none["']""", 0, True, "JWT signature not verified allows token forgery.", "Always verify signatures with a fixed algorithm allowlist.", ["CWE-347"]),
 _R("SAST-AUTH-CLIENTROLE", "authorization_weakness", "medium", "low", "py", r"request\.(?:args|form|json)\S*(?:is_admin|isAdmin|role)\b", 0, False, "Trusting a client-supplied role may allow privilege escalation.", "Derive roles from the server-side session/token only.", ["CWE-639", "CWE-285"]),
 _R("SAST-AUTH-CSRF", "authorization_weakness", "medium", "medium", "py", r"@csrf_exempt\b", 0, False, "CSRF protection disabled for a view.", "Remove exemption or protect with tokens / SameSite cookies.", ["CWE-352"]),
 _R("SAST-AUTH-ADMINROUTE", "authorization_weakness", "medium", "low", "js", r"""\b(?:app|router)\.(?:get|post|put|delete|patch)\(\s*["']/(?:admin|internal)[^"']*["']\s*,\s*(?:async\s*)?\(?\s*(?:req|\()""", 0, False, "Sensitive route with no visible auth middleware.", "Add authentication/authorization middleware.", ["CWE-862"]),
 _R("SAST-AUTH-PERMITALL", "authorization_weakness", "low", "medium", "java", r"\.permitAll\(\)", 0, False, "Endpoints publicly accessible; verify intentional.", "Restrict to authenticated roles where appropriate.", ["CWE-862"]),
 _R("SAST-EVAL", "unsafe_input_handling", "high", "medium", "py|js|php", r"\beval\s*\(|new\s+Function\(|(?<![\w.])exec\(", 1, False, "Dynamic code evaluation can execute attacker-controlled code.", "Remove eval/exec; use safe parsers.", ["CWE-95"]),
 _R("SAST-XSS", "unsafe_input_handling", "medium", "medium", "py|js|php", r"\.innerHTML\s*=|dangerouslySetInnerHTML|document\.write\(|render_template_string\(|mark_safe\(|\|\s*safe\b|echo\s+[^;]*\$_(?:GET|POST|REQUEST)", 1, False, "Unescaped output may enable cross-site scripting.", "Escape output contextually or use safe templating.", ["CWE-79"]),
 _R("SAST-SSRF-PY", "ssrf", "high", "medium", "py", r"\brequests\.(?:get|post|put|request)\(|urllib\.request\.urlopen\(", 2, False, "User-controlled URL may reach internal services.", "Allowlist hosts; block private/link-local ranges.", ["CWE-918", "OWASP-API7:2023"]),
 _R("SAST-SSRF-JS", "ssrf", "high", "medium", "js", r"\b(?:axios\.(?:get|post)|fetch|http\.get|got)\(", 2, False, "User-controlled URL may reach internal services.", "Allowlist hosts; block private/link-local ranges.", ["CWE-918", "OWASP-API7:2023"]),
 _R("SAST-SSRF-JAVA", "ssrf", "high", "medium", "java", r"new\s+URL\(", 2, False, "User-controlled URL may reach internal services.", "Allowlist hosts; block private/link-local ranges.", ["CWE-918", "OWASP-API7:2023"]),
 _R("SAST-SSRF-PHP", "ssrf", "high", "medium", "php", r"CURLOPT_URL|file_get_contents\(\s*\$", 2, False, "User-controlled URL may reach internal services.", "Allowlist hosts; block private/link-local ranges.", ["CWE-918", "OWASP-API7:2023"]),
 _R("SAST-LOG-SECRET", "sensitive_data_exposure", "medium", "low", "any", r"""(?i)(?:log(?:ger)?\.\w+|print|console\.(?:log|info|debug)|error_log|System\.out\.println)\s*\([^)]*\b(?:password|passwd|secret|token|api[_-]?key|authorization|ssn|card[_-]?number)\b""", 0, False, "Sensitive data may be written to logs.", "Remove or mask sensitive values before logging.", ["CWE-532"]),
 _R("SAST-ERR-DEBUG", "insecure_error_handling", "medium", "high", "py", r"app\.run\([^)]*debug\s*=\s*True|traceback\.print_exc\(", 0, True, "Debug/stack traces can leak internals (Werkzeug debugger allows RCE).", "Disable debug; log server-side, return generic errors.", ["CWE-209", "CWE-489"]),
 _R("SAST-ERR-STACK", "insecure_error_handling", "medium", "medium", "js", r"res\.(?:send|json)\([^)]*\b(?:err|error)\.stack", 0, False, "Stack traces returned to clients leak internals.", "Return generic errors; log details server-side.", ["CWE-209"]),
 _R("SAST-ERR-PHP", "insecure_error_handling", "medium", "medium", "php", r"""ini_set\(\s*["']display_errors["']\s*,\s*["']?(?:1|On|true)""", 0, False, "Displayed errors leak internals.", "Set display_errors=Off in production.", ["CWE-209"]),
 _R("SAST-ERR-JAVA", "insecure_error_handling", "low", "medium", "java", r"\.printStackTrace\(\)", 0, False, "Stack traces may leak to output/logs.", "Use a logger; do not expose traces to clients.", ["CWE-209"]),
 _R("SAST-FILE-TEMP", "insecure_file_handling", "medium", "medium", "py", r"tempfile\.mktemp\(", 0, False, "Race-prone temp file creation.", "Use tempfile.mkstemp / NamedTemporaryFile.", ["CWE-377"]),
 _R("SAST-FILE-CHMOD", "insecure_file_handling", "medium", "high", "any", r"chmod\([^)]*(?:0o?777|0o?666)", 0, True, "World-writable file permissions.", "Use least-privilege permissions (e.g. 0o640).", ["CWE-732"]),
 _R("SAST-FILE-UPLOAD", "insecure_file_handling", "medium", "low", "php", r"move_uploaded_file\(", 1, False, "Uploaded files may be executable or oversized.", "Validate type/size, randomize names, store outside web root.", ["CWE-434"]),
 _R("SAST-CORS-ANY", "security_misconfiguration", "medium", "medium", "any", r"""(?i)Access-Control-Allow-Origin["']?\s*[:,=]\s*["']?\*|\borigins?\s*[:=]\s*["']\*["']|\bCORS\(\s*app\s*\)|\bcors\(\s*\)|allow_origins\s*=\s*\[\s*["']\*["']|@CrossOrigin\s*(?:\(\s*(?:origins\s*=\s*)?"\*"\s*\))?\s*$|allowedOrigins\("\*"\)""", 0, False, "Wildcard CORS lets any site read responses; dangerous with credentials.", "Restrict CORS to explicit trusted origins.", ["CWE-942", "OWASP-API8:2023"]),
]
EXT = {".py": "py", ".java": "java", ".js": "js", ".jsx": "js", ".mjs": "js", ".cjs": "js", ".ts": "js", ".tsx": "js", ".php": "php"}
COMMENT = {"py": ("#",), "js": ("//", "*", "/*"), "java": ("//", "*", "/*"), "php": ("//", "#", "*", "/*")}
TAINT = re.compile(r"request\.(?:args|form|json|values|data|GET|POST|files|cookies|headers)|\breq\.(?:params|query|body|headers|cookies)|\$_(?:GET|POST|REQUEST|COOKIE|FILES)|getParameter\(|getHeader\(|sys\.argv|\binput\(|@RequestParam|@PathVariable")


def scan_sast(rel: str, lines: list[str], lang: str) -> list[Finding]:
    out = []
    rules = [r for r in SAST if r.langs == "any" or lang in r.langs.split("|")]
    for i, l in enumerate(lines):
        s = l.strip()
        if not s or len(l) > 1000 or s.startswith(COMMENT[lang]):
            continue
        for r in rules:
            if not r.rx.search(l):
                continue
            tl = TAINT.search(l)
            tw = TAINT.search("\n".join(lines[max(0, i - 5):i]))
            if r.taint == 2 and not (tl or tw):
                continue
            conf = ("high" if tl else "medium" if tw else r.conf) if r.taint else r.conf
            ex = ("User-controlled source on the same line." if tl else "User-controlled source within preceding 5 lines (data flow not proven)."
                  if tw else "No user-controlled source observed nearby (reachability not proven).") if r.taint else ""
            out.append(mk(r.id, r.cat, r.sev, conf, "confirmed" if r.det else "potential", rel, rel, i + 1, scrub(l),
                          "static-pattern+proximity-taint" if r.taint else "static-pattern", r.impact, r.fix, r.refs, ex,
                          "pattern_match_unverified" if not r.det else "insecure_setting_present"))
    return out

# ───────────────────────── config / IaC / CI ─────────────────────────

class CRule(NamedTuple):
    id: str; kind: str; cat: str; sev: str; conf: str; rx: re.Pattern; det: bool; impact: str; fix: str; refs: tuple

def _C(i, k, c, s, cf, rx, d, imp, fix, refs=()): return CRule(i, k, c, s, cf, re.compile(rx, re.I), d, imp, fix, tuple(refs))

CONF_RULES = [
 _C("DOCKER-ROOT", "docker", "container_security", "medium", "high", r"^\s*USER\s+(?:root|0)\b", True, "Container runs as root.", "Create and switch to an unprivileged user.", ["CWE-250"]),
 _C("DOCKER-ADD-URL", "docker", "container_security", "medium", "high", r"^\s*ADD\s+https?://", True, "Remote content fetched without integrity check.", "Use curl with checksum verification, or COPY.", ["CWE-494"]),
 _C("DOCKER-PIPE-SH", "docker", "container_security", "high", "high", r"(?:curl|wget)[^|\n]*\|\s*(?:sudo\s+)?(?:ba)?sh", True, "Piping remote script to shell enables supply-chain compromise.", "Download, verify checksum/signature, then execute.", ["CWE-494"]),
 _C("DOCKER-CHMOD", "docker", "container_security", "medium", "high", r"chmod\s+(?:-R\s+)?0?777", True, "World-writable permissions in image.", "Use least-privilege permissions.", ["CWE-732"]),
 _C("DOCKER-SSH", "docker", "container_security", "low", "medium", r"^\s*EXPOSE\s+22\b", False, "SSH port exposed in container.", "Avoid SSH in containers; use exec/kubectl.", []),
 _C("COMPOSE-PRIV", "compose", "container_security", "high", "high", r"privileged:\s*true", True, "Privileged container has near-host access.", "Remove privileged; add only needed capabilities.", ["CWE-250"]),
 _C("COMPOSE-HOSTNET", "compose", "insecure_network_exposure", "high", "high", r"network_mode:\s*[\"']?host|pid:\s*[\"']?host", True, "Shares host network/PID namespace.", "Use bridge networking and explicit ports.", []),
 _C("COMPOSE-SOCK", "compose", "container_security", "high", "high", r"/var/run/docker\.sock", True, "Docker socket mount gives root-equivalent host control.", "Avoid the socket mount; use a restricted proxy.", ["CWE-250"]),
 _C("COMPOSE-CAP", "compose", "container_security", "high", "medium", r"^\s*-\s*(?:ALL|SYS_ADMIN|NET_ADMIN|SYS_PTRACE)\s*$", False, "Excessive Linux capabilities.", "Drop ALL and add only required caps.", []),
 _C("COMPOSE-UNCONF", "compose", "container_security", "high", "high", r"(?:seccomp|apparmor)[:=]unconfined", True, "Security profile disabled.", "Keep default seccomp/AppArmor profiles.", []),
 _C("COMPOSE-DBPORT", "compose", "insecure_network_exposure", "medium", "medium", r"""^\s*-\s*["']?(?:3306|5432|27017|6379)\s*:\s*(?:3306|5432|27017|6379)["']?\s*$""", False, "Database port published on all interfaces.", "Bind to 127.0.0.1 or keep on internal network.", []),
 _C("COMPOSE-ALLIF", "compose", "insecure_network_exposure", "low", "medium", r"0\.0\.0\.0:\d+:\d+", False, "Port bound to all interfaces.", "Bind to 127.0.0.1 unless public exposure is intended.", []),
 _C("K8S-PRIV", "k8s", "container_security", "high", "high", r"privileged:\s*true", True, "Privileged pod.", "Set privileged: false.", ["CWE-250"]),
 _C("K8S-PRIVESC", "k8s", "container_security", "medium", "high", r"allowPrivilegeEscalation:\s*true", True, "Privilege escalation allowed.", "Set allowPrivilegeEscalation: false.", []),
 _C("K8S-HOSTNS", "k8s", "container_security", "high", "high", r"host(?:Network|PID|IPC):\s*true", True, "Pod shares host namespaces.", "Remove host namespace sharing.", []),
 _C("K8S-ROOT", "k8s", "container_security", "medium", "high", r"runAsNonRoot:\s*false|runAsUser:\s*0\b", True, "Pod runs as root.", "Set runAsNonRoot: true with non-zero UID.", []),
 _C("K8S-HOSTPATH", "k8s", "container_security", "medium", "medium", r"^\s*hostPath:", False, "hostPath volume exposes node filesystem.", "Use PVC/emptyDir; restrict paths read-only.", []),
 _C("K8S-CAP", "k8s", "container_security", "high", "medium", r"^\s*-\s*(?:ALL|SYS_ADMIN|NET_ADMIN)\s*$", False, "Excessive capabilities (verify under capabilities.add).", "Drop ALL; add minimal caps.", []),
 _C("K8S-LATEST", "k8s", "container_security", "low", "high", r"image:\s*\S+:latest\b", True, "Mutable image tag.", "Pin by version or digest.", []),
 _C("TF-OPEN-CIDR", "tf", "infrastructure_misconfiguration", "high", "medium", r"cidr_blocks\s*=\s*\[[^\]]*[\"']0\.0\.0\.0/0[\"']", False, "Security group open to the internet.", "Restrict CIDRs to required sources.", ["CWE-284"]),
 _C("TF-PUBLIC-ACL", "tf", "infrastructure_misconfiguration", "high", "high", r"acl\s*=\s*[\"']public-read(?:-write)?[\"']", True, "Publicly readable/writable bucket.", "Use private ACL and block public access.", []),
 _C("TF-NO-ENCRYPT", "tf", "infrastructure_misconfiguration", "medium", "high", r"(?:encrypted|storage_encrypted|enable_key_rotation)\s*=\s*false", True, "Encryption/rotation disabled.", "Enable encryption at rest / rotation.", []),
 _C("TF-PUBLIC-DB", "tf", "infrastructure_misconfiguration", "high", "high", r"publicly_accessible\s*=\s*true", True, "Database publicly accessible.", "Place in private subnet.", []),
 _C("TF-IAM-STAR", "tf", "infrastructure_misconfiguration", "high", "medium", r"""Actions?\s*=\s*(?:\[\s*)?"\*"|"Action"\s*:\s*"\*\"""", False, "Wildcard IAM action.", "Grant least-privilege actions.", []),
 _C("TF-HTTP", "tf", "tls_configuration", "medium", "medium", r"protocol\s*=\s*[\"']HTTP[\"']|min_tls_version\s*=\s*[\"']TLS1_[01][\"']", False, "Cleartext / legacy TLS.", "Use HTTPS and TLS 1.2+.", []),
 _C("GHA-WRITE-ALL", "gha", "cicd_security", "high", "high", r"permissions:\s*write-all", True, "Workflow token has write access everywhere.", "Declare least-privilege permissions per job.", []),
 _C("GHA-UNPINNED", "gha", "cicd_security", "medium", "high", r"uses:\s*(?!\./|docker://|actions/)[\w.-]+/[\w./-]+@(?![0-9a-f]{40}\b)\S+", False, "Third-party action pinned to mutable ref.", "Pin to full commit SHA.", ["CWE-829"]),
 _C("GHA-INJECTION", "gha", "cicd_security", "high", "medium", r"\$\{\{\s*github\.(?:event\.(?:issue|pull_request|comment|review|review_comment|head_commit|commits)[\w.\[\]*]*\.(?:title|body|message|name|ref|label|email)|head_ref)\s*\}\}", False, "Untrusted event data in expression can inject shell commands if used in run:.", "Pass via env var and quote it; avoid inline expressions in run.", ["CWE-94"]),
 _C("GHA-UNSECURE-CMD", "gha", "cicd_security", "medium", "high", r"ACTIONS_ALLOW_UNSECURE_COMMANDS\s*:\s*[\"']?true", True, "Deprecated insecure workflow commands enabled.", "Remove the flag.", []),
 _C("GHA-PIPE-SH", "gha", "cicd_security", "high", "high", r"(?:curl|wget)[^|\n]*\|\s*(?:sudo\s+)?(?:ba)?sh", True, "Remote script piped to shell in CI.", "Verify checksum/signature before executing.", []),
 _C("GHA-ECHO-SECRET", "gha", "cicd_security", "medium", "medium", r"echo\s+.*\$\{\{\s*secrets\.", False, "Echoing secrets can leak them to logs.", "Never print secrets.", []),
 _C("ENV-DEBUG", "envcfg", "debug_configuration", "medium", "high", r"^\s*(?:DEBUG|FLASK_DEBUG|APP_DEBUG)\s*[=:]\s*[\"']?(?:true|1|on)\b", True, "Debug mode enabled.", "Disable debug outside local dev.", ["CWE-489"]),
 _C("ENV-DEVMODE", "envcfg", "debug_configuration", "low", "medium", r"NODE_ENV\s*[=:]\s*[\"']?development", False, "Development mode configured.", "Use production for deployed envs.", []),
 _C("ENV-TLS-OFF", "envcfg", "tls_configuration", "high", "medium", r"(?:ssl|tls|verify)[\w.-]*\s*[=:]\s*[\"']?(?:false|off|disable[d]?)\b|server\.ssl\.enabled\s*=\s*false", False, "TLS/verification disabled in config.", "Enable TLS and certificate verification.", ["CWE-295"]),
 _C("ENV-HOSTS-STAR", "envcfg", "security_misconfiguration", "medium", "high", r"""ALLOWED_HOSTS\s*=\s*\[\s*["']\*["']""", True, "Host header validation disabled.", "List explicit hosts.", []),
 _C("ENV-COOKIE", "envcfg", "security_misconfiguration", "medium", "high", r"(?:SESSION|CSRF)_COOKIE_SECURE\s*=\s*False", True, "Cookies sent over HTTP.", "Set Secure cookies.", []),
 _C("ENV-CORS", "envcfg", "security_misconfiguration", "medium", "medium", r"""(?:origins?|cors[\w.-]*)\s*[=:]\s*["']?\*""", False, "Wildcard CORS in config.", "Use explicit origins.", ["CWE-942"]),
 _C("ENV-ACTUATOR", "envcfg", "security_misconfiguration", "high", "high", r"management\.endpoints\.web\.exposure\.include\s*=\s*\*|spring\.h2\.console\.enabled\s*=\s*true", True, "Management endpoints/console exposed.", "Expose only health; disable H2 console.", []),
 _C("ENV-NGINX-TLS", "envcfg", "tls_configuration", "medium", "high", r"ssl_protocols[^;]*(?:SSLv3|TLSv1(?:\.0|\.1)?\b)", True, "Legacy TLS protocols enabled.", "Allow TLSv1.2+ only.", []),
]


def config_kinds(rel: str, text: str) -> list[str]:
    lb, k = rel.rsplit("/", 1)[-1].lower(), []
    if lb.startswith("dockerfile") or lb.endswith(".dockerfile"): k.append("docker")
    if re.search(r"(^|/)(docker-)?compose[^/]*\.ya?ml$", rel.lower()): k.append("compose")
    if "/.github/workflows/" in "/" + rel and lb.endswith((".yml", ".yaml")): k.append("gha")
    if lb.endswith((".yml", ".yaml")) and not k and "apiVersion:" in text and re.search(r"^kind:\s*\w+", text, re.M): k.append("k8s")
    if lb.endswith(".tf"): k.append("tf")
    if lb.startswith(".env") or lb == "php.ini" or lb.endswith(".conf") or re.match(r"(settings|config|application)[\w.-]*\.(py|json|ya?ml|ini|toml|properties)$", lb):
        k.append("envcfg")
    return k


def scan_config(rel: str, lines: list[str], text: str, kinds: list[str]) -> list[Finding]:
    out = []
    for i, l in enumerate(lines, 1):
        if len(l) > 1000 or l.strip().startswith("#"):
            continue
        for r in CONF_RULES:
            if r.kind in kinds and r.rx.search(l):
                out.append(mk(r.id, r.cat, r.sev, r.conf, "confirmed" if r.det else "potential", rel, rel, i, scrub(l), "config-rule",
                              r.impact, r.fix, r.refs, validation="insecure_setting_present" if r.det else "pattern_match_unverified"))
    if "docker" in kinds:
        stages, last_from, user = set(), 1, None
        for i, l in enumerate(lines, 1):
            m = re.match(r"\s*FROM\s+(?:--platform=\S+\s+)?(\S+)(?:\s+AS\s+(\S+))?", l, re.I)
            if m:
                img, last_from, user = m.group(1), i, None
                if img.lower() != "scratch" and img not in stages and "@sha256:" not in img and (":" not in img.split("/")[-1] or img.endswith(":latest")):
                    out.append(mk("DOCKER-LATEST", "container_security", "low", "high", "potential", rel, rel, i, l.strip(), "config-rule",
                                  "Untagged/latest base image is non-reproducible.", "Pin the base image by version or digest.", validation="pattern_match_unverified"))
                if m.group(2): stages.add(m.group(2))
            u = re.match(r"\s*USER\s+(\S+)", l, re.I)
            if u: user = u.group(1)
        if user is None and any(re.match(r"\s*FROM\s", l, re.I) for l in lines):
            out.append(mk("DOCKER-NO-USER", "container_security", "medium", "high", "confirmed", rel, rel, last_from, lines[last_from - 1].strip(), "config-rule",
                          "No USER instruction: final stage runs as root.", "Add a non-root USER.", ["CWE-250"], validation="insecure_setting_present"))
    if "gha" in kinds:
        if re.search(r"\bpull_request_target\b", text):
            risky = "github.event.pull_request.head" in text or "github.head_ref" in text
            ln = next(i for i, l in enumerate(lines, 1) if "pull_request_target" in l)
            out.append(mk("GHA-PRT", "cicd_security", "high" if risky else "low", "medium" if risky else "high", "potential" if risky else "informational",
                          rel, rel, ln, lines[ln - 1].strip(), "config-rule",
                          "pull_request_target runs with secrets; checking out PR head code can allow secret theft." if risky else "pull_request_target trigger in use.",
                          "Do not check out/execute untrusted PR code in this trigger.", ["CWE-94"]))
        if not re.search(r"^\s*permissions:", text, re.M):
            out.append(mk("GHA-NO-PERMS", "cicd_security", "info", "high", "informational", rel, rel, 1, "no top-level permissions block", "config-rule",
                          "Token permissions fall back to repo defaults (may be write).", "Add least-privilege 'permissions:'."))
    return out

# ───────────────────────── dependencies ─────────────────────────

@dataclass
class Dep:
    ecosystem: str; name: str; version: Optional[str]; direct: Optional[bool]; manifest: str; line: int = 0
    license: Optional[str] = None; depends_on: list = field(default_factory=list); resolved: str = ""; spec: str = ""


def _py(n): return re.sub(r"[-_.]+", "-", n).lower()


def parse_requirements(text, mf):
    deps, flags = [], []
    for i, l in enumerate(text.splitlines(), 1):
        s = l.split(" #")[0].strip()
        if not s or s.startswith("#"): continue
        if re.match(r"(--extra-index-url|--index-url|-i\s|--find-links|-f\s)", s): flags.append((mf, i, s)); continue
        if s.startswith("-"): continue
        m = re.match(r"^([A-Za-z0-9][A-Za-z0-9._-]*)(?:\[[^\]]*\])?\s*(?:(===|==|~=|>=|<=|>|<|!=)\s*([^\s;,]+))?", s)
        if m: deps.append(Dep("PyPI", _py(m.group(1)), m.group(3) if m.group(2) in ("==", "===") else None, True, mf, i, spec=s))
    return deps, flags


def parse_package_json(text, mf):
    d = json.loads(text); out = []
    for sec in ("dependencies", "devDependencies", "optionalDependencies"):
        for n, v in (d.get(sec) or {}).items():
            out.append(Dep("npm", n, v if re.fullmatch(r"\d+\.\d+\.\d+[\w.+-]*", str(v)) else None, True, mf, spec=str(v)))
    return out


def parse_package_lock(text, mf, root_direct=None):
    d = json.loads(text); out = []
    if "packages" in d:
        rootp = d["packages"].get("", {})
        direct = set(rootp.get("dependencies", {})) | set(rootp.get("devDependencies", {})) | set(rootp.get("optionalDependencies", {}))
        for k, p in d["packages"].items():
            if not k or "node_modules/" not in k: continue
            n = k.rsplit("node_modules/", 1)[1]
            out.append(Dep("npm", n, p.get("version"), k == f"node_modules/{n}" and n in direct, mf, license=p.get("license") if isinstance(p.get("license"), str) else None,
                           depends_on=list((p.get("dependencies") or {}).keys()), resolved=p.get("resolved", "")))
    else:
        def walk(deps, top):
            for n, p in (deps or {}).items():
                out.append(Dep("npm", n, p.get("version"), top, mf, depends_on=list((p.get("requires") or {}).keys()), resolved=p.get("resolved", "")))
                walk(p.get("dependencies"), False)
        walk(d.get("dependencies"), True)
    return out


def parse_composer_lock(text, mf, direct_names=None):
    d = json.loads(text); out = []
    for sec in ("packages", "packages-dev"):
        for p in d.get(sec, []):
            lic = p.get("license")
            out.append(Dep("Packagist", p["name"].lower(), str(p.get("version", "")).lstrip("v") or None,
                           (p["name"].lower() in direct_names) if direct_names is not None else None, mf,
                           license=" OR ".join(lic) if isinstance(lic, list) and lic else None,
                           depends_on=[r for r in (p.get("require") or {}) if r != "php" and not r.startswith("ext-")]))
    return out


def parse_composer_json(text, mf):
    d = json.loads(text)
    return [Dep("Packagist", n.lower(), None, True, mf, spec=str(v)) for sec in ("require", "require-dev") for n, v in (d.get(sec) or {}).items() if n != "php" and not n.startswith("ext-")]


def parse_pom(text, mf):
    props = dict(re.findall(r"<([\w.\-]+)>([^<]+)</\1>", (re.search(r"<properties>(.*?)</properties>", text, re.S) or [None, ""])[1]))
    out = []
    for m in re.finditer(r"<dependency>(.*?)</dependency>", text, re.S):
        b = m.group(1)
        g, a = re.search(r"<groupId>\s*([^<\s]+)", b), re.search(r"<artifactId>\s*([^<\s]+)", b)
        v = re.search(r"<version>\s*([^<\s]+)", b)
        if not (g and a): continue
        ver = v.group(1) if v else None
        if ver and ver.startswith("${"): ver = props.get(ver[2:-1])
        out.append(Dep("Maven", f"{g.group(1)}:{a.group(1)}", ver, True, mf, text[:m.start()].count("\n") + 1))
    return out


def parse_gradle(text, mf):
    out = []
    for i, l in enumerate(text.splitlines(), 1):
        m = re.search(r"""(?:implementation|api|compile|runtimeOnly|testImplementation|compileOnly)\s*\(?\s*["']([\w.\-]+):([\w.\-]+):([^"'$)\s]+)["']""", l)
        if m: out.append(Dep("Maven", f"{m.group(1)}:{m.group(2)}", m.group(3), True, mf, i))
    return out


def parse_poetry_lock(text, mf):
    d = tomllib.loads(text)
    return [Dep("PyPI", _py(p["name"]), p.get("version"), None, mf, depends_on=[_py(x) for x in (p.get("dependencies") or {})]) for p in d.get("package", [])]


def parse_pipfile_lock(text, mf):
    d = json.loads(text)
    return [Dep("PyPI", _py(n), str(v.get("version", "")).lstrip("=") or None, None, mf) for sec in ("default", "develop") for n, v in (d.get(sec) or {}).items()]

# ── vulnerability intelligence (offline OSV records, optional online OSV) ──
ECO_OSV = {"PyPI": "PyPI", "npm": "npm", "Maven": "Maven", "Packagist": "Packagist"}
OSV_ID = re.compile(r"^[A-Za-z0-9._:-]{3,64}$")


def vkey(v: str): return tuple(int(x) for x in re.findall(r"\d+", v)[:6]) or (0,)


def is_affected(ver: str, aff: dict) -> bool:
    if ver in (aff.get("versions") or []): return True
    for r in aff.get("ranges") or []:
        if r.get("type") not in ("SEMVER", "ECOSYSTEM"): continue
        vuln = False
        for ev in r.get("events") or []:
            if "introduced" in ev: vuln = ev["introduced"] == "0" or vkey(ver) >= vkey(ev["introduced"])
            elif "fixed" in ev and vuln and vkey(ver) >= vkey(ev["fixed"]): vuln = False
            elif "last_affected" in ev and vuln and vkey(ver) > vkey(ev["last_affected"]): vuln = False
        if vuln: return True
    return False


def validate_osv(rec) -> Optional[dict]:
    if not isinstance(rec, dict) or not isinstance(rec.get("id"), str) or not OSV_ID.match(rec["id"]): return None
    if not isinstance(rec.get("affected"), list): return None
    return {"id": rec["id"], "aliases": [a for a in rec.get("aliases", []) if isinstance(a, str)][:10],
            "summary": str(rec.get("summary", ""))[:300], "affected": [a for a in rec["affected"] if isinstance(a, dict)],
            "severity": str((rec.get("database_specific") or {}).get("severity", "")).lower()}


class VulnDB:
    """Offline advisory store: directory of OSV-format JSON files. No records => reports 'unavailable'."""
    def __init__(self, directory: Optional[str] = None):
        self.idx, self.records, self.dir = {}, 0, directory
        if directory and Path(directory).is_dir():
            for f in Path(directory).glob("*.json"):
                try: data = json.loads(f.read_text())
                except (OSError, ValueError): continue
                for r in data if isinstance(data, list) else [data]:
                    v = validate_osv(r)
                    if not v: continue
                    self.records += 1
                    for a in v["affected"]:
                        p = a.get("package") or {}
                        self.idx.setdefault((p.get("ecosystem"), _py(p["name"]) if p.get("ecosystem") == "PyPI" else p.get("name")), []).append((v, a))

    def status(self): return {"status": "available" if self.records else "unavailable", "records": self.records,
                              "reason": None if self.records else "No local advisory database loaded; no vulnerability results are claimed."}

    def lookup(self, d: Dep):
        return [v for v, a in self.idx.get((ECO_OSV[d.ecosystem], d.name), []) if d.version and is_affected(d.version, a)]


def osv_online(deps: list[Dep], timeout=10):
    """Opt-in: sends ONLY ecosystem/name/version to api.osv.dev. Returns (matches{(eco,name,ver):[rec]}, status)."""
    import urllib.request
    res = {}
    try:
        cand = [d for d in deps if d.version][:500]
        body = json.dumps({"queries": [{"package": {"name": d.name, "ecosystem": ECO_OSV[d.ecosystem]}, "version": d.version} for d in cand]}).encode()
        r = json.loads(urllib.request.urlopen(urllib.request.Request("https://api.osv.dev/v1/querybatch", body, {"Content-Type": "application/json"}), timeout=timeout).read())
        for d, x in zip(cand, r.get("results", [])):
            for v in (x.get("vulns") or [])[:20]:
                if not OSV_ID.match(str(v.get("id", ""))): continue
                full = json.loads(urllib.request.urlopen(f"https://api.osv.dev/v1/vulns/{v['id']}", timeout=timeout).read())
                rec = validate_osv(full)
                if rec: res.setdefault((d.ecosystem, d.name, d.version), []).append(rec)
        return res, {"status": "ok"}
    except Exception as e:  # network or schema failure - report honestly
        return {}, {"status": "error", "reason": f"{type(e).__name__}: {str(e)[:120]}"}

POPULAR = {"PyPI": ["requests", "numpy", "pandas", "flask", "django", "urllib3", "boto3", "pyyaml", "cryptography", "pytest", "setuptools", "jinja2", "sqlalchemy", "fastapi", "pydantic"],
           "npm": ["express", "lodash", "react", "axios", "moment", "webpack", "chalk", "commander", "debug", "typescript", "jquery", "vue", "next", "eslint", "dotenv"],
           "Packagist": ["laravel/framework", "symfony/console", "guzzlehttp/guzzle", "monolog/monolog", "phpunit/phpunit"], "Maven": []}


def lev(a, b):
    p = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        c = [i]
        for j, cb in enumerate(b, 1): c.append(min(p[j] + 1, c[-1] + 1, p[j - 1] + (ca != cb)))
        p = c
    return p[-1]


DEFAULT_LICENSE_POLICY = {"deny": ["AGPL", "SSPL", "GPL-3.0", "GPL-2.0"], "review": ["LGPL", "MPL", "EPL", "CDDL", "GPL"]}


def purl(d: Dep) -> str:
    n = d.name
    if d.ecosystem == "Maven": g, a = n.split(":", 1) if ":" in n else ("", n); base = f"pkg:maven/{g}/{a}"
    elif d.ecosystem == "npm": base = "pkg:npm/" + n.replace("@", "%40", 1)
    elif d.ecosystem == "PyPI": base = f"pkg:pypi/{n}"
    else: base = f"pkg:composer/{n}"
    return base + (f"@{d.version}" if d.version else "")


def analyze_deps(deps: list[Dep], flags: list, vdb: VulnDB, cfg: dict) -> tuple[list[Finding], dict]:
    out, intel = [], {}
    online, ostat = ({}, {"status": "disabled"})
    if cfg.get("osv_online") and os.environ.get("FORGEX_ALLOW_EXTERNAL_INTEL") == "1":
        online, ostat = osv_online(deps)
    intel["local_vulnerability_db"] = vdb.status(); intel["online_osv"] = ostat
    intel["outdated_check"] = {"status": "unavailable", "reason": "No registry metadata source configured; outdated/unmaintained status is not claimed."}
    unresolved = [d for d in deps if not d.version]
    intel["unresolved_versions"] = len(unresolved)
    for d in deps:
        recs = vdb.lookup(d) if d.version else []
        recs += online.get((d.ecosystem, d.name, d.version), [])
        seen = set()
        for r in recs:
            if r["id"] in seen: continue
            seen.add(r["id"])
            sev = {"moderate": "medium"}.get(r["severity"], r["severity"]) if r["severity"] in ("low", "moderate", "medium", "high", "critical") else "unknown"
            fixed = next((e["fixed"] for a in r["affected"] for rg in a.get("ranges", []) for e in rg.get("events", []) if "fixed" in e), None)
            out.append(mk(f"DEP-VULN-{r['id']}", "vulnerable_dependency", sev, "high", "confirmed", purl(d), d.manifest, d.line or 1,
                          f"{d.name}=={d.version} matches {r['id']}: {r['summary']}", "advisory-match",
                          "Package version has a published advisory.", f"Upgrade to >= {fixed}." if fixed else "No fixed version in record; evaluate mitigations/replacement.",
                          [r["id"], *r["aliases"]], "Direct dependency." if d.direct else "Transitive dependency - check reachability.", "advisory_record_matched",
                          ))
        if d.resolved.startswith("http://"):
            out.append(mk("DEP-INSECURE-RESOLVED", "dependency_confusion_risk", "high", "high", "confirmed", purl(d), d.manifest, 1, f"resolved over cleartext: {d.resolved[:120]}",
                          "lockfile-analysis", "Package fetched over HTTP can be tampered with.", "Use https registry URLs.", ["CWE-494"], validation="insecure_setting_present"))
        elif d.resolved and not d.resolved.startswith("https://registry.npmjs.org/") and not d.name.startswith("@"):
            out.append(mk("DEP-NONDEFAULT-REGISTRY", "dependency_confusion_risk", "medium", "low", "potential", purl(d), d.manifest, 1, f"unscoped package resolved from {d.resolved[:120]}",
                          "lockfile-analysis", "Unscoped package from non-default registry is a dependency-confusion indicator.", "Scope internal packages and pin registry mapping.", ["CWE-427"]))
        if d.direct and d.name not in POPULAR.get(d.ecosystem, []):
            for p in POPULAR.get(d.ecosystem, []):
                if len(p) >= 5 and lev(d.name, p) == 1:
                    out.append(mk("DEP-TYPOSQUAT", "typosquatting_signal", "low", "low", "potential", purl(d), d.manifest, d.line or 1, f"'{d.name}' is 1 edit away from popular '{p}'",
                                  "name-similarity", "Possible typosquat; name similarity alone is NOT proof.", "Verify the package publisher and intended name.", ["CWE-1357"]))
                    break
    for mf, ln, txt in flags:
        out.append(mk("DEP-EXTRA-INDEX", "dependency_confusion_risk", "medium", "medium", "potential", mf, mf, ln, txt[:200], "manifest-analysis",
                      "Extra package index allows a public package to shadow an internal name.", "Use a single index or pin hashes (--require-hashes).", ["CWE-427"]))
    for mf in sorted({d.manifest for d in deps if d.ecosystem == "PyPI" and not d.version and mf_is_req(d.manifest)}):
        n = sum(1 for d in deps if d.manifest == mf and not d.version)
        out.append(mk("DEP-UNPINNED", "dependency_hygiene", "info", "high", "informational", mf, mf, 1, f"{n} dependencies not pinned to an exact version", "manifest-analysis",
                      "Unpinned versions make builds non-reproducible and vulnerability matching impossible.", "Pin versions / use a lockfile."))
    pol = {**DEFAULT_LICENSE_POLICY, **(cfg.get("license_policy") or {})}
    nolic = 0
    for d in deps:
        if not d.license: nolic += 1; continue
        up = d.license.upper()
        lvl = "deny" if any(x.upper() in up for x in pol["deny"]) else "review" if any(x.upper() in up for x in pol["review"]) else None
        if lvl:
            out.append(mk(f"LIC-{lvl.upper()}", "license_policy", "high" if lvl == "deny" else "low", "high", "confirmed" if lvl == "deny" else "informational", purl(d), d.manifest, 1,
                          f"{d.name} license: {d.license}", "license-policy", f"License '{d.license}' is on the {lvl} list.", "Replace the dependency or obtain legal review."))
    intel["license_data"] = {"status": "partial" if nolic else "complete", "packages_without_license": nolic, "total": len(deps),
                             "note": "Licenses come only from lockfile metadata; missing = unknown, not 'no license'."}
    return out, intel


def mf_is_req(m): return "requirements" in m.lower()

# ───────────────────────── SBOM ─────────────────────────

def dedupe_deps(deps):
    best = {}
    for d in deps:
        k = (d.ecosystem, d.name, d.version)
        if d.version is None and any(x.version for x in deps if (x.ecosystem, x.name) == (d.ecosystem, d.name)): continue
        if k not in best or (d.direct and not best[k].direct): best[k] = d
    return list(best.values())


def build_cyclonedx(repo_id, scan_id, repo_version, deps):
    idx = {}
    for d in deps: idx.setdefault((d.ecosystem, d.name), purl(d))
    comps = [{"type": "library", "bom-ref": purl(d), "name": d.name, "version": d.version or "", "purl": purl(d),
              **({"licenses": [{"expression": d.license}]} if d.license else {}),
              "properties": [{"name": "forgex:ecosystem", "value": d.ecosystem}, {"name": "forgex:direct", "value": {True: "true", False: "false", None: "unknown"}[d.direct]},
                             {"name": "forgex:manifest", "value": d.manifest}]} for d in deps]
    rel = [{"ref": purl(d), "dependsOn": sorted({idx[(d.ecosystem, n)] for n in d.depends_on if (d.ecosystem, n) in idx})} for d in deps]
    rel.append({"ref": f"repo:{repo_id}", "dependsOn": [purl(d) for d in deps if d.direct]})
    return {"bomFormat": "CycloneDX", "specVersion": "1.5", "serialNumber": f"urn:uuid:{uuid.uuid4()}", "version": 1,
            "metadata": {"timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "tools": {"components": [{"type": "application", "name": TOOL_VERSION}]},
                         "component": {"type": "application", "bom-ref": f"repo:{repo_id}", "name": repo_id, "version": repo_version},
                         "properties": [{"name": "forgex:scan_id", "value": scan_id}, {"name": "forgex:repo_version", "value": repo_version}]},
            "components": comps, "dependencies": rel}


def build_spdx(repo_id, scan_id, repo_version, deps):
    sid = lambda d: "SPDXRef-" + re.sub(r"[^A-Za-z0-9.-]", "-", purl(d))
    return {"spdxVersion": "SPDX-2.3", "dataLicense": "CC0-1.0", "SPDXID": "SPDXRef-DOCUMENT", "name": f"{repo_id}@{repo_version}",
            "documentNamespace": f"https://forgex.local/spdx/{repo_id}/{scan_id}/{uuid.uuid4()}",
            "creationInfo": {"created": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "creators": [f"Tool: {TOOL_VERSION}"]},
            "packages": [{"SPDXID": sid(d), "name": d.name, "versionInfo": d.version or "NOASSERTION", "downloadLocation": "NOASSERTION",
                          "licenseConcluded": d.license or "NOASSERTION", "comment": f"ecosystem={d.ecosystem}; direct={d.direct}; manifest={d.manifest}",
                          "externalRefs": [{"referenceCategory": "PACKAGE-MANAGER", "referenceType": "purl", "referenceLocator": purl(d)}]} for d in deps],
            "relationships": [{"spdxElementId": sid(d), "relationshipType": "DEPENDS_ON", "relatedSpdxElement": sid(t)}
                              for d in deps for t in deps if t.ecosystem == d.ecosystem and t.name in d.depends_on]}


PURL_ECO = {"pypi": "PyPI", "npm": "npm", "maven": "Maven", "composer": "Packagist"}


def parse_purl(p: str):
    m = re.match(r"^pkg:([a-z]+)/(.+?)(?:@([^?#]+))?(?:[?#].*)?$", p or "")
    if not m or m.group(1) not in PURL_ECO: return None
    name = m.group(2).replace("%40", "@")
    if m.group(1) == "maven": name = name.replace("/", ":", 1)
    return PURL_ECO[m.group(1)], name, m.group(3)


def parse_sbom(doc: dict) -> list[Dep]:
    if not isinstance(doc, dict): raise ValueError("SBOM must be a JSON object")
    out = []
    if doc.get("bomFormat") == "CycloneDX":
        comps = doc.get("components") or []
        if len(comps) > 100_000: raise ValueError("SBOM too large")
        byref, deps = {}, {}
        for c in comps:
            pp = parse_purl(c.get("purl", ""))
            if not pp: continue
            props = {p.get("name"): p.get("value") for p in c.get("properties", []) if isinstance(p, dict)}
            lic = c.get("licenses") or []
            l = (lic[0].get("expression") or (lic[0].get("license") or {}).get("id")) if lic and isinstance(lic[0], dict) else None
            d = Dep(pp[0], pp[1], pp[2] or c.get("version") or None, {"true": True, "false": False}.get(props.get("forgex:direct")), props.get("forgex:manifest", ""), license=l)
            byref[c.get("bom-ref", c.get("purl"))] = d; out.append(d)
        for r in doc.get("dependencies", []):
            if r.get("ref") in byref: byref[r["ref"]].depends_on = [byref[x].name for x in r.get("dependsOn", []) if x in byref]
    elif str(doc.get("spdxVersion", "")).startswith("SPDX-"):
        byid = {}
        for p in doc.get("packages", []):
            ref = next((e["referenceLocator"] for e in p.get("externalRefs", []) if e.get("referenceType") == "purl"), None)
            pp = parse_purl(ref)
            if not pp: continue
            lic = p.get("licenseConcluded")
            d = Dep(pp[0], pp[1], pp[2], None, "", license=None if lic in (None, "NOASSERTION") else lic)
            byid[p["SPDXID"]] = d; out.append(d)
        for r in doc.get("relationships", []):
            if r.get("relationshipType") == "DEPENDS_ON" and r.get("spdxElementId") in byid and r.get("relatedSpdxElement") in byid:
                byid[r["spdxElementId"]].depends_on.append(byid[r["relatedSpdxElement"]].name)
    else:
        raise ValueError("unsupported SBOM format (expected CycloneDX or SPDX JSON)")
    return out

# ───────────────────────── correlation / scoring ─────────────────────────

OWASP_API = {"ssrf": "OWASP-API7:2023", "security_misconfiguration": "OWASP-API8:2023", "insecure_authentication": "OWASP-API2:2023",
             "authorization_weakness": "OWASP-API1/5:2023 (verify)", "insecure_error_handling": "OWASP-API8:2023", "tls_configuration": "OWASP-API8:2023"}


def correlate(findings: list[Finding]) -> list[Finding]:
    uniq = {f.id: f for f in findings}
    byfile = {}
    for f in uniq.values(): byfile.setdefault(f.file, []).append(f)
    for fs in byfile.values():
        for f in fs:
            f.related = [g.id for g in fs if g.id != f.id and g.rule_id != f.rule_id and abs(g.line - f.line) <= 3][:8]
    for f in uniq.values():
        if f.category in OWASP_API and OWASP_API[f.category] not in f.references: f.references.append(OWASP_API[f.category])
    return sorted(uniq.values(), key=lambda f: (-SEV_W[f.severity] * CONF_W[f.confidence], f.file, f.line))


def summarize(findings: list[Finding]) -> dict:
    act = [f for f in findings if f.bucket in ("confirmed", "potential")]
    matrix = {b: {} for b in BUCKETS}
    for f in findings: matrix[f.bucket][f.severity] = matrix[f.bucket].get(f.severity, 0) + 1
    top = sorted(act, key=lambda f: -SEV_W[f.severity] * CONF_W[f.confidence])[:10]
    worst = max((f.severity for f in act), key=lambda s: SEV_W[s], default="none")
    return {"total": len(findings), "by_bucket_severity": matrix, "highest_severity": worst,
            "critical_confirmed": sum(1 for f in findings if f.severity == "critical" and f.bucket == "confirmed"),
            "critical_potential": sum(1 for f in findings if f.severity == "critical" and f.bucket == "potential"),
            "risk_points": round(sum(SEV_W[f.severity] * CONF_W[f.confidence] for f in act), 1),
            "top_contributors": [{"id": f.id, "rule": f.rule_id, "points": round(SEV_W[f.severity] * CONF_W[f.confidence], 1)} for f in top],
            "scoring_note": "risk_points = sum(severity weight x confidence weight) over confirmed+potential findings. It is NOT an average; "
                            "highest_severity and critical counts are always reported separately. Not a compliance assessment (incl. OWASP API Top 10)."}

# ───────────────────────── persistence ─────────────────────────

SCHEMA = """
CREATE TABLE IF NOT EXISTS sec_scans(id TEXT PRIMARY KEY, repo_id TEXT, path TEXT, repo_version TEXT, status TEXT, error TEXT, started REAL, finished REAL, provenance TEXT, summary TEXT);
CREATE TABLE IF NOT EXISTS sec_findings(scan_id TEXT, id TEXT, severity TEXT, bucket TEXT, category TEXT, file TEXT, data TEXT, PRIMARY KEY(scan_id,id));
CREATE TABLE IF NOT EXISTS sec_deps(scan_id TEXT, purl TEXT, data TEXT);
CREATE TABLE IF NOT EXISTS sec_sboms(id TEXT PRIMARY KEY, repo_id TEXT, scan_id TEXT, repo_version TEXT, fmt TEXT, created REAL, data TEXT);
CREATE TABLE IF NOT EXISTS sec_plans(id TEXT PRIMARY KEY, finding_id TEXT, scan_id TEXT, status TEXT, data TEXT, created REAL);
CREATE TABLE IF NOT EXISTS sec_history(n INTEGER PRIMARY KEY AUTOINCREMENT, finding_id TEXT, scan_id TEXT, event TEXT, actor TEXT, detail TEXT, ts REAL);
CREATE TABLE IF NOT EXISTS sec_audit(n INTEGER PRIMARY KEY AUTOINCREMENT, ts REAL, actor TEXT, action TEXT, detail TEXT);
CREATE INDEX IF NOT EXISTS i_f ON sec_findings(scan_id,severity,category);
"""


class Store:
    def __init__(self, path: str):
        self.path, self.lock = path, threading.Lock()
        with closing(sqlite3.connect(path)) as c: c.executescript(SCHEMA); c.commit()

    def q(self, sql, a=()):
        with self.lock, closing(sqlite3.connect(self.path)) as c:
            c.row_factory = sqlite3.Row
            return [dict(r) for r in c.execute(sql, a).fetchall()]

    def x(self, sql, a=(), many=False):
        with self.lock, closing(sqlite3.connect(self.path)) as c:
            (c.executemany if many else c.execute)(sql, a); c.commit()

    def audit(self, actor, action, detail=""): self.x("INSERT INTO sec_audit(ts,actor,action,detail) VALUES(?,?,?,?)", (time.time(), actor, action, detail[:500]))
    def history(self, fid, sid, event, actor, detail=""): self.x("INSERT INTO sec_history(finding_id,scan_id,event,actor,detail,ts) VALUES(?,?,?,?,?,?)", (fid, sid, event, actor, detail[:1000], time.time()))

# ───────────────────────── engine ─────────────────────────

MANIFESTS = re.compile(r"^(requirements[\w.-]*\.txt|package\.json|package-lock\.json|composer\.json|composer\.lock|pom\.xml|build\.gradle(?:\.kts)?|poetry\.lock|Pipfile\.lock)$")


class SecurityEngine:
    def __init__(self, store: Store, repos_root: Optional[str] = None, advisory_dir: Optional[str] = None, config: Optional[dict] = None):
        self.store, self.repos_root, self.cfg = store, repos_root, config or {}
        self.vdb = VulnDB(advisory_dir)

    def begin(self, repo_id: str, path: str, actor="system") -> str:
        root = resolve_repo(path, self.repos_root)
        sid = "S-" + uuid.uuid4().hex[:12]
        self.store.x("INSERT INTO sec_scans(id,repo_id,path,status,started) VALUES(?,?,?,?,?)", (sid, repo_id, str(root), "running", time.time()))
        self.store.audit(actor, "scan_started", f"{sid} repo={repo_id}")
        return sid

    def scan(self, scan_id: str, repo_id: str, path: str, cfg: Optional[dict] = None) -> dict:
        cfg = {**self.cfg, **(cfg or {})}
        try:
            root = resolve_repo(path, self.repos_root)
            findings, deps, flags, tree, nfiles = [], [], [], hashlib.sha256(), 0
            dirs_with_lock, composer_direct = set(), {}
            files = []
            for p, rel in safe_walk(root):
                data = p.read_bytes()
                tree.update(rel.encode()); tree.update(hashlib.sha256(data).digest())
                if b"\0" in data[:4096]: continue
                files.append((rel, data.decode("utf-8", "ignore")))
            nfiles = len(files)
            for rel, text in files:  # composer.json first so lock parsing knows direct deps
                if rel.rsplit("/", 1)[-1] == "composer.json":
                    try: composer_direct[rel.rsplit("/", 1)[0] if "/" in rel else ""] = {d.name for d in parse_composer_json(text, rel)}
                    except ValueError: pass
            for rel, text in files:
                lines, base = text.splitlines(), rel.rsplit("/", 1)[-1]
                kinds = config_kinds(rel, text)
                lang = EXT.get(os.path.splitext(base)[1].lower())
                is_cfg = bool(kinds) or base.lower().endswith((".properties", ".ini", ".env", ".yml", ".yaml", ".toml", ".cfg"))
                findings += scan_secrets(rel, lines, cfg, is_cfg)
                if lang: findings += scan_sast(rel, lines, lang)
                if kinds: findings += scan_config(rel, lines, text, kinds)
                if MANIFESTS.match(base):
                    try:
                        if base.startswith("requirements"): d, f = parse_requirements(text, rel); deps += d; flags += f
                        elif base == "package.json": deps += parse_package_json(text, rel)
                        elif base == "package-lock.json": deps += parse_package_lock(text, rel)
                        elif base == "composer.json": deps += parse_composer_json(text, rel)
                        elif base == "composer.lock": deps += parse_composer_lock(text, rel, composer_direct.get(rel.rsplit("/", 1)[0] if "/" in rel else ""))
                        elif base == "pom.xml": deps += parse_pom(text, rel)
                        elif base.startswith("build.gradle"): deps += parse_gradle(text, rel)
                        elif base == "poetry.lock": deps += parse_poetry_lock(text, rel)
                        elif base == "Pipfile.lock": deps += parse_pipfile_lock(text, rel)
                    except (ValueError, KeyError, TypeError, tomllib.TOMLDecodeError) as e:
                        findings.append(mk("PARSE-ERR", "scan_coverage", "info", "high", "informational", rel, rel, 1, f"could not parse manifest: {type(e).__name__}", "parser", "Dependencies in this manifest were NOT analyzed.", "Fix manifest syntax and rescan."))
                    if "pom.xml" == base:
                        for m in re.finditer(r"<url>\s*(http://[^<\s]+)", text):
                            findings.append(mk("DEP-HTTP-REPO", "dependency_confusion_risk", "medium", "high", "confirmed", rel, rel, text[:m.start()].count("\n") + 1, m.group(1)[:120], "manifest-analysis", "Maven repository over HTTP is vulnerable to tampering.", "Use https repository URLs."))
            deps = dedupe_deps(deps)
            dfind, intel = analyze_deps(deps, flags, self.vdb, cfg)
            findings = correlate(findings + dfind)
            gh = git_head(root)
            repo_version = f"git:{gh}" if gh else f"tree:{tree.hexdigest()[:16]}"
            summ = summarize(findings); summ.update(files_scanned=nfiles, dependencies=len(deps), intelligence=intel)
            prov = {"tool": TOOL_VERSION, "python": sys.version.split()[0], "rules": {"sast": len(SAST), "config": len(CONF_RULES), "secret": len(SECRET_PATTERNS)},
                    "advisory_records": self.vdb.records, "mode": "read-only static analysis; no repository code executed", "repo_version": repo_version}
            st = self.store
            st.x("INSERT OR REPLACE INTO sec_findings VALUES(?,?,?,?,?,?,?)", [(scan_id, f.id, f.severity, f.bucket, f.category, f.file, json.dumps(asdict(f))) for f in findings], many=True)
            st.x("INSERT INTO sec_deps VALUES(?,?,?)", [(scan_id, purl(d), json.dumps(asdict(d))) for d in deps], many=True)
            st.x("UPDATE sec_scans SET status='completed',repo_version=?,finished=?,provenance=?,summary=? WHERE id=?", (repo_version, time.time(), json.dumps(prov), json.dumps(summ), scan_id))
            st.audit("system", "scan_completed", f"{scan_id} findings={len(findings)}")
            return summ
        except Exception as e:
            self.store.x("UPDATE sec_scans SET status='failed',error=?,finished=? WHERE id=?", (f"{type(e).__name__}: {str(e)[:300]}", time.time(), scan_id))
            raise

    # queries
    def findings(self, scan_id, **flt):
        sql, a = "SELECT data FROM sec_findings WHERE scan_id=?", [scan_id]
        for k in ("severity", "bucket", "category", "file"):
            if flt.get(k): sql += f" AND {k}=?"; a.append(flt[k])
        return [json.loads(r["data"]) for r in self.store.q(sql, a)]

    def latest(self, repo_id=None):
        r = self.store.q("SELECT id FROM sec_scans WHERE status='completed'" + (" AND repo_id=?" if repo_id else "") + " ORDER BY finished DESC LIMIT 1", [repo_id] if repo_id else [])
        return r[0]["id"] if r else None

    def deps(self, scan_id): return [json.loads(r["data"]) for r in self.store.q("SELECT data FROM sec_deps WHERE scan_id=?", (scan_id,))]

    def compare(self, base, head):
        a = {f["id"]: f for f in self.findings(base)}; b = {f["id"]: f for f in self.findings(head)}
        return {"base": base, "head": head, "new": [b[i] for i in b.keys() - a.keys()], "resolved": [a[i] for i in a.keys() - b.keys()], "unchanged": len(a.keys() & b.keys())}

    def generate_sbom(self, repo_id, fmt="cyclonedx", scan_id=None, actor="system"):
        sid = scan_id or self.latest(repo_id)
        if not sid: raise LookupError("no completed scan for repository; run a scan first")
        sc = self.store.q("SELECT repo_version FROM sec_scans WHERE id=?", (sid,))[0]
        deps = [Dep(**d) for d in self.deps(sid)]
        doc = (build_cyclonedx if fmt == "cyclonedx" else build_spdx)(repo_id, sid, sc["repo_version"], deps)
        bid = "B-" + uuid.uuid4().hex[:10]
        self.store.x("INSERT INTO sec_sboms VALUES(?,?,?,?,?,?,?)", (bid, repo_id, sid, sc["repo_version"], fmt, time.time(), json.dumps(doc)))
        self.store.audit(actor, "sbom_generated", f"{bid} {fmt} scan={sid}")
        return doc

    def report(self, scan_id):
        sc = self.store.q("SELECT * FROM sec_scans WHERE id=?", (scan_id,))
        if not sc: raise LookupError("scan not found")
        sc = sc[0]; fs = self.findings(scan_id)
        return {"scan": {k: sc[k] for k in ("id", "repo_id", "repo_version", "status", "started", "finished")}, "provenance": json.loads(sc["provenance"] or "{}"),
                "summary": json.loads(sc["summary"] or "{}"), "findings": {b: [f for f in fs if f["bucket"] == b] for b in BUCKETS},
                "dependency_count": len(self.deps(scan_id)),
                "limitations": ["Static pattern analysis: findings are candidates unless marked confirmed; data flow is not proven.",
                                "Vulnerability results exist only for loaded advisory records; 'unavailable' means NOT checked, not 'safe'.",
                                "Secret liveness is not tested; secrets are redacted and never transmitted.", "Not an OWASP or other compliance assessment."]}

# ───────────────────────── remediation integration ─────────────────────────

PLAN_TEMPLATES = {
    "secret": ["Confirm whether the credential is live (owner action, with provider console).", "MANUAL: rotate/revoke at provider - FORGEX never rotates credentials.",
               "Patch workspace: replace literal with env/secret-manager reference.", "Purge from git history if committed (maintainer decision).", "Add pre-commit secret scanning."],
    "dependency": ["Verify advisory applicability and reachability.", "Patch workspace: bump to fixed version and regenerate lockfile.", "Run regression suite (Module 05).", "Review diff and release notes."],
    "default": ["Reproduce / review evidence and confidence.", "Patch workspace: apply the recommended fix.", "Re-run security scan and regression suite (Module 05).", "Human review of the diff."],
}


class RemediationService:
    """Integrates Module 02 (plans), 03 (isolated patches), 05 (validation) via injected callables. Never modifies the original repo."""
    def __init__(self, engine: SecurityEngine, plan_hook: Optional[Callable] = None, patch_hook: Optional[Callable] = None, regression_hook: Optional[Callable] = None):
        self.e, self.plan_hook, self.patch_hook, self.regression_hook = engine, plan_hook, patch_hook, regression_hook

    def create_plan(self, finding_id, scan_id=None, actor="system"):
        sid = scan_id or self.e.latest()
        f = next((x for x in self.e.findings(sid) if x["id"] == finding_id), None) if sid else None
        if not f: raise LookupError("finding not found")
        kind = "secret" if f["rule_id"].startswith("SEC-") else "dependency" if f["category"] == "vulnerable_dependency" else "default"
        plan = {"title": f"Remediate {f['rule_id']} in {f['file']}:{f['line']}", "finding_id": finding_id, "scan_id": sid, "severity": f["severity"], "confidence": f["confidence"],
                "evidence": f["evidence"], "recommendation": f["remediation"], "steps": PLAN_TEMPLATES[kind],
                "workflow": ["detect", "evidence", "risk", "task", "engineering_plan", "isolated_patch", "security+regression_checks", "human_review", "record_approval"],
                "forbidden_automation": ["rotate_credentials", "modify_production_infrastructure", "modify_original_repository"], "requires_human_approval": True}
        ext = self.plan_hook(plan) if self.plan_hook else None
        pid = "P-" + uuid.uuid4().hex[:10]
        self.e.store.x("INSERT INTO sec_plans VALUES(?,?,?,?,?,?)", (pid, finding_id, sid, "pending_review", json.dumps(plan), time.time()))
        self.e.store.history(finding_id, sid, "plan_created", actor, f"plan={pid} module02_ref={ext}")
        self.e.store.audit(actor, "plan_created", pid)
        return {"plan_id": pid, "status": "pending_review", "module02_ref": ext, "plan": plan}

    def request_patch(self, plan_id, actor="system"):
        if not self.patch_hook: return {"status": "module03_not_wired"}
        p = self.e.store.q("SELECT * FROM sec_plans WHERE id=?", (plan_id,))[0]
        res = self.patch_hook(json.loads(p["data"]))  # must operate in an isolated workspace
        self.e.store.history(p["finding_id"], p["scan_id"], "patch_prepared", actor, str(res)[:500])
        return res

    def record_review(self, finding_id, decision, actor, note="", scan_id=None):
        if decision not in ("approved", "rejected", "needs_changes"): raise ValueError("invalid decision")
        self.e.store.history(finding_id, scan_id, f"review_{decision}", actor, note)
        self.e.store.x("UPDATE sec_plans SET status=? WHERE finding_id=?", (decision, finding_id))
        self.e.store.audit(actor, f"review_{decision}", finding_id)

    def history(self, finding_id): return self.e.store.q("SELECT event,actor,detail,ts FROM sec_history WHERE finding_id=? ORDER BY n", (finding_id,))


def ingest_external_findings(engine: SecurityEngine, scan_id: str, items: list[dict], source="module07") -> int:
    """Import findings from other FORGEX modules (e.g. Module 07 API/webhook security) into a scan."""
    rows = []
    for it in items:
        f = mk(str(it.get("rule_id", "EXT"))[:60], str(it.get("category", "api_security"))[:60], it.get("severity", "medium") if it.get("severity") in SEV_W else "medium",
               it.get("confidence", "low") if it.get("confidence") in CONF_W else "low", "potential", str(it.get("component", ""))[:200], str(it.get("file", ""))[:300],
               int(it.get("line", 1) or 1), scrub(str(it.get("evidence", ""))), f"imported:{source}", str(it.get("impact", ""))[:300], str(it.get("remediation", ""))[:300],
               it.get("references", [])[:10], source=source)
        rows.append((scan_id, f.id, f.severity, f.bucket, f.category, f.file, json.dumps(asdict(f))))
    engine.store.x("INSERT OR REPLACE INTO sec_findings VALUES(?,?,?,?,?,?,?)", rows, many=True)
    return len(rows)

# ───────────────────────── FastAPI router ─────────────────────────

def build_router(engine: SecurityEngine, remediation: Optional[RemediationService] = None):
    from typing import Literal
    from fastapi import APIRouter, BackgroundTasks, Header, HTTPException
    from fastapi.responses import JSONResponse
    from pydantic import BaseModel

    rem = remediation or RemediationService(engine)
    r = APIRouter(prefix="/api/v1/security", tags=["security"])

    class ScanReq(BaseModel): repo_id: str; path: str; config: Optional[dict] = None
    class SbomReq(BaseModel): repo_id: str; format: Literal["cyclonedx", "spdx"] = "cyclonedx"; scan_id: Optional[str] = None
    class PlanReq(BaseModel): scan_id: Optional[str] = None

    def nf(e): return HTTPException(404, str(e))

    @r.post("/scans", status_code=202)
    def start_scan(req: ScanReq, bg: BackgroundTasks, x_forgex_user: str = Header("system")):
        try: sid = engine.begin(req.repo_id, req.path, x_forgex_user)
        except PermissionError as e: raise HTTPException(403, str(e))
        except (ValueError, OSError) as e: raise HTTPException(400, str(e))
        cfg = {k: v for k, v in (req.config or {}).items() if k in ("allowlist", "license_policy", "osv_online")}
        bg.add_task(lambda: _safe(engine.scan, sid, req.repo_id, req.path, cfg))
        return {"scan_id": sid, "status": "running"}

    def _safe(fn, *a):
        try: fn(*a)
        except Exception: pass  # failure is persisted on the scan row

    @r.get("/scans")
    def list_scans(repo_id: Optional[str] = None, limit: int = 50):
        return engine.store.q("SELECT id,repo_id,repo_version,status,started,finished,error FROM sec_scans" + (" WHERE repo_id=?" if repo_id else "") + " ORDER BY started DESC LIMIT ?", ([repo_id] if repo_id else []) + [min(limit, 200)])

    @r.get("/scans/compare")
    def compare(base: str, head: str): return engine.compare(base, head)

    @r.get("/scans/{scan_id}")
    def get_scan(scan_id: str):
        s = engine.store.q("SELECT * FROM sec_scans WHERE id=?", (scan_id,))
        if not s: raise HTTPException(404, "scan not found")
        s = s[0]; s["provenance"] = json.loads(s["provenance"] or "{}"); s["summary"] = json.loads(s["summary"] or "{}"); return s

    @r.get("/findings")
    def list_findings(scan_id: Optional[str] = None, severity: Optional[str] = None, bucket: Optional[str] = None, category: Optional[str] = None, file: Optional[str] = None, limit: int = 200, offset: int = 0):
        sid = scan_id or engine.latest()
        if not sid: return {"scan_id": None, "findings": [], "total": 0}
        fs = engine.findings(sid, severity=severity, bucket=bucket, category=category, file=file)
        return {"scan_id": sid, "total": len(fs), "findings": fs[offset:offset + min(limit, 1000)]}

    @r.get("/findings/{finding_id}")
    def get_finding(finding_id: str, scan_id: Optional[str] = None):
        sid = scan_id or engine.latest()
        f = next((x for x in engine.findings(sid) if x["id"] == finding_id), None) if sid else None
        if not f: raise HTTPException(404, "finding not found")
        return {**f, "history": rem.history(finding_id)}

    @r.get("/dependencies")
    def dependencies(scan_id: Optional[str] = None):
        sid = scan_id or engine.latest()
        return {"scan_id": sid, "dependencies": engine.deps(sid) if sid else [], "intelligence": json.loads((engine.store.q("SELECT summary FROM sec_scans WHERE id=?", (sid,)) or [{"summary": "{}"}])[0]["summary"] or "{}").get("intelligence", {}) if sid else {}}

    @r.get("/sbom/{repository_id}")
    def get_sbom(repository_id: str, format: Optional[str] = None):
        q = engine.store.q("SELECT data,scan_id,repo_version,fmt FROM sec_sboms WHERE repo_id=?" + (" AND fmt=?" if format else "") + " ORDER BY created DESC LIMIT 1", [repository_id] + ([format] if format else []))
        if not q: raise HTTPException(404, "no SBOM generated for this repository")
        return JSONResponse(json.loads(q[0]["data"]), headers={"Content-Disposition": f'attachment; filename="sbom-{repository_id}-{q[0]["fmt"]}.json"'})

    @r.post("/sbom/generate")
    def gen_sbom(req: SbomReq, x_forgex_user: str = Header("system")):
        try: return engine.generate_sbom(req.repo_id, req.format, req.scan_id, x_forgex_user)
        except LookupError as e: raise nf(e)

    @r.get("/secrets")
    def secrets(scan_id: Optional[str] = None):
        sid = scan_id or engine.latest()
        fs = [f for f in (engine.findings(sid) if sid else []) if f["rule_id"].startswith("SEC-")]
        return {"scan_id": sid, "secrets": fs, "note": "Values are redacted; never returned or stored in clear text."}

    @r.get("/configuration")
    def configuration(scan_id: Optional[str] = None):
        cats = {"container_security", "infrastructure_misconfiguration", "cicd_security", "insecure_network_exposure", "tls_configuration", "debug_configuration", "security_misconfiguration"}
        sid = scan_id or engine.latest()
        return {"scan_id": sid, "findings": [f for f in (engine.findings(sid) if sid else []) if f["category"] in cats]}

    @r.get("/reports/{scan_id}")
    def report(scan_id: str, x_forgex_user: str = Header("system")):
        try: rep = engine.report(scan_id)
        except LookupError as e: raise nf(e)
        engine.store.audit(x_forgex_user, "report_downloaded", scan_id)
        return JSONResponse(rep, headers={"Content-Disposition": f'attachment; filename="security-report-{scan_id}.json"'})

    @r.post("/findings/{finding_id}/create-plan")
    def create_plan(finding_id: str, req: PlanReq = PlanReq(), x_forgex_user: str = Header("system")):
        try: return rem.create_plan(finding_id, req.scan_id, x_forgex_user)
        except LookupError as e: raise nf(e)

    return r
