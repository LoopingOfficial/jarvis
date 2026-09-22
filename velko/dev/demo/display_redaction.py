"""Redact outbound display data only; never rewrite workspace files or credentials."""
import re
MARKER = '[SECRET MASQUÉ]'
PATTERNS = [
 re.compile(r'(?i)(\b(?:api[_-]?key|[a-z_]*token|password|passwd|secret|cookie|authorization|credentials?)\b[\s"\']*[:=]\s*["\']?)([^\s"\'`,;<>]{8,})'),
 re.compile(r'(?i)(\bBearer\s+)([A-Za-z0-9_.+/-]{8,})'),
 re.compile(r'()\b(?:sk-[A-Za-z0-9_-]{16,}|gh[pousr]_[A-Za-z0-9_]{20,}|github_pat_[A-Za-z0-9_]{20,})\b'),
 re.compile(r'()-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----[\s\S]*?-----END (?:RSA |EC |OPENSSH )?PRIVATE KEY-----'),
]
def redact_text(value):
 for pattern in PATTERNS:
  value = pattern.sub(lambda m: m.group(1) + MARKER, value)
 return value

def display_copy(value):
 if isinstance(value, str): return redact_text(value)
 if isinstance(value, list): return [display_copy(v) for v in value]
 if isinstance(value, dict):
  result = {k: display_copy(v) for k, v in value.items()}
  if isinstance(value.get('content'), str): result['redacted'] = result['content'] != value['content']
  return result
 return value
