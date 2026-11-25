"""Ninguna credencial en el código: se revisa cada archivo versionado (y lo nuevo aún sin commit) en cada CI."""

import ast
import re
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
FILES = [f for f in subprocess.run(["git", "ls-files", "-co", "--exclude-standard"], cwd=ROOT, capture_output=True, text=True, check=True)
         .stdout.splitlines() if (ROOT / f).is_file() and "/.next/" not in f and not re.search(r"(package-lock\.json|uv\.lock)$", f)]
PATTERNS = {
    "clave privada": r"-----BEGIN (?:RSA |EC |OPENSSH |DSA |PGP )?PRIVATE KEY",
    "OpenAI/Anthropic": r"\bsk-(?:ant-|proj-)?[A-Za-z0-9_-]{20,}",
    "AWS": r"\bAKIA[0-9A-Z]{16}\b", "Google": r"\bAIza[0-9A-Za-z_-]{35}\b", "GitHub": r"\bgh[pousr]_[A-Za-z0-9]{30,}\b",
    "Groq": r"\bgsk_[A-Za-z0-9]{30,}\b", "Hugging Face": r"\bhf_[A-Za-z0-9]{30,}\b", "Resend": r"\bre_[A-Za-z0-9]{8,}_[A-Za-z0-9]{16,}\b",
    "Supabase": r"\bsb(?:p|_secret)_[A-Za-z0-9_]{20,}\b", "Slack": r"\bxox[abprs]-[A-Za-z0-9-]{10,}\b",
    "JWT": r"\beyJ[A-Za-z0-9_-]{15,}\.eyJ[A-Za-z0-9_-]{15,}\.[A-Za-z0-9_-]{10,}",
    "URL con contraseña": r"\b(?:postgres(?:ql)?(?:\+\w+)?|redis|amqp|mysql|mongodb(?:\+srv)?)://[^\s:/@'\"$<{…]+:(?!\*|\$|<|\{|…|contrase|password|clave|user|x@|PASS)[^\s@'\"$<{…]{6,}@",
}
SENSITIVE_NAME = re.compile(r"(api_?key|secret_?key|secret|password|passwd|token|credential)s?$", re.IGNORECASE)
PRODUCTION = [f for f in FILES if f.endswith(".py") and not re.search(r"(^|/)tests?/", f)]


def test_no_tracked_file_contains_a_known_credential_format():
    found = []
    for name in FILES:
        text = (ROOT / name).read_bytes()
        if b"\x00" in text[:4096] or name == "tests/security/test_no_secrets.py":
            continue
        text = text.decode("utf-8", errors="ignore")
        found += [f"{name}: {kind}" for kind, pattern in PATTERNS.items() if re.search(pattern, text)]
    assert found == []


def _target(node) -> str:
    if isinstance(node, ast.Subscript):  # os.environ["SECRET_KEY"] = "…"
        return node.slice.value if isinstance(node.slice, ast.Constant) and isinstance(node.slice.value, str) else ""
    return getattr(node, "id", getattr(node, "attr", ""))


def _literal_secrets(path: str) -> list[str]:
    found = []
    for node in ast.walk(ast.parse((ROOT / path).read_text(encoding="utf-8"))):
        pairs = []
        if isinstance(node, ast.Assign):
            pairs = [(_target(t), node.value) for t in node.targets]
        elif isinstance(node, ast.AnnAssign):
            pairs = [(getattr(node.target, "id", ""), node.value)]
        elif isinstance(node, ast.keyword):
            pairs = [(node.arg or "", node.value)]
        elif isinstance(node, ast.Dict):
            pairs = [(k.value if isinstance(k, ast.Constant) and isinstance(k.value, str) else "", v) for k, v in zip(node.keys, node.values)]
        elif isinstance(node, ast.Call) and getattr(node.func, "attr", "") in ("get", "getenv") and len(node.args) == 2:
            first = node.args[0]
            pairs = [(first.value if isinstance(first, ast.Constant) and isinstance(first.value, str) else "", node.args[1])]
        for name, value in pairs:
            if SENSITIVE_NAME.search(name) and isinstance(value, ast.Constant) and isinstance(value.value, str) and len(value.value) >= 6:
                found.append(f"{path}:{node.lineno if hasattr(node, 'lineno') else value.lineno} {name}")
    return found


def test_no_credential_is_assigned_or_defaulted_to_a_literal_in_production_code():
    assert [hit for path in PRODUCTION for hit in _literal_secrets(path)] == []


