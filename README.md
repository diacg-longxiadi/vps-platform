# 帝云 — 基於 Incus 的 NAT VPS 平台

輕量級 VPS 管理平台，基於 Incus 容器技術，提供 NAT IPv4 + 獨立 IPv6 VPS 實例的自助部署與管理。

## 功能

- **一鍵建立 VPS** — 選取鏡像、配置規格，秒級部署 Incus 容器
- **獨立 IPv6** — 每個實例配備獨立 IPv6 地址，全端口開放（HE tunnel /64）
- **流量配額** — 每月自動重置的雙向流量配額（iptables quota）
- **即時監控** — 即時 CPU、記憶體、磁碟、網路用量顯示
- **彈性配置** — 自訂 CPU 核心、記憶體、儲存空間
- **管理員面板** — 管理員可檢視全部實例與使用者
- **Email 驗證** — 註冊需透過 Email 驗證（Gmail SMTP）

## 技術架構

```
┌─────────────┐     ┌──────────┐     ┌─────────┐
│   Caddy     │────▶│ FastAPI  │────▶│  Incus  │
│ (HTTPS/TLS) │     │ (Uvicorn)│     │ (容器)  │
└─────────────┘     └──────────┘     └─────────┘
                           │
                    ┌──────┴──────┐
                    │   SQLite    │
                    │  (資料庫)   │
                    └─────────────┘
```

| 元件 | 技術 | 說明 |
|------|------|------|
| Web 伺服器 | Caddy 2 | TLS 終止、反向代理、自動 Let's Encrypt |
| 後端框架 | FastAPI + Uvicorn | 非同步 Python Web 框架 |
| 容器引擎 | Incus 6.22 | 系統容器隔離 |
| 資料庫 | SQLite (aiosqlite) | 輕量嵌入式資料庫 |
| 前端 | Jinja2 + CSS | 服務端渲染，無 SPA |
| IPv6 隧道 | Hurricane Electric | /64 路由到容器 eth0 |
| 郵件 | Gmail SMTP | 註冊驗證信 |

## 部署

### 環境需求

- Ubuntu 22.04+
- Python 3.10+
- Incus 6.x
- Caddy 2
- HE IPv6 tunnel（選用）

### 安裝步驟

```bash
# 1. 安裝 Incus
snap install incus
incus admin init

# 2. 建立專案目錄
mkdir -p /opt/vps-platform/{templates,static,data}
cd /opt/vps-platform

# 3. 安裝 Python 依賴
python3 -m venv venv
source venv/bin/activate
pip install fastapi uvicorn jinja2 aiosqlite passlib bcrypt python-multipart

# 4. 下載原始碼
git clone git@github.com:diacg-longxiadi/vps-platform.git /tmp/vps
cp -r /tmp/vps/* .

# 5. 設定 SMTP（選用，用於 Email 驗證）
cp smtp_config.example.json /root/smtp_config.json
# 編輯 /root/smtp_config.json 填入你的 Gmail App Password

# 6. 啟動
nohup python3 -m uvicorn main:app --host 0.0.0.0 --port 8081 &

# 7. 設定 Caddy 反向代理
# /etc/caddy/Caddyfile:
# docker-vps.your-domain.xyz {
#     reverse_proxy localhost:8081
# }
```

## API 路由

| 路由 | 方法 | 說明 |
|------|------|------|
| `/` | GET | 首頁 |
| `/register` | GET/POST | 註冊 |
| `/login` | GET/POST | 登入 |
| `/logout` | GET | 登出 |
| `/verify/{token}` | GET | Email 驗證 |
| `/dashboard` | GET | 使用者主控台 |
| `/instance/{id}` | GET | 實例詳情 |
| `/admin` | GET | 管理員面板 |
| `/api/instances/create` | POST | 建立實例 |
| `/api/instances/{id}/action` | POST | 啟動/停止/重啟 |
| `/api/instances/{id}/delete` | POST | 刪除實例 |
| `/api/instances/{id}/metrics` | GET | 實例效能指標 |
| `/api/admin/instances` | GET | 管理員：全部實例 |
| `/api/admin/users` | GET | 管理員：全部使用者 |

## 容器規格

| 方案 | CPU | 記憶體 | 儲存 | 流量/月 |
|------|-----|--------|------|---------|
| 入門 | 0.2 核心 | 256 MB | 5 GB | 500 GB |
| 標準 | 1 核心 | 1 GB | 10 GB | 1 TB |
| 專業 | 2 核心 | 2 GB | 20 GB | 2 TB |

## 網路架構

- **IPv4**：NAT 模式（Incus bridge），由主機 DNAT 轉發
- **IPv6**：HE tunnel  routed /64，直接掛到容器 eth0（全端口開放）
- **流量配額**：iptables/ip6tables quota 模組，每月 1 日 cron 重置

## 開發

```bash
# 本機開發（啟用 hot reload）
cd /opt/vps-platform
python3 -m uvicorn main:app --host 0.0.0.0 --port 8081 --reload
```

## License

MIT
