#!/usr/bin/env bash
set -euo pipefail

BASE="https://typhoon.coffee"
SITEMAPS=(sitemap_en.xml sitemap_de-DE.xml sitemap_es-ES.xml sitemap_it-IT.xml sitemap_fr-FR.xml sitemap_cs-CZ.xml)

urls_file=$(mktemp)
trap 'rm -f "$urls_file"' EXIT

for sm in "${SITEMAPS[@]}"; do
    curl -s "$BASE/$sm" | grep -o '<loc>[^<]*</loc>' | sed -e 's/<loc>//' -e 's/<\/loc>//' >> "$urls_file"
done

total=$(wc -l < "$urls_file")
echo "Warming $total URLs..."

failures=$(mktemp)
trap 'rm -f "$urls_file" "$failures"' EXIT

cat "$urls_file" | xargs -P 10 -I{} sh -c '
    code=$(curl -s -o /dev/null -w "%{http_code}" "{}")
    if [ "$code" != "200" ]; then
        echo "{} -> $code"
    fi
' >> "$failures"

fail_count=$(wc -l < "$failures")
if [ "$fail_count" -gt 0 ]; then
    echo "$fail_count non-200 responses:"
    cat "$failures"
else
    echo "All URLs returned 200."
fi
