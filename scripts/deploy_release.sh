#!/usr/bin/env bash
set -Eeuo pipefail

if [[ $# -ne 2 ]]; then
  echo "用法: $0 <发布压缩包.tar.gz> <版本号>" >&2
  exit 2
fi

archive="$(readlink -f "$1")"
release_id="$2"
app_root="${OREATE_APP_ROOT:-/www/wwwroot/oreateai}"
state_dir="${OREATE_STATE_DIR:-/var/lib/oreateai}"
service_name="${OREATE_SERVICE_NAME:-oreateai}"
health_url="${OREATE_HEALTH_URL:-http://127.0.0.1:8897/healthz}"
service_owner="${OREATE_SERVICE_OWNER:-oreateai:oreateai}"
release_root="$app_root/releases"
current_link="$app_root/current"
release_dir="$release_root/$release_id"

if [[ ! "$release_id" =~ ^[A-Za-z0-9._-]+$ ]]; then
  echo "版本号只能包含字母、数字、点、下划线和短横线" >&2
  exit 2
fi
if [[ ! -f "$archive" ]]; then
  echo "发布压缩包不存在: $archive" >&2
  exit 2
fi
for state_file in config.json accounts.db; do
  if [[ ! -f "$state_dir/$state_file" ]]; then
    echo "持久化文件不存在: $state_dir/$state_file" >&2
    exit 2
  fi
done
if [[ -e "$release_dir" ]]; then
  echo "发布目录已存在，拒绝覆盖: $release_dir" >&2
  exit 2
fi
if [[ -e "$current_link" && ! -L "$current_link" ]]; then
  echo "当前版本路径不是软链接，拒绝切换: $current_link" >&2
  exit 2
fi

mkdir -p "$release_root"
mkdir "$release_dir"
tar -xzf "$archive" -C "$release_dir"

# 仓库中可保留本地开发配置和数据库，但线上版本必须始终指向持久化目录。
for state_file in config.json accounts.db; do
  rm -f -- "$release_dir/$state_file"
  ln -s "$state_dir/$state_file" "$release_dir/$state_file"
done
chown -R "$service_owner" "$release_dir"

previous_release=""
if [[ -L "$current_link" ]]; then
  previous_release="$(readlink -f "$current_link")"
fi
switched=0

rollback() {
  local exit_code=$?
  trap - ERR
  if [[ "$switched" -eq 1 && -n "$previous_release" && -d "$previous_release" ]]; then
    echo "健康检查失败，回滚到: $previous_release" >&2
    ln -sfn "$previous_release" "$current_link"
    systemctl restart "$service_name" || true
  fi
  exit "$exit_code"
}
trap rollback ERR

ln -sfn "$release_dir" "$current_link"
switched=1
systemctl restart "$service_name"

for _ in $(seq 1 30); do
  if systemctl is-active --quiet "$service_name" && curl -fsS "$health_url" >/dev/null; then
    trap - ERR
    echo "发布成功: $release_dir"
    exit 0
  fi
  sleep 1
done

echo "服务未在 30 秒内通过健康检查" >&2
false
