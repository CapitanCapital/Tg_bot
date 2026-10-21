#!/bin/sh
# Install existing timer configuration; never publish or prepare immediately.
set -eu
if [ "$(id -u)" -ne 0 ]; then
  echo 'Run as root on the digest server' >&2
  exit 1
fi
deploy_dir=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
test -x /home/digest/.local/bin/codex
test -d /home/digest/.codex
test -x /home/digest/tg-news-digest/.venv/bin/python
for unit in tg-digest-prepare.service tg-digest-publish.service tg-digest-prepare.timer tg-digest-publish.timer; do
  install -m 644 "$deploy_dir/$unit" "/etc/systemd/system/$unit"
done
install -d -m 755 /etc/systemd/system/tg-digest-prepare.service.d
install -m 644 "$deploy_dir/tg-digest-prepare.service.d/codex.conf" /etc/systemd/system/tg-digest-prepare.service.d/codex.conf
systemd-analyze verify /etc/systemd/system/tg-digest-prepare.service /etc/systemd/system/tg-digest-publish.service /etc/systemd/system/tg-digest-prepare.timer /etc/systemd/system/tg-digest-publish.timer
systemctl daemon-reload
systemctl enable --now tg-digest-prepare.timer tg-digest-publish.timer
systemctl list-timers 'tg-digest-*' --no-pager
