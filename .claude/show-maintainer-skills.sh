#!/usr/bin/env bash
# Displays a maintainer skills banner at session start.
# Reads name + description from frontmatter of each .claude/skills/*/SKILL.md.

SKILLS_DIR=".claude/skills"
LOGO="docs/assets/clauditor-social-preview.png"

# Write directly to the user's terminal so the banner is visible.
#
# Hook stdout is captured by Claude Code for context injection — anything we
# print there is hidden from the user (and counted against context budget). We
# need a sidechannel to the real terminal.
#
# /dev/tty doesn't work: Claude Code spawns hook children without a
# controlling terminal, so `exec > /dev/tty` fails with ENXIO.
#
# Strategy: walk up the parent process chain and find the first ancestor whose
# stdout (/proc/PID/fd/1) is a /dev/pts/N device. That's the terminal that
# launched Claude (works whether or not tmux is in the chain). Write the
# banner there directly. If no PTY ancestor is found (Claude launched from a
# non-terminal context — IDE plugin, CI, etc.), exit silently.

find_user_tty() {
    local pid=$PPID
    local depth=0
    while [ -n "$pid" ] && [ "$pid" != "0" ] && [ "$pid" != "1" ] && [ $depth -lt 20 ]; do
        local target
        target=$(readlink "/proc/$pid/fd/1" 2>/dev/null)
        case "$target" in
            /dev/pts/*|/dev/tty[0-9]*)
                if [ -w "$target" ]; then
                    echo "$target"
                    return 0
                fi
                ;;
        esac
        pid=$(awk '{print $4}' "/proc/$pid/stat" 2>/dev/null)
        depth=$((depth + 1))
    done
    return 1
}

USER_TTY=$(find_user_tty)
[ -z "$USER_TTY" ] && exit 0
exec > "$USER_TTY"

BOLD=$'\033[1m'
CYAN=$'\033[36m'
YELLOW=$'\033[33m'
GRAY=$'\033[90m'
RESET=$'\033[0m'

# Render order: small thumbnail logo FIRST, then skills list. Claude Code
# repaints its TUI over the bottom of the banner output, so the bottom
# portion gets clipped. Logo is sized small (24x12 cells) so the total
# banner height stays compact and the skills list lands in the survival zone.

if command -v chafa &>/dev/null && [[ -f "$LOGO" ]]; then
    echo ""
    chafa --size 24x12 -f symbols "$LOGO"
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
