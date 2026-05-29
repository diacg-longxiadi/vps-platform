#!/usr/bin/env python3
"""輕量容器監控伺服器 — 提供 JSON API + HTML Dashboard"""
import json, subprocess, os, time
from http.server import HTTPServer, BaseHTTPRequestHandler
from socketserver import ThreadingMixIn

class ThreadingHTTPServer(ThreadingMixIn, HTTPServer):
    allow_reuse_address = True
    daemon_threads = True
from datetime import datetime, timezone

PORT = 8080
QUOTA_BYTES = 500 * 1024**3  # 500GB

def run(cmd):
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=10)
        return r.stdout.strip(), r.stderr.strip()
    except:
        return "", ""

def get_containers():
    out, _ = run(["docker", "ps", "-a", "--format", "{{.Names}}|{{.Status}}|{{.Image}}|{{.ID}}"])
    containers = []
    for line in out.splitlines():
        if not line:
            continue
        parts = line.split("|")
        name, status, image, cid = parts[0], parts[1], parts[2], parts[3]
        running = status.startswith("Up")
        containers.append({"name": name, "status": status, "running": running, "image": image, "id": cid})
    return containers

def get_stats():
    stats = {}
    out, _ = run(["docker", "stats", "--no-stream",
                  "--format", "{{.Name}}|{{.CPUPerc}}|{{.MemUsage}}|{{.MemPerc}}|{{.NetIO}}"])
    for line in out.splitlines():
        if not line or "|" not in line:
            continue
        parts = line.split("|")
        if len(parts) < 5:
            continue
        stats[parts[0]] = {"cpu": parts[1], "mem": parts[2], "mem_pct": parts[3], "net": parts[4]}
    return stats

def get_networks(containers):
    nets = {}
    ids = [c["id"] for c in containers]
    if not ids:
        return nets
    out, _ = run(["docker", "inspect"] + ids +
                 ["--format", "{{.Name}} __NET__ {{range $k,$v := .NetworkSettings.Networks}}{{$k}},{{$v.IPAddress}};{{end}}"])
    for line in out.splitlines():
        if " __NET__ " not in line:
            continue
        parts = line.split(" __NET__ ", 1)
        cname = parts[0].lstrip("/")
        nets[cname] = {}
        for seg in parts[1].strip(";").split(";"):
            if not seg:
                continue
            n, ip = seg.split(",", 1) if "," in seg else (seg, "")
            nets[cname][n] = ip
    return nets
def get_container_quota(v6_addr):
    """從 ip6tables 讀取特定容器的 IPv6 配額用量"""
    if not v6_addr:
        return None
    out, _ = run(["ip6tables", "-vL", "DOCKER", "-n", "-x"])
    for line in out.splitlines():
        if "quota" in line and "ACCEPT" in line and v6_addr in line:
            parts = line.split()
            if len(parts) >= 8:
                try:
                    return int(parts[1])
                except ValueError:
                    pass
    return None

def get_container_quota_v4(ipv4):
    """從 iptables DOCKER-USER 讀取特定容器的 IPv4 配額用量"""
    if not ipv4:
        return None
    out, _ = run(["iptables", "-vL", "DOCKER-USER", "-n", "-x"])
    total = 0
    for line in out.splitlines():
        if "quota" in line and "ACCEPT" in line and ipv4 in line:
            parts = line.split()
            if len(parts) >= 8:
                try:
                    total += int(parts[1])
                except ValueError:
                    pass
    return total if total > 0 else None