LITERAL = re.compile(r"""(?ix)(?:^|[\s{,;(])["']?[\w.-]*(?:api[_-]?key|secret|passw(?:or)?d|access_?token|client_?secret|credential)s?["']?
                         \s*[:=]\s*(["'])(?!\s*\1)(?P<value>[^"'\s$<{/][^"'\s$<{]{7,})\1""")
OTHER_CODE = [f for f in FILES if re.search(r"\.(ts|tsx|js|cjs|mjs|sh|ya?ml|toml|sql|json)$|(^|/)(Dockerfile|Caddyfile[\w.]*|trackflow-ctl)$", f)
              and not re.search(r"(^|/)(tests?|__tests__)/|/evidence/|^docs/pr-evidence/", f)]


def test_no_literal_credential_outside_python_either():
    found = []
    for name in OTHER_CODE:
        for number, line in enumerate((ROOT / name).read_text(encoding="utf-8", errors="ignore").splitlines(), 1):
            match = LITERAL.search(line)
            if match and re.search(r"[A-Za-z]", match["value"]) and not re.search(r"(?i)^(string|boolean|number|bearer|text|password|current-password|new-password)$", match["value"]):
                found.append(f"{name}:{number}")
    assert found == []


def test_no_script_sets_or_uses_a_fixed_password():
    scripts = [f for f in FILES if re.search(r"\.sh$|(^|/)Dockerfile$|trackflow-ctl$", f)]
    hits = [f"{f}:{n}" for f in scripts for n, line in enumerate((ROOT / f).read_text(encoding="utf-8").splitlines(), 1)
            if re.search(r"sshpass\s+-p\s*[^$\s]|requirepass\s+[^$\s]|\w+:[^$\s'\"]+['\"]?\s*\|\s*chpasswd", line)]
    assert hits == []


@pytest.mark.parametrize("sample,hit", [('const apiKey = "abcdef123456"', True), ("  password: 'hunter2-largo'", True), ('CLIENT_SECRET="abcdefgh1"', True),
                                        ('apiKey: process.env.KEY', False), ('password: ${DB_PASSWORD}', False), ('type="password"', False)])
def test_the_literal_detector_detects(sample, hit):
    assert bool(LITERAL.search(sample)) is hit


@pytest.mark.parametrize("sample,hit", [('API_KEY = "abcdef123456"', True), ('x = os.environ.get("LLM_API_KEY", "por-defecto")', True),
                                        ('Client(password="hunter2!")', True), ('d = {"client_secret": "zzzzzz"}', True),
                                        ('os.environ["SECRET_KEY"] = "fijo-en-el-codigo"', True),
                                        ('API_KEY = os.environ["LLM_API_KEY"]', False), ('x = os.environ.get("LLM_API_KEY", "")', False)])
def test_the_detector_detects(tmp_path, monkeypatch, sample, hit):
    (tmp_path / "s.py").write_text(sample)
    monkeypatch.setattr(sys.modules[__name__], "ROOT", tmp_path)
    assert bool(_literal_secrets("s.py")) is hit


def test_env_files_and_keys_are_never_tracked():
    tracked = subprocess.run(["git", "ls-files"], cwd=ROOT, capture_output=True, text=True, check=True).stdout.splitlines()
    assert [f for f in tracked if re.search(r"\.(pem|key|p12|pfx)$|(^|/)id_(rsa|ed25519)$", f)] == []
    env = [f for f in tracked if re.search(r"(^|/)\.env(\..+)?$", f) and not f.endswith(".example")]
    assert env == ["uis/talent-pipeline-tracker/.env.local"]              # heredado: solo una URL pública (informe, brecha B-9)
    text = (ROOT / env[0]).read_text(encoding="utf-8")
    assert re.fullmatch(r"\s*NEXT_PUBLIC_API_URL=\S*\s*", text)


def test_the_example_env_files_have_no_values():
    for name in [f for f in FILES if re.search(r"\.env\.example$", f)]:
        for line in (ROOT / name).read_text(encoding="utf-8").splitlines():
            key, _, value = line.partition("=")
            if SENSITIVE_NAME.search(key.strip()) and not line.lstrip().startswith("#"):
                assert value.strip().strip('"') == "" or re.search(r"(?i)cambia|change|your|tu_|<|xxx|replace|reemplaza", value), f"{name}: {key}"
