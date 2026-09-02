#!/bin/sh
# entrypoint.sh - 容器启动时:
#   1) 通过阿里 DoH 解析所有字节火山/抖音/红果域名,写入 /etc/hosts
#   2) 转交 CMD
#
# 背景:NAS 上 caddy-proxy 的 TUN 模式把容器 DNS 解析劫持到 198.18.0.0/15(fake-ip 段)
# 导致容器内所有外网 HTTPS 都 timeout。本脚本在 docker daemon 生成 /etc/hosts 之后注入真实 IP。

set -e

DOH="https://dns.alidns.com/resolve"
HOSTS_FILE="/etc/hosts"

# 需要解析的域名列表(全部走 DoH 取权威 A 记录)
# v1~v99:字节火山视频 CDN(vXX-reading-video-?.qznovelvod.com 是实际播放地址)
# vXX-hongman:字节火山漫剧 CDN(quickapp 分享页用)
DOMAINS="
api5-normal-sinfonlinea.fqnovel.com
api5-normal-sinfonlineb.fqnovel.com
hongguoduanju.com
www.hongguoduanju.com
v11-cold.douyinvod.com
v6-cold.douyinvod.com
v26-cold.douyinvod.com
v3-cold.douyinvod.com
v9-cold.douyinvod.com
v5-cold.douyinvod.com
v95-cold.douyinvod.com
v8-cold.douyinvod.com
v1-cold.douyinvod.com
v2-cold.douyinvod.com
v4-cold.douyinvod.com
v7-cold.douyinvod.com
v5-reading-video-d.qznovelvod.com
v6-reading-video-d.qznovelvod.com
v11-reading-video-d.qznovelvod.com
v26-reading-video-d.qznovelvod.com
v3-reading-video-d.qznovelvod.com
v9-reading-video-d.qznovelvod.com
v5-hongman.qznovelvod.com
v6-hongman.qznovelvod.com
v11-hongman.qznovelvod.com
v26-hongman.qznovelvod.com
v3-hongman.qznovelvod.com
v9-hongman.qznovelvod.com
kylin.hainanyuyue.com
vas-lf-x.snssdk.com
lf1-cdn-tos.bytegoofy.com
"

echo "[entrypoint] === 注入 hosts 绕过 DNS 劫持 ==="
{
    echo ""
    echo "# --- entrypoint 自动注入 $(date -Iseconds) ---"
    for d in $DOMAINS; do
        # DoH 解析,取第一个 A 记录 IP
        ip=$(curl -sS --max-time 5 "$DOH?name=$d&type=A" 2>/dev/null \
            | python3 -c "
import sys, json
try:
    d = json.load(sys.stdin)
    ans = [a['data'] for a in d.get('Answer', []) if a.get('type') == 1]
    print(ans[0] if ans else '')
except Exception:
    print('')
" 2>/dev/null)
        if [ -n "$ip" ]; then
            printf '%s\t%s\n' "$ip" "$d"
            echo "[entrypoint]   $d -> $ip"
        else
            echo "[entrypoint]   $d -> (解析失败,跳过)"
        fi
    done
} >> "$HOSTS_FILE"

echo "[entrypoint] /etc/hosts 当前条目数: $(wc -l < $HOSTS_FILE)"
echo "[entrypoint] === 解析验证 ==="
for d in api5-normal-sinfonlineb.fqnovel.com v11-cold.douyinvod.com; do
    echo "[entrypoint]   $d: $(getent ahosts $d 2>/dev/null | head -1)"
done

# 转交 CMD
exec "$@"