def get_host_info():
    disk_out, _ = run(["df", "-h", "--output=size,used,avail,pcent", "/"])
    mem_out, _ = run(["free", "-h"])
    uptime_out, _ = run(["uptime", "-p"])
    lines = disk_out.splitlines()
    disk = {"size": "", "used": "", "avail": "", "pct": ""}
    if len(lines) >= 2:
        parts = lines[1].split()
        if len(parts) >= 4:
            disk = {"size": parts[0], "used": parts[1], "avail": parts[2], "pct": parts[3]}
    mem = {"total": "", "used": "", "avail": ""}
    for line in mem_out.splitlines():
        if line.startswith("Mem:"):
            parts = line.split()
            if len(parts) >= 7:
                mem = {"total": parts[1], "used": parts[2], "avail": parts[6]}
    return {"disk": disk, "memory": mem, "uptime": uptime_out}


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path == "/api/status":
            self.send_json(self.get_status())
        elif self.path in ("/", ""):
            self.send_html(HTML)
        else:
            self.send_error(404)

    def get_status(self):
        containers = get_containers()
        stats = get_stats()
        nets = get_networks(containers)
        host = get_host_info()
        now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")

        for c in containers:
            s = stats.get(c["name"], {})
            n = nets.get(c["name"], {})
            c["cpu"] = s.get("cpu", "N/A")
            c["mem_usage"] = s.get("mem", "N/A")
            c["mem_pct"] = s.get("mem_pct", "N/A")
            c["net_io"] = s.get("net", "N/A")
            # Pick first non-empty IP from any network
            ipv4 = ""
            for net_name, ip in n.items():
                if ip and ":" not in ip:
                    ipv4 = ip
                    break
            c["ipv4"] = ipv4
            # Get IPv6 via nsenter inside container namespace
            pid_out, _ = run(["docker", "inspect", "--format", "{{.State.Pid}}", c["name"]])
            c["ipv6_v6"] = ""
            if pid_out and pid_out.strip().isdigit():
                v6_out, _ = run(["nsenter", "-t", pid_out.strip(), "-n", "--", "ip", "-6", "addr", "show", "dev", "eth0"])
                for v6line in v6_out.splitlines():
                    if "2001:470:1f18:2cb" in v6line:
                        c["ipv6_v6"] = v6line.strip().split()[1].split("/")[0]
                        break
            # Per-container quota (IPv4 + IPv6 合併)
            qb_v6 = get_container_quota(c["ipv6_v6"])
            qb_v4 = get_container_quota_v4(c["ipv4"])
            qb = (qb_v6 or 0) + (qb_v4 or 0)
            if qb_v6 is not None or qb_v4 is not None:
                remaining = max(0, QUOTA_BYTES - qb)
                pct = round(qb / QUOTA_BYTES * 100, 1) if QUOTA_BYTES > 0 else 0
                c["quota"] = {
                    "total_gb": 500,
                    "used_gb": round(qb / 1024**3, 2),
                    "remaining_gb": round(remaining / 1024**3, 2),
                    "used_pct": pct,
                    "used_bytes": qb,
                }
            else:
                c["quota"] = None

        return {
            "containers": containers,
            "host": host,
            "updated_at": now,
        }

    def send_json(self, data):
        self.send_response(200)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(json.dumps(data, ensure_ascii=False).encode())

    def send_html(self, html):
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.end_headers()
        self.wfile.write(html.encode())

    def log_message(self, fmt, *args):
        pass


