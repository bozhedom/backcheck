#!/usr/bin/env bash
# Снимок на дедлайн: какой коммит был последним в каждом открытом PR.
# Запускать в 23:59 накануне встречи, например через cron:
#   59 23 * * *  ORG=my-classroom-org /path/to/deadline-snapshot.sh
#
# ORG    организация GitHub Classroom, где лежат репозитории проектов;
# REPOS  или файл со списком owner/repo, по одному в строке (если репозитории в личных аккаунтах).
# Результат: snapshot-ГГГГ-ММ-ДД.tsv с колонками: репозиторий, ветка, коммит, время открытия PR.
set -euo pipefail

out="snapshot-$(date +%F).tsv"
printf 'repo\tbranch\tcommit\tpr_created_at\n' > "$out"

if [[ -n "${REPOS:-}" ]]; then
  repos=$(grep -v '^\s*#' "$REPOS" | sed '/^\s*$/d')
else
  : "${ORG:?Укажи ORG=организация или REPOS=файл со списком}"
  repos=$(gh repo list "$ORG" --limit 500 --json nameWithOwner --jq '.[].nameWithOwner' | grep '/backend-' || true)
fi

for repo in $repos; do
  gh pr list -R "$repo" --state open --json headRefName,headRefOid,createdAt \
    --jq '.[] | [.headRefName, .headRefOid[0:7], .createdAt] | @tsv' | sed "s#^#${repo}\t#" >> "$out"
done

echo "Записано: $out ($(($(wc -l < "$out") - 1)) PR)"
