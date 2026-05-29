# 帝云 VPS 平台 — 開發筆記

> 給 AI 助手的專案記憶，方便日後接手維護。

## 專案概覽

- **URL:** https://docker-vps.xn--acg-4i2f.xyz
- **主機:** Oracle Cloud AMD (VM.Standard.E2.1.Micro, 1GB RAM)
- **後端:** FastAPI + Uvicorn (port 8081)
- **反向代理:** Caddy (HTTPS, port 443 → 8081)
- **容器引擎:** Incus（管理底層的 ubuntu system container）
- **資料庫:** SQLite (`/opt/vps-platform/data/vps.db`)
- **模板:** Jinja2 + 視覺設計師提供的暗色風格模板
- **靜態檔:** `/opt/vps-platform/static/` (CSS, JS)

系統目錄：`/opt/vps-platform/`

## 路徑

- `/` — Landing page（未登入）/ Dashboard（已登入）
- `/login` / `/register` — 登入註冊
- `/dashboard` — 使用者總覽
- `/instances` — 實例列表
- `/instances/create` — 選擇方案建立實例
- `/instance/{id}` — 實例詳情（SSH 資訊、資源用量、動作按鈕）
- `/wallet` — 錢包餘額與交易紀錄
- `/transfer` — 餘額/實例轉移
- `/admin` — 管理員面板

## 網路架構（核心）

### IPv6：Hurricane Electric 隧道

```
[Internet] ←→ [HE Tunnel] ←→ [he-ipv6] ←→ [主機 eth0]
                       |
                 2001:470:1f18:2cb::2/64
                       |
                  Incus bridge (incusbr0)
                       |
                  [容器 eth0]
```

- HE tunnel endpoint: `2001:470:1f18:2cb::2/64`
- 閘道: `2001:470:1f18:2cb::1`
- 容器 IPv6 分配: `2001:470:1f18:2cb::(4..254)`

### IPv6 容器直掛（nsenter 技巧）

**為什麼不用 bridge 原生 DHCP/SLAAC？**
Incus bridge 預設 IPv6 DHCP/SLAAC 無法把 HE tunnel 的 /64 網段直接派給容器。所以用 nsenter 手動掛：

1. `incus list` 拿容器 PID
2. `nsenter -t $PID -n -- ip addr add <v6>/128 dev eth0`
3. 主機加路由: `ip route add <v6>/128 dev incusbr0`
4. ip6tables ACCEPT: `ip6tables -I FORWARD -d <v6> -j ACCEPT`

**這個方法 vs macvlan/ipvlan：**
- HE tunnel (sit) 不支援 macvlan/ipvlan
- nsenter 直接掛 address 到容器 eth0，保留 bridge 內網管理 IP
- 容器仍可透過 incusbr0 IPv4 被管理 (`incus exec`)

### IPv4（無）

- 主機有公網 IPv4，但容器只有 Incus bridge 內網 (`10.10.0.0/22`)
- 沒設 DNAT，所以容器 IPv4 不對外
- 管理連線走 `incus exec`（socket，不需網路）

### 配額（iptables quota）

- 每個容器雙向各 500GB/月
- IPv4 和 IPv6 分開配額（各 500GB）
- Cron 每月 1 日 0:00 重置
- 指令範例：`iptables -R DOCKER 1 -d <v4> -m quota --quota 536870912000 -j ACCEPT`

## Container 建立流程

```
POST /api/instances/create
  ├─ 1. generate_password() — 12 碼亂數密碼
  ├─ 2. next_ipv6() — 從 DB 找下一個可用 IPv6 地址
  ├─ 3. create_incus_instance()
  │     ├─ incus init (image, CPU, RAM, disk, privileged)
  │     ├─ incus config device set eth0 ipv6.address
  │     └─ incus start
  ├─ 4. 等待容器就緒 → incus exec 設 root 密碼 (chpasswd)
  ├─ 5. nsenter 掛 IPv6 到容器 eth0
  ├─ 6. 主機加路由 + ip6tables ACCEPT
  └─ 7. 寫入 DB (instance_id, 密碼明文存, IPv6)
```

