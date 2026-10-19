#!/usr/bin/env bash
# Writes .env and Caddyfile for the EC2 + Caddy deployment by asking questions,
# so nobody has to hand-edit password hashes. Re-run it to change users: it
# asks for every user again and replaces both files.
# See docs/deploy-ec2-caddy.md.
set -euo pipefail
cd "$(dirname "$0")"

# How to hash a password read from stdin; overridable for testing.
HASH_CMD=${HASH_CMD:-"docker run --rm -i caddy:2 caddy hash-password"}

ask() {  # ask "Prompt" [default] -> echoes the answer
    local reply
    if [[ -n "${2:-}" ]]; then
        read -r -p "$1 [$2]: " reply
        echo "${reply:-$2}"
    else
        while [[ -z "${reply:-}" ]]; do read -r -p "$1: " reply; done
        echo "$reply"
    fi
}

echo "== Site =="
domain=$(ask "Domain name for the app (e.g. dpre.example.com)")
email=$(ask "Your email (Let's Encrypt sends certificate notices here)")

users=()
roles=()
hashes=()
echo
echo "== Users =="
echo "Roles: 1) admin   = operator,reviewer,steward,catalog_admin (upload, run, review)"
echo "       2) reviewer"
echo "       3) steward"
echo "       4) council = approve weights and waivers (give to someone who does NOT review)"
echo "       or type your own comma-separated list"
while :; do
    echo
    name=$(ask "Username (letters, digits, . _ -)")
    if [[ ! "$name" =~ ^[A-Za-z0-9._-]+$ ]]; then echo "  Not a valid username."; continue; fi
    choice=$(ask "Role for $name" "1")
    case "$choice" in
        1) role="operator,reviewer,steward,catalog_admin" ;;
        2) role="reviewer" ;;
        3) role="steward" ;;
        4) role="council" ;;
        *) role="${choice// /}" ;;
    esac
    while :; do
        read -r -s -p "Password for $name (min 12 characters): " pw; echo
        read -r -s -p "Repeat password: " pw2; echo
        if [[ "$pw" != "$pw2" ]]; then echo "  Passwords differ."; continue; fi
        if (( ${#pw} < 12 )); then echo "  Too short."; continue; fi
        break
    done
    echo "  Hashing (the first time downloads the Caddy image)..."
    hash=$(printf '%s\n' "$pw" | $HASH_CMD | tail -n 1)
    unset pw pw2
    users+=("$name"); roles+=("$role"); hashes+=("$hash")
    more=$(ask "Add another user? (y/n)" "n")
    [[ "$more" =~ ^[Yy] ]] || break
done

cat > .env <<EOF
DPRE_DOMAIN=$domain
ACME_EMAIL=$email
EOF

{
    echo "# Written by setup.sh on $(date -u +%F). Re-run setup.sh to change users."
    echo "{"
    echo "	email {\$ACME_EMAIL}"
    echo "}"
    echo
    echo "{\$DPRE_DOMAIN} {"
    echo "	encode gzip"
    echo
    echo "	request_body {"
    echo "		max_size 64MB"
    echo "	}"
    echo
    echo "	basic_auth {"
    for i in "${!users[@]}"; do echo "		${users[$i]} ${hashes[$i]}"; done
    echo "	}"
    echo
    echo "	map {http.auth.user.id} {dpre_roles} {"
    for i in "${!users[@]}"; do echo "		${users[$i]} \"${roles[$i]}\""; done
    echo "		default \"reviewer\""
    echo "	}"
    echo
    echo "	map {http.auth.user.id} {dpre_domains} {"
    echo "		default \"\""
    echo "	}"
    echo
    echo "	reverse_proxy dpre:8000 {"
    echo "		header_up -X-DPRE-Identity"
    echo "		header_up -Authorization"
    echo "		header_up X-Forwarded-User {http.auth.user.id}"
    echo "		header_up X-Forwarded-Roles {dpre_roles}"
    echo "		header_up X-Forwarded-Groups {dpre_domains}"
    echo "	}"
    echo "}"
} > Caddyfile
chmod 600 .env Caddyfile

echo
echo "Wrote .env and Caddyfile for $domain with ${#users[@]} user(s)."
echo "Next: docker compose up -d --build"
echo "(If the app is already running: docker compose restart caddy)"
