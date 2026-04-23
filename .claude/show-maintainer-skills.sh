#!/usr/bin/env bash
# Displays a maintainer skills banner at session start.
# Reads name + description from frontmatter of each .claude/skills/*/SKILL.md.

SKILLS_DIR=".claude/skills"
LOGO="docs/assets/clauditor-social-preview.png"

# Write directly to the terminal so the banner is visible to the user.
# Hook stdout is captured for Claude's context injection; /dev/tty bypasses that.
exec > /dev/tty

BOLD=$'\033[1m'
CYAN=$'\033[36m'
YELLOW=$'\033[33m'
GRAY=$'\033[90m'
RESET=$'\033[0m'

# Logo
if command -v chafa &>/dev/null && [[ -f "$LOGO" ]]; then
    chafa --size 60x30 "$LOGO"
fi

skills=()
descs=()
while IFS= read -r skill_file; do
    name=$(grep -m1 '^name:' "$skill_file" 2>/dev/null | sed 's/^name: *//')
    desc=$(grep -m1 '^description:' "$skill_file" 2>/dev/null | sed 's/^description: *//')
    [[ -n "$name" ]] && skills+=("/$name") && descs+=("$desc")
done < <(find "$SKILLS_DIR" -name "SKILL.md" 2>/dev/null | sort)

[[ ${#skills[@]} -eq 0 ]] && exit 0

echo ""
printf "  ${BOLD}${YELLOW}⚙  Maintainer Skills${RESET}\n"
printf "  ${GRAY}%s${RESET}\n" "────────────────────────────────────────────────────────────"

for i in "${!skills[@]}"; do
    name="${skills[$i]}"
    desc="${descs[$i]}"
    if [[ ${#desc} -gt 55 ]]; then
        desc="${desc:0:54}…"
    fi
    printf "  ${CYAN}%-28s${RESET} %s\n" "$name" "$desc"
done

printf "  ${GRAY}%s${RESET}\n" "────────────────────────────────────────────────────────────"
echo ""
