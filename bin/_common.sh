# Shared helpers; sourced by the other bin/ scripts.
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
UV=/opt/homebrew/bin/uv
[ -x "$UV" ] || UV="$(command -v uv)"
LOG_DIR="$HOME/Library/Logs/wrgpt-buddy"
AGENTS_DIR="$HOME/Library/LaunchAgents"
WEB_LABEL=com.sefk.wrgpt-buddy.web
POLL_LABEL=com.sefk.wrgpt-buddy.poll
GUI="gui/$(id -u)"
unset VIRTUAL_ENV

ensure_env() {
  [ -f "$ROOT/.env" ] || cp "$ROOT/.env.example" "$ROOT/.env"
  if ! grep -q '^DJANGO_SECRET_KEY=[^c ]' "$ROOT/.env" || grep -q '^DJANGO_SECRET_KEY=change-me' "$ROOT/.env"; then
    key="$(python3 -c 'import secrets; print(secrets.token_urlsafe(50))')"
    sed -i '' '/^DJANGO_SECRET_KEY=/d' "$ROOT/.env"
    echo "DJANGO_SECRET_KEY=$key" >> "$ROOT/.env"
    echo "generated DJANGO_SECRET_KEY in .env"
  fi
}

load_env() {
  set -a; [ -f "$ROOT/.env" ] && . "$ROOT/.env"; set +a
}