HTML = r"""<!DOCTYPE html>
<html lang="zh-TW">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>容器監控</title>
<style>
*{margin:0;padding:0;box-sizing:border-box}
:root{--bg:#0c0e12;--card:#181b22;--border:#262a33;--text:#e1e4e8;--muted:#7a828e;--accent:#3b82f6;--green:#34d399;--red:#f87171;--yellow:#fbbf24}
body{background:var(--bg);color:var(--text);font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif;padding:24px;min-height:100vh}
.header{display:flex;justify-content:space-between;align-items:center;margin-bottom:24px}
.header h1{font-size:1.25rem;font-weight:600;letter-spacing:-.02em}
.header-right{display:flex;align-items:center;gap:12px;font-size:.8rem;color:var(--muted)}
#refresh-btn{background:var(--card);color:var(--text);border:1px solid var(--border);border-radius:8px;padding:6px 14px;font-size:.8rem;cursor:pointer;transition:.15s}
#refresh-btn:hover{background:var(--border)}
.summary{display:grid;grid-template-columns:repeat(auto-fit,minmax(160px,1fr));gap:12px;margin-bottom:20px}
.summary-item{background:var(--card);border:1px solid var(--border);border-radius:10px;padding:14px 16px;transition:all .15s}
.summary-item:hover{border-color:var(--accent)}
.summary-item .label{font-size:.75rem;color:var(--muted);margin-bottom:4px;text-transform:uppercase;letter-spacing:.05em}
.summary-item .value{font-size:1.35rem;font-weight:600;line-height:1.3}
.summary-item .sub{font-size:.75rem;color:var(--muted);margin-top:2px}
.quota-card{background:var(--card);border:1px solid var(--border);border-radius:10px;padding:16px 20px;margin-bottom:20px;transition:.15s}
.quota-card:hover{border-color:var(--accent)}
.quota-header{display:flex;justify-content:space-between;margin-bottom:6px;font-size:.85rem}
.quota-header span:first-child{color:var(--muted)}
.quota-header strong{color:var(--text)}
.bar{height:5px;background:#262a33;border-radius:4px;overflow:hidden;margin:6px 0}
.bar-fill{height:100%;border-radius:4px;transition:width .6s cubic-bezier(.4,0,.2,1)}
.bar-fill.green{background:var(--green)}
.bar-fill.yellow{background:var(--yellow)}
.bar-fill.red{background:var(--red)}
.quota-footer{display:flex;justify-content:space-between;font-size:.75rem;color:var(--muted)}
.grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(340px,1fr));gap:12px}
.card{background:var(--card);border:1px solid var(--border);border-radius:10px;padding:16px;transition:all .2s ease}
.card:hover{transform:translateY(-1px);box-shadow:0 4px 24px rgba(0,0,0,.3)}
.card-header{display:flex;justify-content:space-between;align-items:center;margin-bottom:10px}
.card-name{font-size:.95rem;font-weight:600;display:flex;align-items:center;gap:8px}
.card-image{font-size:.7rem;color:var(--muted);font-weight:400}
.badge{font-size:.7rem;padding:2px 10px;border-radius:999px;font-weight:500;letter-spacing:.02em}
.badge.green{background:rgba(52,211,153,.12);color:var(--green)}
.badge.red{background:rgba(248,113,113,.12);color:var(--red)}
.sep{height:1px;background:var(--border);margin:8px 0}
.row{display:flex;justify-content:space-between;align-items:center;padding:3px 0;font-size:.82rem}
.row .l{color:var(--muted)}
.row .v{font-family:'SF Mono','Fira Code',monospace;color:var(--text);font-size:.8rem;text-align:right}
.row .v.v6{font-size:.7rem}
.mem-bar{margin:4px 0;width:100%}
.ip-block{font-size:.72rem;word-break:break-all;max-width:200px;text-align:right;line-height:1.3}
@media(max-width:600px){
  body{padding:12px}
  .grid{grid-template-columns:1fr}
  .summary{grid-template-columns:repeat(2,1fr)}
}
</style>
</head>
<body>
<div class="header">
  <h1>容器監控</h1>
  <div class="header-right">
    <span id="update-time"></span>
    <button id="refresh-btn" onclick="fetchData()">⟳</button>
  </div>
</div>
<div id="summary" class="summary"></div>
<div id="containers" class="grid"></div>

<script>
let intv;
function b(b){if(b===0)return '0 B';const k=1024,s=['B','KB','MB','GB','TB'],i=Math.floor(Math.log(b)/Math.log(k));return parseFloat((b/Math.pow(k,i)).toFixed(1))+' '+s[i]}
function fN(s){if(!s||s==='N/A')return'—';const p=s.split('/');return p.length<2?s:'\u25bc'+p[0].trim()+' \u25b2'+p[1].trim()}
function qc(p){return p<70?'green':p<90?'yellow':'red'}
function rn(d){
  document.getElementById('update-time').textContent=new Date().toLocaleString('zh-TW',{timeZone:'Asia/Taipei'});
  const h=d.host;
  document.getElementById('summary').innerHTML=
    '<div class="summary-item"><div class="label">磁碟</div><div class="value">'+(h.disk.used||'?')+'</div><div class="sub">/ '+(h.disk.size||'?')+' ('+(h.disk.pct||'')+')</div></div>'+
    '<div class="summary-item"><div class="label">記憶體</div><div class="value">'+(h.memory.used||'?')+'</div><div class="sub">/ '+(h.memory.total||'?')+' ('+((h.memory.total&&h.memory.used)?Math.round(parseInt(h.memory.used)/parseInt(h.memory.total)*100):'?')+'%)</div></div>'+
    '<div class="summary-item"><div class="label">上線</div><div class="value">'+d.containers.reduce((a,c)=>a+(c.running?1:0),0)+' / '+d.containers.length+'</div><div class="sub">容器</div></div>'+
    '<div class="summary-item"><div class="label">運行</div><div class="value" style="font-size:1rem">'+(h.uptime||'?')+'</div></div>';
  let html='';
  for(const c of d.containers){
    const sc=c.running?'green':'red';
    html+='<div class="card"><div class="card-header"><div class="card-name">'+c.name+' <span class="card-image">'+c.image+'</span></div><span class="badge '+sc+'">'+(c.running?'\u25cf 在線':'離線')+'</span></div>';
    if(c.status)html+='<div class="row"><span class="l">狀態</span><span class="v">'+c.status+'</span></div>';
    html+='<div class="row"><span class="l">IPv4</span><span class="v">'+(c.ipv4||'—')+'</span></div>'+
      '<div class="row"><span class="l">IPv6</span><span class="v ip-block">'+(c.ipv6_v6||'—')+'</span></div>'+
      '<div class="sep"></div>'+
      '<div class="row"><span class="l">CPU</span><span class="v">'+c.cpu+'</span></div>'+
      '<div class="row"><span class="l">記憶體</span><span class="v">'+c.mem_usage+'</span></div>';
    if(c.mem_pct!=='N/A'){
      const mp=parseFloat(c.mem_pct)||0;
      html+='<div class="mem-bar"><div class="bar"><div class="bar-fill '+(mp>80?'red':mp>60?'yellow':'green')+'" style="width:'+Math.min(mp,100)+'%"></div></div></div>';
    }
    html+='<div class="row"><span class="l">網路IO</span><span class="v">'+fN(c.net_io)+'</span></div>';
    // Per-container quota
    if(c.quota){
      const q=c.quota, c2=qc(q.used_pct);
      html+='<div class="sep"></div>'+
        '<div class="row"><span class="l">流量配額</span><span class="v">'+q.used_gb+' / '+q.total_gb+' GB ('+q.used_pct+'%)</span></div>'+
        '<div class="bar"><div class="bar-fill '+c2+'" style="width:'+Math.min(q.used_pct,100)+'%"></div></div>'+
        '<div style="display:flex;justify-content:space-between;font-size:.7rem;color:var(--muted);margin-top:2px">'+
        '<span>已用 '+b(q.used_bytes)+'</span><span>剩餘 '+q.remaining_gb+' GB</span></div>';
    }
    html+='</div>';
  }
  document.getElementById('containers').innerHTML=html;
}
async function fetchData(){
  try{const r=await fetch('/api/status');rn(await r.json())}catch(e){document.getElementById('containers').innerHTML='<div style="color:var(--red);padding:16px">取得資料失敗: '+e.message+'</div>'}
}
fetchData();intv=setInterval(fetchData,5000);
</script>
</body>
</html>"""


if __name__ == "__main__":
    os.makedirs("/opt/container-monitor", exist_ok=True)
    server = HTTPServer(("0.0.0.0", PORT), Handler)
    print(f"Container Monitor started on http://0.0.0.0:{PORT}")
    server.serve_forever()
