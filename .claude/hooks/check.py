"""Claude Code hook (PostToolUse on Edit/Write): syntax check of the file that was just edited.

.py          -> Python syntax (the same check as `python -m py_compile`, without writing a .pyc)
.js / .html  -> JavaScript syntax with `node --check` (for HTML: each inline <script> block, line numbers kept)
.json / .webmanifest -> JSON syntax
Silent with exit 0 when all is fine; on an error exit 2 with a short message on stderr, which Claude sees.
"""
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile

MAX_LINES = 20


def fail(msg):
    lines = msg.strip().splitlines()
    if len(lines) > MAX_LINES:
        lines = lines[:MAX_LINES] + ['…']
    sys.stderr.write('\n'.join(lines) + '\n')
    sys.exit(2)


def find_node():
    cands = [shutil.which('node'), r'C:\Program Files\nodejs\node.exe']
    try:  # Playwright for Python brings its own Node.js
        import playwright
        cands.append(os.path.join(os.path.dirname(playwright.__file__), 'driver', 'node.exe'))
    except ImportError:
        pass
    return next((c for c in cands if c and os.path.isfile(c)), None)


def node_check(node, code, shown_name, module=False):
    """Run node --check on code; return a short error text or None."""
    fd, tmp = tempfile.mkstemp(suffix='.mjs' if module else '.js')
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as f:
            f.write(code)
        r = subprocess.run([node, '--check', tmp], capture_output=True, text=True, encoding='utf-8',
                           errors='replace', timeout=60)
    finally:
        os.remove(tmp)
    if r.returncode == 0:
        return None
    out = (r.stderr or r.stdout).replace(tmp, shown_name)
    keep = [s for s in out.splitlines() if not s.startswith('    at ') and not s.startswith('Node.js v')]
    return '\n'.join(keep).strip()


def check_js_file(path, name):
    node = find_node()
    if not node:
        print('JavaScript syntax check skipped: Node.js not found (winget install OpenJS.NodeJS.LTS)', file=sys.stderr)
        sys.exit(1)
    with open(path, encoding='utf-8') as f:
        err = node_check(node, f.read(), name, module=path.endswith('.mjs'))
    if err:
        fail(f'JavaScript syntax error in {name}:\n{err}')


SCRIPT = re.compile(r'<script\b([^>]*)>(.*?)</script\s*>', re.S | re.I)
JS_TYPES = ('', 'text/javascript', 'application/javascript', 'module')


def check_html(path, name):
    with open(path, encoding='utf-8') as f:
        html = f.read()
    blocks = []
    for m in SCRIPT.finditer(html):
        attrs = m.group(1)
        if re.search(r'\bsrc\s*=', attrs, re.I):
            continue
        t = re.search(r'\btype\s*=\s*["\']?([^"\'\s>]+)', attrs, re.I)
        typ = t.group(1).lower() if t else ''
        if typ not in JS_TYPES:
            continue
        # blank lines in front so node reports the line numbers of the HTML file
        pad = '\n' * html.count('\n', 0, m.start(2))
        blocks.append((pad + m.group(2), typ == 'module'))
    if not blocks:
        return
    node = find_node()
    if not node:
        print('JavaScript syntax check skipped: Node.js not found (winget install OpenJS.NodeJS.LTS)', file=sys.stderr)
        sys.exit(1)
    for code, module in blocks:
        err = node_check(node, code, name, module)
        if err:
            fail(f'JavaScript syntax error in an inline <script> of {name} (line numbers = lines in {name}):\n{err}')


def check_py(path, name):
    with open(path, 'rb') as f:
        src = f.read()
    try:
        compile(src, name, 'exec')
    except SyntaxError as e:
        fail(f'Python syntax error in {name}, line {e.lineno}: {e.msg}\n{(e.text or "").rstrip()}')


def check_json(path, name):
    try:
        with open(path, encoding='utf-8-sig') as f:
            json.load(f)
    except ValueError as e:
        fail(f'JSON error in {name}: {e}')


def main():
    sys.stderr.reconfigure(encoding='utf-8', errors='replace')
    try:
        data = json.loads(sys.stdin.buffer.read().decode('utf-8', 'replace'))
    except ValueError:
        return
    path = (data.get('tool_input') or {}).get('file_path') or ''
    if not path or not os.path.isfile(path):
        return
    low = path.lower()
    norm = low.replace('\\', '/')
    if '/data/12/' in norm or '/raw/' in norm:
        return
    name = os.path.basename(path)
    if low.endswith('.py'):
        check_py(path, name)
    elif low.endswith(('.js', '.mjs')):
        check_js_file(path, name)
    elif low.endswith(('.html', '.htm')):
        check_html(path, name)
    elif low.endswith(('.json', '.webmanifest')):
        check_json(path, name)


if __name__ == '__main__':
    main()