**密碼處理：**
- 隨機產生 12 碼，明文存入 DB `password_hash` 欄位
- 容器內用 `echo 'root:{password}' | chpasswd` 設定
- 容器密碼存在 DB，不留主機檔案

**next_ipv6() 實作：**
```python
# 從 2001:470:1f18:2cb::4 開始（::1 HE閘道, ::2 主機, ::3 Docker alpine-box）
# 檢查 DB 中已分配的 IPv6，取第一個未使用的
for i in range(4, 255):
    ip = f"2001:470:1f18:2cb::{i:x}"
    if ip not in used_ips:
        return ip
```

## Incus 配置

### Default Profile
```
config: {}
devices:
  root:
    path: /
    pool: default
    type: disk
```

- Storage pool: ZFS (`default`, ~28.6GB 總空間)
- 鏡像: `images:ubuntu/25.10`（可換，目前以此為主）
- 容器預設規格: 0.2 CPU / 256MB RAM / 5GB disk

### 注意：privileged container
- `security.privileged=true` — 要用 nsenter 掛 IPv6 需要 privileged
- 若需要更嚴格的隔離，可改用 unprivileged + 其他 IPv6 方案（如 bridge proxy）

## API 一覽

| 方法 | 路徑 | 說明 | 認證 |
|------|------|------|------|
| GET | `/api/plans` | 方案列表 | 無 |
| GET | `/api/user/profile` | 使用者資訊 | Session |
| GET | `/api/instances` | 使用者的實例列表 | Session |
| POST | `/api/instances/create` | 建立實例 | Session |
| POST | `/api/instances/{id}/action` | 啟動/停止/重啟 | Session |
| POST | `/api/instances/{id}/delete` | 刪除實例 | Session |
| GET | `/api/instances/{id}/metrics` | 即時資源用量 | Session |
| GET | `/api/admin/instances` | 所有實例 | Admin |
| GET | `/api/admin/users` | 所有使用者 | Admin |

## Systemd 服務

```
[Unit]
Description=VPS Platform (FastAPI)
After=network.target incus.service

[Service]
Type=simple
ExecStart=/usr/bin/python3 -m uvicorn main:app --host 0.0.0.0 --port 8081
WorkingDirectory=/opt/vps-platform
Restart=always
RestartSec=5
User=root

[Install]
WantedBy=multi-user.target
```

- 服務名稱: `vps-platform.service`
- 注意 `After=incus.service`，確保 Incus 在平台之前啟動

## Caddy 配置

```
docker-vps.xn--acg-4i2f.xyz {
    reverse_proxy localhost:8081
    log { ... }
}
```

- TLS 自動 Let's Encrypt（Caddy 自動處理）
- `/etc/caddy/Caddyfile`

## 已知陷阱

1. **Starlette >= 1.2.0 TemplateResponse API 變更**
   - `templates.TemplateResponse(request, "template.html", {...})`
   - **不是** `templates.TemplateResponse("template.html", {...})`
   - 第一個參數必須是 `request` 物件

2. **`incus list --format=json -c4` 的 JSON 結構**
   - 不同版本的 Incus 回傳格式可能不同
   - 安全起見用文本 `grep PID` 取代 JSON parse

3. **密碼設定的 IPv4 依賴陷阱**
   - `incus exec` 走 socket，不依賴容器是否有 IPv4
   - 之前程式碼被 `if v4:` 擋住 → 改為直接 exec

4. **HE tunnel sit 不支援 macvlan/ipvlan**
   - 不要嘗試把 HE tunnel 掛到 Docker macvlan 網路
   - 唯一有效方案：bridge + nsenter

5. **容器重啟後 IPv6 副地址會消失**
   - nsenter 加的 `/128` 地址不是永久的
   - 容器重啟後需要重新執行 nsenter + route + ip6tables
   - 目前 `api_instance_action("start")` 沒有自動補 IPv6 route
   - **TODO**: 啟動動作需要補 nsenter + route + ip6tables

