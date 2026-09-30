#!/usr/bin/env python3
"""
Publish the website (app + offline data) to the `gh-pages` branch, which GitHub Pages serves.

The branch always holds just ONE commit that is replaced on every publish (force push), so new
data builds don't pile up in the repository history. The code history stays on `main`
(without data/). The publish repository (%LOCALAPPDATA%/WildNav-publish) lives outside OneDrive
and reads the site files straight from this folder (no copies).

Usage (after build_tiles.py, from the project folder):
    python publish_site.py

Only files that changed are uploaded (git knows what the previous publish contained).
"""
import json
import os
import subprocess
import sys
import tempfile

ROOT = os.path.dirname(os.path.abspath(__file__))
PUB = os.path.join(os.environ.get('LOCALAPPDATA') or tempfile.gettempdir(), 'WildNav-publish')
SITE = ['index.html', 'sw.js', 'manifest.webmanifest', 'README.md', 'icons', 'data']
BRANCH = 'gh-pages'


def git(*args, check=True, inp=None):
    """git with the publish repository as the database and the project folder as the files."""
    return subprocess.run(['git', f'--git-dir={os.path.join(PUB, ".git")}', f'--work-tree={ROOT}', *args],
                          cwd=ROOT, check=check, text=True, capture_output=True, input=inp)


def main():
    if not os.path.exists(os.path.join(ROOT, 'data', 'index.json')):
        sys.exit('data/index.json is missing: build the data first (build_tiles.py).')
    if not os.path.isdir(os.path.join(PUB, '.git')):
        os.makedirs(PUB, exist_ok=True)
        subprocess.run(['git', 'init', '-q', PUB], check=True)
        remote = subprocess.run(['git', 'remote', 'get-url', 'origin'], cwd=ROOT, check=True,
                                text=True, capture_output=True).stdout.strip()
        git('remote', 'add', 'origin', remote)
    # what the remote branch has now, so unchanged files are not uploaded again
    have_remote = git('fetch', '-q', 'origin', BRANCH, check=False).returncode == 0

    # build the tree in a fresh index: exactly the site files (+ .nojekyll: serve files as they are)
    idx = os.path.join(PUB, '.git', 'publish-index')
    if os.path.exists(idx):
        os.remove(idx)
    env = dict(os.environ, GIT_INDEX_FILE=idx)
    base = ['git', f'--git-dir={os.path.join(PUB, ".git")}', f'--work-tree={ROOT}']
    subprocess.run(base + ['add', '-f', '--', *[s for s in SITE if os.path.exists(os.path.join(ROOT, s))],
                           ':!*.part', ':!**/__pycache__/**'], cwd=ROOT, env=env, check=True)
    empty = git('hash-object', '-w', '--stdin', inp='').stdout.strip()
    subprocess.run(base + ['update-index', '--add', '--cacheinfo', f'100644,{empty},.nojekyll'], cwd=ROOT, env=env, check=True)
    tree = subprocess.run(base + ['write-tree'], cwd=ROOT, env=env, check=True, text=True, capture_output=True).stdout.strip()

    # one new commit without parents, replacing the branch
    with open(os.path.join(ROOT, 'data', 'index.json'), encoding='utf-8') as f:
        built = json.load(f).get('built', '')
    head = subprocess.run(['git', 'rev-parse', '--short', 'HEAD'], cwd=ROOT, check=True, text=True,
                          capture_output=True).stdout.strip()
    commit = git('-c', 'user.name=WildNav publish', '-c', 'user.email=publish@wildnav.invalid',
                 'commit-tree', tree, '-m', f'Site: code {head}, data {built}').stdout.strip()
    git('update-ref', f'refs/heads/{BRANCH}', commit)
    print(f'Publishing to {BRANCH} ({"only changed files" if have_remote else "first upload, all files"})…', flush=True)
    r = subprocess.run(base + ['push', '--force', 'origin', f'{BRANCH}:{BRANCH}'], cwd=ROOT)
    if r.returncode:
        sys.exit('Push failed (see above).')
    git('gc', '-q', '--prune=now', check=False)   # drop the previous publish locally
    print('Done. GitHub Pages updates within a few minutes.')


if __name__ == '__main__':
    main()
