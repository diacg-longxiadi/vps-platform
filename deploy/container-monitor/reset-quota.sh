#!/bin/bash
# 每月重置所有容器的 IPv4 + IPv6 流量配額
# 方法：刪除舊規則 → 重新插入

QUOTA_BYTES=$((500 * 1024**3))

# === IPv6 - ip6tables DOCKER chain ===
# alpine-box ::3
ip6tables -D DOCKER -d 2001:470:1f18:2cb::3 -m quota --quota $QUOTA_BYTES -j ACCEPT 2>/dev/null
ip6tables -D DOCKER -s 2001:470:1f18:2cb::3 -m quota --quota $QUOTA_BYTES -j ACCEPT 2>/dev/null
# 重新插入（位置 1 = 在 DROP 規則之前）
ip6tables -I DOCKER 1 -d 2001:470:1f18:2cb::3 -m quota --quota $QUOTA_BYTES -j ACCEPT
ip6tables -I DOCKER 2 -s 2001:470:1f18:2cb::3 -m quota --quota $QUOTA_BYTES -j ACCEPT

# === IPv4 - iptables DOCKER-USER chain ===
# alpine-box 172.19.0.2
iptables -D DOCKER-USER -d 172.19.0.2/32 -m quota --quota $QUOTA_BYTES -j ACCEPT 2>/dev/null
iptables -D DOCKER-USER -s 172.19.0.2/32 -m quota --quota $QUOTA_BYTES -j ACCEPT 2>/dev/null
# 重新插入（位置 1 = 在 DROP 規則之前）
iptables -I DOCKER-USER 1 -d 172.19.0.2/32 -m quota --quota $QUOTA_BYTES -j ACCEPT
iptables -I DOCKER-USER 2 -s 172.19.0.2/32 -m quota --quota $QUOTA_BYTES -j ACCEPT

echo "Fri May 29 01:15:59 AM UTC 2026: Quota reset complete for alpine-box"
