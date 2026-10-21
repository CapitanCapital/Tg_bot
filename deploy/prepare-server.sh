set -eu
export DEBIAN_FRONTEND=noninteractive
apt-get -o Acquire::Retries=1 -o Acquire::https::Timeout=20 -o Acquire::http::Timeout=20 update -qq
apt-get -o Acquire::Retries=1 -o Acquire::https::Timeout=20 -o Acquire::http::Timeout=20 install -y -qq python3-venv ca-certificates curl
if ! id digest >/dev/null 2>&1; then
 useradd --create-home --shell /bin/bash digest
fi
install -d -o digest -g digest -m 750 /home/digest/tg-news-digest
install -d -o digest -g digest -m 700 /home/digest/tg-news-digest/data /home/digest/tg-news-digest/logs
runuser -u digest -- python3 -m venv /home/digest/tg-news-digest/.venv
if [ -z "$(swapon --show --noheadings)" ] && [ ! -e /swapfile ]; then
 fallocate -l 1G /swapfile
 chmod 600 /swapfile
 mkswap /swapfile
 swapon /swapfile
 if ! grep -q '^/swapfile ' /etc/fstab; then
  printf '/swapfile none swap sw 0 0\n' >> /etc/fstab
 fi
fi
runuser -u digest -- /home/digest/tg-news-digest/.venv/bin/python - <<'PY'
import ssl,sqlite3,os,sys
from pathlib import Path
p=Path('/home/digest/tg-news-digest/data/.write-check')
p.write_text('ok');p.unlink()
assert os.getuid()!=0
assert sys.prefix!=sys.base_prefix
print('Non-root virtual environment, SSL, SQLite and data directory: OK')
PY
free -m
df -h /
id digest
systemctl is-active unattended-upgrades
printf 'Reboot required: '
if [ -f /var/run/reboot-required ]; then echo yes; else echo no; fi
