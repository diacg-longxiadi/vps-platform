#!/usr/bin/env python3
"""VPS 管理平台 - FastAPI 應用"""

import asyncio, subprocess, re, secrets, hashlib
from datetime import datetime, timedelta
from pathlib import Path
from fastapi import FastAPI, Request, Form, HTTPException, Depends, Cookie, Response
from fastapi.responses import HTMLResponse, RedirectResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from passlib.hash import bcrypt
import aiosqlite
import smtplib, json
from email.mime.text import MIMEText

# ── 設定 ──
DB_PATH = "/opt/vps-platform/data/vps.db"
SESSION_SECRET = secrets.token_hex(32)
INCUS_BRIDGE = "incusbr0"
INCUS_NET = "10.10.0.0/22"
INCUS_GW = "10.10.0.1"
HE_V6_PREFIX = "2001:470:1f18:2cb"
QUOTA_GB = 500
QUOTA_BYTES = 500 * 1024**3

# ── FastAPI ──
app = FastAPI(title="VPS Platform")
templates = Jinja2Templates(directory="/opt/vps-platform/templates")
app.mount("/static", StaticFiles(directory="/opt/vps-platform/static"), name="static")

# ── 資料庫初始化 ──
async def init_db():
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("""
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                email TEXT UNIQUE NOT NULL,
                password_hash TEXT NOT NULL,
                is_admin INTEGER DEFAULT 0,
                credits REAL DEFAULT 0,
                is_verified INTEGER DEFAULT 0,
                created_at TEXT DEFAULT (datetime('now'))
            )
        """)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS instances (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                name TEXT NOT NULL,
                incus_name TEXT UNIQUE NOT NULL,
                status TEXT DEFAULT 'stopped',
                cpu REAL DEFAULT 0.2,
                ram_mb INTEGER DEFAULT 256,
                disk_gb INTEGER DEFAULT 5,
                ipv4 TEXT,
                ipv6 TEXT,
                quota_gb INTEGER DEFAULT 500,
                password_hash TEXT,
                created_at TEXT DEFAULT (datetime('now')),
                FOREIGN KEY (user_id) REFERENCES users(id)
            )
        """)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS verify_tokens (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                token TEXT UNIQUE NOT NULL,
                created_at TEXT DEFAULT (datetime('now')),
                FOREIGN KEY (user_id) REFERENCES users(id)
            )
        """)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS sessions (
                token TEXT PRIMARY KEY,
                user_id INTEGER NOT NULL,
                created_at TEXT DEFAULT (datetime('now')),
                FOREIGN KEY (user_id) REFERENCES users(id)
            )
        """)
        await db.commit()

        # 檢查是否有預設管理員
        cursor = await db.execute("SELECT id FROM users WHERE email=?", ("admin@vps.local",))
        if not await cursor.fetchone():
            h = bcrypt.hash("admin123")
            await db.execute("INSERT INTO users (email, password_hash, is_admin) VALUES (?, ?, 1)",
                           ("admin@vps.local", h))
            await db.commit()
            print("預設管理員: admin@vps.local / admin123")

@app.on_event("startup")
async def startup():
    await init_db()


# ── 工具函式 ──

def run_cmd(cmd, timeout=30):
    """執行 shell 指令"""
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        return r.stdout.strip(), r.stderr.strip(), r.returncode
    except subprocess.TimeoutExpired:
        return "", "timeout", -1
    except Exception as e:
        return "", str(e), -1


def next_ipv6():
    """從 HE /64 找下一個可用 IPv6 地址"""
    used = set()
    out, _, _ = run_cmd(["incus", "list", "-c6", "--format=json"])
    import json
    try:
        data = json.loads(out)
        for item in data:
            for k, v in item.get("state", {}).get("network", {}).items():
                for addr in v.get("addresses", []):
                    if addr.get("family") == "inet6" and addr.get("scope") == "global":
                        used.add(addr.get("address", "").split("/")[0])
    except: pass

    for i in range(5, 255):
        v6 = f"{HE_V6_PREFIX}::{i:x}"
        if v6 not in used:
            return v6
    return None


def generate_password():
    """隨機密碼"""
    return secrets.token_hex(12)


# ── Email 驗證 ──

def load_smtp_config():
    """載入 SMTP 設定"""
    try:
        with open("/root/smtp_config.json") as f:
            return json.load(f)
    except:
        return {}

def send_verify_email(to_email, token):
    """發送驗證郵件"""
    cfg = load_smtp_config()
    if not cfg.get("require_verification", True):
        return True

    verify_url = "https://docker-vps.xn--acg-4i2f.xyz/verify/" + token
    subject = "VPS Platform - 請驗證您的 Email"
    body = f"""您好，\n\n感謝您註冊 VPS Platform！\n\n請點擊以下連結驗證您的 Email：\n{verify_url}\n\n連結有效 24 小時。\n\nVPS Platform 團隊"""
    msg = MIMEText(body, "plain", "utf-8")
    msg["Subject"] = subject
    msg["From"] = cfg.get("from_email", "noreply@vps.local")
    msg["To"] = to_email

    try:
        with smtplib.SMTP(cfg.get("host", "smtp.gmail.com"), cfg.get("port", 587), timeout=15) as s:
            s.ehlo()
            s.starttls()
            s.ehlo()
            s.login(cfg["user"], cfg["password"])
            s.send_message(msg)
        return True
    except Exception as e:
        print(f"[email] send failed: {e}")
        return False

async def is_user_verified(user_id) -> bool:
    """檢查使用者是否已驗證"""
    cfg = load_smtp_config()
    if not cfg.get("require_verification", True):
        return True
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute("SELECT is_verified FROM users WHERE id=?", (user_id,))
        row = await cursor.fetchone()
        return bool(row and row[0] == 1)

async def get_current_user(request: Request):
    """從 session cookie 取得目前使用者"""
    token = request.cookies.get("session")
    if not token:
        return None
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute("""
            SELECT u.id, u.email, u.is_admin, u.credits
            FROM sessions s JOIN users u ON s.user_id = u.id
            WHERE s.token=?
        """, (token,))
        row = await cursor.fetchone()
        if row:
            return {"id": row[0], "email": row[1], "is_admin": bool(row[2]), "credits": row[3]}
    return None


# ── Incus 管理 ──

async def create_incus_instance(name, image="images:ubuntu/25.10", cpu=0.2, ram_mb=256, disk_gb=5, ipv6=""):
    """透過 incus CLI 建立容器"""
    # 建立容器
    stdout, stderr, rc = run_cmd([
        "incus", "init", image, name,
        "--config", f"limits.cpu.allowance={int(cpu*100)}%",
        "--config", f"limits.memory={ram_mb}MiB",
        "--config", "security.privileged=true",
        "--config", "user.vendor-data=",  # 空 cloud-init
    ], timeout=120)
    if rc != 0:
        return False, stderr or stdout

    # 修改網路設定 - 加入 IPv6
    if ipv6:
        run_cmd(["incus", "config", "device", "set", name, "eth0", "ipv6.address", ipv6])

    # 啟動
    stdout, stderr, rc = run_cmd(["incus", "start", name], timeout=60)
    if rc != 0:
        return False, stderr or stdout

    # 等待取得 IPv4
    v4 = ""
    for _ in range(10):
        await asyncio.sleep(2)
        out, _, _ = run_cmd(["incus", "list", name, "--format=json", "-c4"])
        try:
            import json
            data = json.loads(out)
            if data:
                for k, v in data[0].get("state", {}).get("network", {}).items():
                    for addr in v.get("addresses", []):
                        if addr.get("family") == "inet" and addr.get("scope") == "global":
                            v4 = addr.get("address", "").split("/")[0]
                            break
                if v4:
                    break
        except: pass

    return True, {"ipv4": v4, "ipv6": ipv6, "name": name}


async def delete_incus_instance(incus_name):
    """刪除 incus 容器"""
    run_cmd(["incus", "stop", incus_name, "--force"], timeout=30)
    stdout, stderr, rc = run_cmd(["incus", "delete", incus_name], timeout=30)
    return rc == 0, stderr or stdout


# ── 路由：頁面 ──

@app.get("/", response_class=HTMLResponse)
async def index(request: Request, user=Depends(get_current_user)):
    if user:
        return RedirectResponse(url="/dashboard")
    return templates.TemplateResponse("index.html", {"request": request})


@app.get("/register", response_class=HTMLResponse)
async def register_page(request: Request):
    return templates.TemplateResponse("register.html", {"request": request})


@app.post("/register")
async def register(request: Request, email: str = Form(...), password: str = Form(...)):
    if not email or not password or len(password) < 6:
        return templates.TemplateResponse("register.html", {
            "request": request, "error": "密碼至少 6 碼"
        })
    password_hash = bcrypt.hash(password)
    try:
        async with aiosqlite.connect(DB_PATH) as db:
            await db.execute("INSERT INTO users (email, password_hash) VALUES (?, ?)",
                           (email, password_hash))
            await db.commit()
        return templates.TemplateResponse("login.html", {
            "request": request, "success": "註冊成功！請登入"
        })
    except Exception as e:
        if "UNIQUE" in str(e):
            return templates.TemplateResponse("register.html", {
                "request": request, "error": "此 Email 已註冊"
            })
        return templates.TemplateResponse("register.html", {
            "request": request, "error": "註冊失敗"
        })


@app.get("/verify/{token}")
async def verify_email(request: Request, token: str):
    """驗證 Email"""
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute(
            "SELECT user_id FROM verify_tokens WHERE token=? AND created_at > datetime('now', '-1 day')",
            (token,))
        row = await cursor.fetchone()
        if row:
            user_id = row[0]
            await db.execute("UPDATE users SET is_verified=1 WHERE id=?", (user_id,))
            await db.execute("DELETE FROM verify_tokens WHERE token=?", (token,))
            await db.commit()
            return RedirectResponse(url="/login?verified=1")
        cursor2 = await db.execute(
            "SELECT user_id FROM verify_tokens WHERE token=?", (token,))
        row2 = await cursor2.fetchone()
        if row2:
            return templates.TemplateResponse("register.html", {
                "request": request, "error": "\u9a57\u8b49\u9023\u7d50\u5df2\u904e\u671f(24 \u5c0f\u6642)\uff0c\u8acb\u91cd\u65b0\u8a3b\u518a\u3002"
            })
        return templates.TemplateResponse("register.html", {
            "request": request, "error": "\u7121\u6548\u7684\u9a57\u8b49\u9023\u7d50\u3002"
        })

@app.get("/login", response_class=HTMLResponse)
async def login_page(request: Request, verified: str = ""):
    extra = {}
    if verified == "1":
        extra["success"] = "Email \u9a57\u8b49\u6210\u529f\uff01\u8acb\u767b\u5165"
    return templates.TemplateResponse("login.html", {"request": request, **extra})


@app.post("/login")
async def login(request: Request, account: str = Form(...), password: str = Form(...)):
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute("SELECT id, password_hash FROM users WHERE email=?", (account,))
        row = await cursor.fetchone()
        if row and bcrypt.verify(password, row[1]):
            cfg = load_smtp_config()
            if cfg.get("require_verification", True):
                cur2 = await db.execute("SELECT is_verified FROM users WHERE id=?", (row[0],))
                vrow = await cur2.fetchone()
                if not vrow or vrow[0] != 1:
                    return templates.TemplateResponse("login.html", {
                        "request": request, "error": "\u8acb\u5148\u9a57\u8b49\u60a8\u7684 Email \u5f8c\u518d\u767b\u5165"
                    })
            token = secrets.token_hex(32)
            await db.execute("INSERT INTO sessions (token, user_id) VALUES (?, ?)",
                           (token, row[0]))
            await db.commit()
            resp = RedirectResponse(url="/dashboard", status_code=303)
            resp.set_cookie(key="session", value=token, max_age=86400*7, httponly=True)
            return resp
    return templates.TemplateResponse("login.html", {
        "request": request, "error": "Email 或密碼錯誤"
    })


@app.get("/logout")
async def logout(response: RedirectResponse = None):
    resp = RedirectResponse(url="/")
    resp.delete_cookie("session")
    return resp


@app.get("/dashboard", response_class=HTMLResponse)
async def dashboard(request: Request, user=Depends(get_current_user)):
    if not user:
        return RedirectResponse(url="/login")
    
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute(
            "SELECT id, name, status, cpu, ram_mb, disk_gb, ipv4, ipv6, quota_gb, created_at FROM instances WHERE user_id=? ORDER BY created_at DESC",
            (user["id"],))
        instances = await cursor.fetchall()
    
    return templates.TemplateResponse("dashboard.html", {
        "request": request,
        "user": user,
        "instances": [
            {"id": r[0], "name": r[1], "status": r[2], "cpu": r[3], "ram_mb": r[4],
             "disk_gb": r[5], "ipv4": r[6] or "—", "ipv6": r[7] or "—",
             "quota_gb": r[8], "created_at": r[9]}
            for r in instances
        ]
    })


@app.get("/instance/{inst_id}", response_class=HTMLResponse)
async def instance_detail(request: Request, inst_id: int, user=Depends(get_current_user)):
    if not user:
        return RedirectResponse(url="/login")
    
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute(
            "SELECT * FROM instances WHERE id=? AND user_id=?",
            (inst_id, user["id"]))
        row = await cursor.fetchone()
        if not row:
            # 管理員可看全部
            if user["is_admin"]:
                cursor = await db.execute(
                    "SELECT * FROM instances WHERE id=?", (inst_id,))
                row = await cursor.fetchone()
            if not row:
                raise HTTPException(404)
    
    inst = {
        "id": row[0], "user_id": row[1], "name": row[2], "incus_name": row[3],
        "status": row[4], "cpu": row[5], "ram_mb": row[6], "disk_gb": row[7],
        "ipv4": row[8] or "—", "ipv6": row[9] or "—", "quota_gb": row[10],
        "password": row[11] or "未設定", "created_at": row[12]
    }
    
    # 取得即時狀態
    out, _, _ = run_cmd(["incus", "info", inst["incus_name"], "--format=json"])
    live = {}
    try:
        import json
        live = json.loads(out) if out else {}
    except: pass
    
    return templates.TemplateResponse("instance.html", {
        "request": request, "user": user, "inst": inst, "live": live
    })


# ── API 路由 ──

@app.post("/api/instances/create")
async def api_create_instance(request: Request, user=Depends(get_current_user)):
    if not user:
        raise HTTPException(401)
    
    form = await request.form()
    name = form.get("name", "").strip()
    cpu = float(form.get("cpu", 0.2))
    ram_mb = int(form.get("ram_mb", 256))
    disk_gb = int(form.get("disk_gb", 5))
    
    if not name or not re.match(r'^[a-z0-9][a-z0-9-]{1,31}$', name):
        return JSONResponse({"error": "名稱限小寫字母數字連字號，2-32 字元"}, status_code=400)
    
    incus_name = f"vps-{user['id']}-{name}"
    password = generate_password()
    ipv6 = next_ipv6()
    
    # 建立 incus 容器
    success, result = await create_incus_instance(
        incus_name, image="images:ubuntu/25.10",
        cpu=cpu, ram_mb=ram_mb, disk_gb=disk_gb, ipv6=ipv6
    )
    
    if not success:
        return JSONResponse({"error": f"建立失敗: {result}"}, status_code=500)
    
    v4 = result.get("ipv4", "")
    v6 = result.get("ipv6", ipv6 or "")
    
    # 設定容器 root 密碼
    if v4:
        for attempt in range(10):
            await asyncio.sleep(3)
            out, _, _ = run_cmd(["incus", "exec", incus_name, "--", "which", "passwd"], timeout=10)
            if "passwd" in out:
                run_cmd(["incus", "exec", incus_name, "--", "bash", "-c",
                        f"echo 'root:{password}' | chpasswd"], timeout=10)
                break
    
    # 寫入資料庫
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute("""
            INSERT INTO instances (user_id, name, incus_name, status, cpu, ram_mb, disk_gb, ipv4, ipv6, quota_gb, password_hash)
            VALUES (?, ?, ?, 'running', ?, ?, ?, ?, ?, ?, ?)
        """, (user["id"], name, incus_name, cpu, ram_mb, disk_gb, v4, v6, QUOTA_GB, password))
        inst_id = cursor.lastrowid
        await db.commit()
    
    return JSONResponse({
        "id": inst_id, "name": name, "incus_name": incus_name,
        "ipv4": v4, "ipv6": v6, "password": password,
        "cpu": cpu, "ram_mb": ram_mb, "disk_gb": disk_gb
    })


@app.post("/api/instances/{inst_id}/action")
async def api_instance_action(inst_id: int, request: Request, user=Depends(get_current_user)):
    if not user:
        raise HTTPException(401)
    
    form = await request.form()
    action = form.get("action", "")
    
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute(
            "SELECT incus_name, status FROM instances WHERE id=? AND user_id=?",
            (inst_id, user["id"]))
        row = await cursor.fetchone()
        # 管理員可操作任何實例
        if not row and user["is_admin"]:
            cursor = await db.execute(
                "SELECT incus_name, status FROM instances WHERE id=?", (inst_id,))
            row = await cursor.fetchone()
        if not row:
            raise HTTPException(404)
    
    incus_name, status = row
    
    if action == "start":
        run_cmd(["incus", "start", incus_name], timeout=30)
        new_status = "running"
    elif action == "stop":
        run_cmd(["incus", "stop", incus_name, "--force"], timeout=30)
        new_status = "stopped"
    elif action == "restart":
        run_cmd(["incus", "restart", incus_name], timeout=30)
        new_status = "running"
    else:
        raise HTTPException(400, "無效操作")
    
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("UPDATE instances SET status=? WHERE id=?", (new_status, inst_id))
        await db.commit()
    
    return JSONResponse({"status": new_status})


@app.post("/api/instances/{inst_id}/delete")
async def api_delete_instance(inst_id: int, user=Depends(get_current_user)):
    if not user:
        raise HTTPException(401)
    
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute(
            "SELECT incus_name, user_id FROM instances WHERE id=?", (inst_id,))
        row = await cursor.fetchone()
        if not row:
            raise HTTPException(404)
        incus_name, owner_id = row
        if owner_id != user["id"] and not user["is_admin"]:
            raise HTTPException(403)
        
        await delete_incus_instance(incus_name)
        await db.execute("DELETE FROM instances WHERE id=?", (inst_id,))
        await db.commit()
    
    return JSONResponse({"status": "deleted"})


@app.get("/api/instances/{inst_id}/metrics")
async def api_instance_metrics(inst_id: int, user=Depends(get_current_user)):
    if not user:
        raise HTTPException(401)
    
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute(
            "SELECT incus_name FROM instances WHERE id=? AND user_id=?",
            (inst_id, user["id"]))
        row = await cursor.fetchone()
        if not row:
            if user["is_admin"]:
                cursor = await db.execute(
                    "SELECT incus_name FROM instances WHERE id=?", (inst_id,))
                row = await cursor.fetchone()
            if not row:
                raise HTTPException(404)
    
    incus_name = row[0]
    out, _, _ = run_cmd(["incus", "info", incus_name, "--format=json"])
    metrics = {"cpu": {}, "memory": {}, "disk": {}, "network": {}, "processes": 0}
    try:
        import json
        data = json.loads(out) if out else {}
        resources = data.get("resources", {})
        metrics["cpu"] = resources.get("cpu", {})
        metrics["memory"] = resources.get("memory", {})
        metrics["disk"] = resources.get("disk", {})
        metrics["processes"] = data.get("processes", 0)
    except: pass
    
    return JSONResponse(metrics)


@app.get("/api/admin/instances")
async def api_admin_instances(user=Depends(get_current_user)):
    if not user or not user["is_admin"]:
        raise HTTPException(403)
    
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute("""
            SELECT i.id, i.name, i.status, i.cpu, i.ram_mb, i.disk_gb, i.ipv4, i.ipv6,
                   i.created_at, u.email
            FROM instances i JOIN users u ON i.user_id = u.id
            ORDER BY i.created_at DESC
        """)
        rows = await cursor.fetchall()
    
    return JSONResponse([{
        "id": r[0], "name": r[1], "status": r[2], "cpu": r[3], "ram_mb": r[4],
        "disk_gb": r[5], "ipv4": r[6], "ipv6": r[7], "created_at": r[8], "owner": r[9]
    } for r in rows])


@app.get("/api/admin/users")
async def api_admin_users(user=Depends(get_current_user)):
    if not user or not user["is_admin"]:
        raise HTTPException(403)
    
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute(
            "SELECT id, email, is_admin, credits, created_at FROM users ORDER BY id")
        rows = await cursor.fetchall()
    
    return JSONResponse([{
        "id": r[0], "email": r[1], "is_admin": bool(r[2]),
        "credits": r[3], "created_at": r[4]
    } for r in rows])


# ── 管理員頁面 ──

@app.get("/admin", response_class=HTMLResponse)
async def admin_page(request: Request, user=Depends(get_current_user)):
    if not user or not user["is_admin"]:
        return RedirectResponse(url="/")
    return templates.TemplateResponse("admin.html", {"request": request, "user": user})


# ── 啟動 ──

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="0.0.0.0", port=8081, reload=True)
