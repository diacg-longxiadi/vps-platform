# 帝云 API 接口文檔

## 基本資訊

| 項目 | 值 |
|------|-----|
| 後端框架 | FastAPI (Python) |
| 運行端口 | 8081（內部） |
| 外部網址 | https://docker-vps.xn--acg-4i2f.xyz |
| 認證方式 | Session Cookie（httponly） |
| Session 有效期 | 7 天 |
| 資料庫 | SQLite (`/opt/vps-platform/data/vps.db`) |
| 容器引擎 | Incus 6.22 |

## 頁面路由（服務端渲染）

後端使用 Jinja2 模板 + FastAPI，前端若需 SPA 重構，這些路由仍需保留作為跳轉或需要修改。

| 路由 | 方法 | 說明 | 需要登入 |
|------|------|------|---------|
| `/` | GET | 首頁（landing page） | 否 |
| `/register` | GET | 註冊頁面 | 否 |
| `/register` | POST | 提交註冊 | 否 |
| `/login` | GET | 登入頁面 | 否 |
| `/login` | POST | 提交登入 | 否 |
| `/logout` | GET | 登出 | 否 |
| `/verify/{token}` | GET | Email 驗證 | 否 |
| `/dashboard` | GET | 使用者主控台 | 是 |
| `/instance/{id}` | GET | 實例詳情 | 是（或管理員） |
| `/admin` | GET | 管理員面板 | 是（管理員） |

## API 路由（JSON）

這些是無需頁面渲染的純 API 端點，返回 JSON。

### 建立實例
```
POST /api/instances/create
Content-Type: application/x-www-form-urlencoded
Cookie: session=<token>

name=my-instance&cpu=0.2&ram_mb=256&disk_gb=5
```
回應：
```json
{
  "id": 1,
  "name": "my-instance",
  "incus_name": "vps-1-my-instance",
  "ipv4": "10.10.0.5",
  "ipv6": "2001:470:1f18:2cb::5",
  "password": "a1b2c3d4e5f6",
  "cpu": 0.2,
  "ram_mb": 256,
  "disk_gb": 5
}
```

### 操作實例（啟動/停止/重啟）
```
POST /api/instances/{id}/action
Content-Type: application/x-www-form-urlencoded
Cookie: session=<token>

action=start|stop|restart
```
回應：
```json
{ "status": "running" }
```

### 刪除實例
```
POST /api/instances/{id}/delete
Cookie: session=<token>
```
回應：
```json
{ "status": "deleted" }
```

### 獲取實例指標
```
GET /api/instances/{id}/metrics
Cookie: session=<token>
```
回應：
```json
{
  "cpu": { "usage": 123456789 },
  "memory": { "usage": 52428800, "usage_peak": 104857600 },
  "disk": { "usage": 1073741824 },
  "network": { "eth0": { "bytes_sent": 1024, "bytes_received": 2048 } },
  "processes": 12
}
```

### 管理員：所有實例
```
GET /api/admin/instances
Cookie: session=<token>
```
回應：
```json
[
  {
    "id": 1,
    "name": "my-instance",
    "status": "running",
    "cpu": 0.2,
    "ram_mb": 256,
    "disk_gb": 5,
    "ipv4": "10.10.0.5",
    "ipv6": "2001:470:1f18:2cb::5",
    "created_at": "2025-05-29 03:00:00",
    "owner": "user@example.com"
  }
]
```

### 管理員：所有使用者
```
GET /api/admin/users
Cookie: session=<token>
```
回應：
```json
[
  {
    "id": 1,
    "email": "user@example.com",
    "is_admin": false,
    "credits": 0,
    "created_at": "2025-05-29 02:00:00"
  }
]
```

## 認證方式

### 登入（取得 session）
```
POST /login
Content-Type: application/x-www-form-urlencoded

email=user@example.com&password=xxxxxx
```
成功時回傳 303 Redirect 到 `/dashboard`，並設定 Cookie `session=<token>`（httponly, 7天有效期）。

### API 請求認證
所有 API 請求需帶上 Cookie：
```
Cookie: session=<token>
```
若未認證回傳 `401 Unauthorized`。

## 資料庫結構

### users 表
| 欄位 | 類型 | 說明 |
|------|------|------|
| id | INTEGER PK | 使用者 ID |
| email | TEXT UNIQUE | Email |
| password_hash | TEXT | bcrypt 雜湊 |
| is_admin | INTEGER | 是否管理員（0/1） |
| is_verified | INTEGER | 是否驗證 Email（0/1） |
| credits | REAL | 餘額 |
| created_at | TEXT | 建立時間 |

### instances 表
| 欄位 | 類型 | 說明 |
|------|------|------|
| id | INTEGER PK | 實例 ID |
| user_id | INTEGER FK | 擁有者 |
| name | TEXT | 使用者命名 |
| incus_name | TEXT UNIQUE | Incus 容器名稱 |
| status | TEXT | running / stopped |
| cpu | REAL | CPU 核心數 |
| ram_mb | INTEGER | 記憶體 MB |
| disk_gb | INTEGER | 儲存空間 GB |
| ipv4 | TEXT | IPv4 地址 |
| ipv6 | TEXT | IPv6 地址 |
| quota_gb | INTEGER | 每月流量配額 |
| password_hash | TEXT | root 密碼（明文存於 `/root/.<name>-pass` 及此欄位） |
| created_at | TEXT | 建立時間 |

### sessions 表
| 欄位 | 類型 | 說明 |
|------|------|------|
| token | TEXT PK | Session Token |
| user_id | INTEGER FK | 使用者 ID |
| created_at | TEXT | 建立時間 |

### verify_tokens 表
| 欄位 | 類型 | 說明 |
|------|------|------|
| id | INTEGER PK | |
| user_id | INTEGER FK | 使用者 ID |
| token | TEXT UNIQUE | 驗證 Token |
| created_at | TEXT | 建立時間（24小時有效） |

## 容器規格

| 方案 | CPU | 記憶體 | 儲存 | 流量/月 |
|------|-----|--------|------|---------|
| 入門 | 0.2 核心 | 256 MB | 5 GB | 500 GB |
| 標準 | 1 核心 | 1 GB | 10 GB | 1 TB |
| 專業 | 2 核心 | 2 GB | 20 GB | 2 TB |

## 網路架構

- **IPv4**：NAT 模式（Incus bridge），由主機 DNAT 轉發
- **IPv6**：HE tunnel routed /64，直接掛到容器 eth0（全端口開放）
- **流量配額**：iptables/ip6tables quota 模組，每月 1 日 cron 重置

## 範例：前端 SPA 整合

後端當前使用 Jinja2 服務端渲染。若要改為 SPA：

1. 保持 `/login` POST 和 `/register` POST 作為認證入口（返回 JSON 或設定 Cookie）
2. 前端使用 `/api/` 路由操作資源
3. 靜態檔案放在 `/opt/vps-platform/static/`，透過 `/static/` 路徑訪問
4. Session Cookie 由後端設定，前端 fetch 需帶 `credentials: 'include'`

### 當前前端模板位置

```
/opt/vps-platform/templates/
  base.html      — 基礎模板（nav + main 結構）
  index.html     — 首頁（landing page）
  login.html     — 登入
  register.html  — 註冊
  dashboard.html — 主控台
  instance.html  — 實例詳情
  admin.html     — 管理員面板

/opt/vps-platform/static/
  style.css      — 樣式表
```