6. **舊 uvicorn process 殘留**
   - `pkill -f uvicorn` 可能殺不乾淨（同名稱但不同 PID）
   - 正確方式：`fuser -k 8081/tcp` 或 `kill -9` 指定 PID
   - 舊 process 會導致 Content-Type 錯誤（json vs html）

## 部署指令

```bash
# 啟動
systemctl enable --now vps-platform

# 重啟
systemctl restart vps-platform

# 查看日誌
journalctl -u vps-platform -f

# 殺 port
fuser -k 8081/tcp

# 手動啟動（除錯用）
cd /opt/vps-platform && python3 -m uvicorn main:app --host 0.0.0.0 --port 8081
```

## GitHub

- Repo: `diacg-longxiadi/vps-platform`
- Remote (SSH): `git@github.com:diacg-longxiadi/vps-platform.git`
- SSH key 在甲骨文 `/root/.ssh/id_github`
- 開發在本機編輯，SCP 上傳，或直接在甲骨文 git commit/push

## 待辦事項

1. 容器重啟自動補 IPv6 route（`api_instance_action` for `start`）
2. 刪除容器時清除主機路由 + ip6tables 規則
3. Incus 配額 cron 腳本（每月重置）
4. 錢包轉移功能（目前是佔位頁面）
5. 註冊驗證信（SMTP config 已設，驗證路由已完成，但功能被簡化跳過）
6. 未驗證郵箱不能登入的檢查

---

## 🧠 Hermes 分析筆記

> 以下為 2026-05-29 系統分析後補充

### 整體架構評價

專案用 FastAPI + Jinja2 做 SSR 介面 + REST API，Incus system container 做虛擬化，HE IPv6 tunnel nsenter 直掛 — 技術路線正確，Oracle 免費機上跑輕量 VPS 平台完全可行。

核心價值：在 Oracle 1C/1G 上榨出多個 IPv6 容器，配合 HE tunnel 的 routed /64 給每個容器獨立公網 IPv6。

### 發現的問題

1. **ip6tables quota 沒設定 alpine-box**
   - FORWARD chain 的 quota 規則全是針對 `::5`（Incus 容器），alpine-box 直掛 `::3` 完全沒流量限制
   - reset-quota.sh 有 `::3` 規則但實際沒插入成功（DOCKER chain 沒查到該規則）

2. **容器重啟後 IPv6 自動修復缺失**
   - nsenter 掛的 `/128` 在容器重啟後消失
   - `api_instance_action("start")` 沒有補 nsenter + route + ip6tables
   - 這是已知 TODO 但一直沒修

3. **密碼明文存 DB**
   - `password_hash` 欄位存的不是 hash 是明文
   - 容器 password 用 `chpasswd` 設定後明文存 DB
   - 雖然是系統密碼不是使用者密碼，但仍有洩漏風險

4. **SESSION_SECRET 是假值**
   - `SESSION_SECRET=secret...(32)` 是佔位符
   - 運行中的服務必須用真實 secret，但這個真實值不在 repo 裡
   - 需要確認真實值存在哪裡（可能寫在 main.py 已部署的版本）

5. **EMail 驗證跳過但路由還在**
   - `verify_tokens` 表已建，SMTP config 設好，但註冊流程簡化跳過驗證
   - 路由 `/verify/{token}` 存在但實際無作用

6. **殘留容器清理問題**
   - 創建容器時若後續步驟（密碼/IPv6/DB）失敗，會留下殘留 Incus 容器
   - 同名稱下次建立會失敗（已存在），但前置 `incus delete --force` 已加

### 開發建議

- **IPv6 路徑**：容器重啟→補 nsenter+route+iptables 這塊一定要做，否則容器重啟即失聯
- **配額系統**：alpine-box 的 quota 要修，可整合到 container-monitor 面板
- **密碼管理**：密碼改用容器啟動腳本傳遞，或至少用環境變數而非 DB 明文
- **前端**：目前是 Jinja2 SSR，如果要切 SPA 需要重構前端的 session 管理
- **測試**：沒有測試覆蓋率，至少 Incus 操作要 mock 測試