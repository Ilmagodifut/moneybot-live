#!/usr/bin/env bash
# Pubblica la web app (site/ + ultimi dati del bot) sul branch gh-pages.
set -euo pipefail
out=$(mktemp -d)
cp -r site/. "$out/"
mkdir -p "$out/data"
cp state/dashboard/*.json "$out/data/"
touch "$out/.nojekyll"
cd "$out"
git init -q -b gh-pages
git add -A
git -c user.name="moneybot" -c user.email="moneybot@users.noreply.github.com" commit -q -m "web app $(date -u '+%Y-%m-%d %H:%M') UTC"
git push -q -f "https://x-access-token:${GITHUB_TOKEN}@github.com/${GITHUB_REPOSITORY}.git" gh-pages
