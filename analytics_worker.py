import subprocess
import os
import time
import json
from http.server import BaseHTTPRequestHandler, HTTPServer
import threading
import base64
import uuid
import secrets
import re
import sys
from urllib.parse import parse_qs
import requests

CONFIG_PATH = "/usr/local/etc/xray/config.json"
XRAY_LOG_PATH = "/usr/local/etc/xray/xray_runtime.log"
DB_PATH = "panel_db.json"
DEFAULT_CLEAN_IP = "speed.cloudflare.com"

# 🟢 اصلاح شد: بازگشت به رمز عبور ثابت داداش و حذف توکن تصادفی
PANEL_USER = "admin"
PANEL_PASS = "kill_pv2_panel"  
SESSION_TOKEN = secrets.token_hex(16)

SYSTEM_LIVE_LOGS = []
USER_TARGET_SITES = {}

# دریافت اطلاعات مخزن و توکن برای پایداری ابدی اطلاعات
GITHUB_TOKEN = os.getenv("GH_PAT") or os.getenv("GITHUB_TOKEN")
REPO_NAME = os.getenv("GITHUB_REPOSITORY")

if os.path.exists('active_edge_host.txt'):
    with open('active_edge_host.txt', 'r') as f:
        tunnel_host = f.read().strip()
else:
    tunnel_host = "127.0.0.1"

def load_database():
    """بارگذاری دیتابیس با اولویت همگام‌سازی از گیت‌هاب یا فایل محلی"""
    if GITHUB_TOKEN and REPO_NAME:
        try:
            url = f"https://api.github.com/repos/{REPO_NAME}/contents/{DB_PATH}"
            headers = {"Authorization": f"token {GITHUB_TOKEN}", "Accept": "application/vnd.github.v3+json"}
            r = requests.get(url, headers=headers, timeout=10)
            if r.status_code == 200:
                content = r.json()
                db_bytes = base64.b64decode(content['content'])
                print("✅ دیتابیس کلاینت‌ها با موفقیت از گیت‌هاب لود شد.", flush=True)
                return json.loads(db_bytes.decode('utf-8'))
        except Exception as e:
            print(f"⚠️ لود از گیت‌هاب با خطا مواجه شد، تلاش برای لود محلی... {e}", flush=True)

    if os.path.exists(DB_PATH):
        try:
            with open(DB_PATH, 'r') as f:
                data = json.load(f)
                if data and len(data) > 0:
                    return data
        except Exception:
            pass

    return {
        "Main_kill_pv2": {
            "uuid": "b6a00fb0-460e-4323-96af-3ba2f48470ee",
            "total_limit_bytes": 0,
            "used_bytes": 0,
            "clean_ip": "speed.cloudflare.com",
            "status": "OFFLINE",
            "last_active_time": 0,
            "down_speed": 0,
            "up_speed": 0,
            "created_at": int(time.time()),
            "expire_seconds": 31536000, 
            "active": True
        }
    }

configs_db = load_database()

def save_database():
    with open(DB_PATH, 'w') as f:
        json.dump(configs_db, f, indent=4)
        
    if GITHUB_TOKEN and REPO_NAME:
        def push_to_github():
            try:
                url = f"https://api.github.com/repos/{REPO_NAME}/contents/{DB_PATH}"
                headers = {"Authorization": f"token {GITHUB_TOKEN}", "Accept": "application/vnd.github.v3+json"}
                sha = ""
                r_get = requests.get(url, headers=headers, timeout=5)
                if r_get.status_code == 200:
                    sha = r_get.json()['sha']
                    
                with open(DB_PATH, 'rb') as f_bytes:
                    encoded_content = base64.b64encode(f_bytes.read()).decode('utf-8')
                    
                payload = {
                    "message": "🔄 Auto-Sync Panel Database [kill_pv2]",
                    "content": encoded_content
                }
                if sha:
                    payload["sha"] = sha
                    
                requests.put(url, headers=headers, json=payload, timeout=10)
            except Exception:
                pass
        threading.Thread(target=push_to_github, daemon=True).start()

def check_expiration_and_limits():
    now = int(time.time())
    changed = False
    for u_name, u_data in configs_db.items():
        if not u_data.get("active", True):
            continue
            
        total_limit = u_data.get("total_limit_bytes", 0)
        if total_limit > 0 and u_data["used_bytes"] >= total_limit:
            configs_db[u_name]["active"] = False
            configs_db[u_name]["status"] = "EXPIRED"
            changed = True
            
        created_time = u_data.get("created_at", now)
        expire_seconds = u_data.get("expire_seconds", 2592000)
        if now - created_time > expire_seconds:
            configs_db[u_name]["active"] = False
            configs_db[u_name]["status"] = "EXPIRED"
            changed = True
            
    if changed:
        save_database()
        sync_xray_core()

def sync_xray_core():
    clients = [{"id": u_data["uuid"], "email": u_name, "level": 0} for u_name, u_data in configs_db.items() if u_data.get("active", True)]
    
    xray_json_config = {
        "log": {
            "loglevel": "info",
            "access": XRAY_LOG_PATH,
            "error": XRAY_LOG_PATH
        },
        "inbounds": [
            {
                "port": 8085,
                "protocol": "vless",
                "settings": {"clients": clients, "decryption": "none"},
                "streamSettings": {
                    "network": "ws", 
                    "wsSettings": {"path": "/killpv2"}
                },
                "sniffing": {
                    "enabled": True, 
                    "destOverride": ["http", "tls"]
                }
            }
        ],
        "outbounds": [{"protocol": "freedom", "tag": "direct_out"}]
    }
    
    with open(CONFIG_PATH, 'w') as f:
        json.dump(xray_json_config, f, indent=4)
        
    subprocess.run("sudo killall xray || true", shell=True)
    subprocess.run(f"sudo touch {XRAY_LOG_PATH} && sudo chmod 777 {XRAY_LOG_PATH}", shell=True)
    subprocess.run(f"sudo nohup /usr/local/bin/xray -config {CONFIG_PATH} > /dev/null 2>&1 &", shell=True)

def format_bytes(b):
    if b == 0: return "نامحدود"
    if b >= 1024**3: return f"{b / (1024**3):.2f} GB"
    if b >= 1024**2: return f"{b / (1024**2):.2f} MB"
    if b >= 1024: return f"{b / 1024:.2f} KB"
    return f"{b} B"

def format_speed(bytes_per_sec):
    kb = bytes_per_sec / 1024
    if kb >= 1024: return f"{kb/1024:.1f} MB/s"
    return f"{kb:.1f} KB/s"

class SanaeiMobileXuiServer(BaseHTTPRequestHandler):
    def log_message(self, format, *args): return
    
    def is_authenticated(self):
        cookies = self.headers.get('Cookie', '')
        return f"session={SESSION_TOKEN}" in cookies

    def do_POST(self):
        content_length = int(self.headers['Content-Length'])
        post_data = self.rfile.read(content_length).decode('utf-8')
        params = parse_qs(post_data)
        
        if self.path == "/login":
            username = params.get('username', [''])[0].strip()
            password = params.get('password', [''])[0].strip()
            if username == PANEL_USER and password == PANEL_PASS:
                self.send_response(303)
                self.send_header('Set-Cookie', f'session={SESSION_TOKEN}; Path=/; HttpOnly')
                self.send_header('Location', '/')
                self.end_headers()
            else:
                self.send_response(303)
                self.send_header('Location', '/?error=true')
                self.end_headers()
            return

        if not self.is_authenticated():
            self.send_response(303)
            self.send_header('Location', '/')
            self.end_headers()
            return

        action = params.get('action', [''])[0]
        if action == 'create':
            username = params.get('username', [''])[0].strip()
            is_unlimited = params.get('unlimited_volume', [''])[0] == 'true'
            volume_val = float(params.get('volume_value', [0])[0] or 0)
            volume_unit = params.get('volume_unit', ['GB'])[0]
            
            initial_used_val = float(params.get('initial_used_value', [0])[0] or 0)
            initial_used_unit = params.get('initial_used_unit', ['GB'])[0]
            
            expire_days = int(params.get('expire_days', [0])[0] or 0)
            expire_hours = int(params.get('expire_hours', [0])[0] or 0)
            total_seconds = (expire_days * 86400) + (expire_hours * 3600)
            if total_seconds <= 0: total_seconds = 2592000 
            
            clean_ip = params.get('clean_ip', ['speed.cloudflare.com'])[0].strip()
            if not clean_ip: clean_ip = "speed.cloudflare.com"
            
            if is_unlimited:
                final_bytes = 0
            else:
                if volume_unit == 'GB':
                    final_bytes = int(volume_val * 1024 * 1024 * 1024)
                else:
                    final_bytes = int(volume_val * 1024 * 1024)

            if initial_used_unit == 'GB':
                final_initial_used_bytes = int(initial_used_val * 1024 * 1024 * 1024)
            else:
                final_initial_used_bytes = int(initial_used_val * 1024 * 1024)
            
            if username and username not in configs_db:
                configs_db[username] = {
                    "uuid": str(uuid.uuid4()),
                    "total_limit_bytes": final_bytes,
                    "used_bytes": final_initial_used_bytes, 
                    "clean_ip": clean_ip,
                    "status": "OFFLINE",
                    "last_active_time": 0,
                    "down_speed": 0,
                    "up_speed": 0,
                    "created_at": int(time.time()),
                    "expire_seconds": total_seconds,
                    "active": True
                }
                USER_TARGET_SITES[username] = []
                save_database()
                sync_xray_core()
                
        elif action == 'toggle':
            username = params.get('username', [''])[0]
            if username in configs_db:
                configs_db[username]["active"] = not configs_db[username].get("active", True)
                save_database()
                sync_xray_core()
                
        elif action == 'delete':
            username = params.get('username', [''])[0]
            if username in configs_db:
                del configs_db[username]
                if username in USER_TARGET_SITES: del USER_TARGET_SITES[username]
                save_database()
                sync_xray_core()
        
        self.send_response(303)
        self.send_header('Location', '/')
        self.end_headers()

    def do_GET(self):
        url_path = self.path.strip("/")
        
        if url_path == "api/stats":
            if not self.is_authenticated():
                self.send_response(401)
                self.end_headers()
                return
            self.send_response(200)
            self.send_header('Content-Type', 'application/json')
            self.end_headers()
            
            check_expiration_and_limits()
            
            response_data = []
            total_online = sum(1 for u in configs_db.values() if u.get("status") == "ONLINE" and u.get("active", True))
            
            now = int(time.time())
            for k, v in configs_db.items():
                total = v["total_limit_bytes"]
                rem = max(0, total - v["used_bytes"]) if total > 0 else 0
                pct = min(100, (v["used_bytes"] / total * 100)) if total > 0 else 0
                
                passed_seconds = now - v.get("created_at", now)
                total_seconds = v.get("expire_seconds", 2592000)
                rem_seconds = max(0, total_seconds - passed_seconds)
                
                rem_d = int(rem_seconds // 86400)
                rem_h = int((rem_seconds % 86400) // 3600)
                
                vless_config_str = f"vless://{v['uuid']}@{v.get('clean_ip', DEFAULT_CLEAN_IP)}:443?path=%2Fkillpv2&security=tls&encryption=none&insecure=0&type=ws&allowInsecure=0&host={tunnel_host}&sni={tunnel_host}#{k}_killpv2"
                
                status_label = v["status"]
                if not v.get("active", True):
                    status_label = "EXPIRED" if v["status"] == "EXPIRED" else "DISABLED"
                
                response_data.append({
                    "username": k,
                    "status": status_label,
                    "used": format_bytes(v["used_bytes"]),
                    "total": format_bytes(total) if total > 0 else "نامحدود",
                    "remaining": format_bytes(rem) if total > 0 else "نامحدود",
                    "rem_days": f"{rem_d} روز و {rem_h} ساعت",
                    "progress": pct,
                    "down_speed": format_speed(v.get("down_speed", 0)),
                    "up_speed": format_speed(v.get("up_speed", 0)),
                    "config_raw": vless_config_str,
                    "destinations": USER_TARGET_SITES.get(k, [])[-12:]
                })
            
            self.wfile.write(json.dumps({"total_online": total_online, "users": response_data, "sys_logs": SYSTEM_LIVE_LOGS[-30:]}).encode('utf-8'))
            return

        if url_path.startswith("sub/"):
            target_user = url_path.replace("sub/", "", 1)
            if target_user in configs_db and configs_db[target_user].get("active", True):
                u_data = configs_db[target_user]
                c_ip = u_data.get("clean_ip", DEFAULT_CLEAN_IP)
                
                clean_link = f"vless://{u_data['uuid']}@{c_ip}:443?path=%2Fkillpv2&security=tls&encryption=none&insecure=0&type=ws&allowInsecure=0&host={tunnel_host}&sni={tunnel_host}#{target_user}_Clean"
                regular_link = f"vless://{u_data['uuid']}@{tunnel_host}:443?path=%2Fkillpv2&security=tls&encryption=none&insecure=0&type=ws&allowInsecure=0#{target_user}_Direct"
                payload = f"{clean_link}\n{regular_link}\n"
                
                encoded_payload = base64.b64encode(payload.encode('utf-8')).decode('utf-8')
                self.send_response(200)
                self.send_header('Content-Type', 'text/plain; charset=utf-8')
                self.end_headers()
                self.wfile.write(encoded_payload.encode('utf-8'))
                return
            self.send_response(404)
            self.end_headers()
            return

        if not self.is_authenticated():
            self.send_response(200)
            self.send_header('Content-Type', 'text/html; charset=utf-8')
            self.end_headers()
            err_msg = '<div style="color:#f87171; text-align:center; margin-bottom:10px; font-size:0.85rem;">❌ رمز عبور اشتباه است داداش</div>' if "error=true" in self.path else ''
            
            login_html = f"""
            <!DOCTYPE html>
            <html lang="fa" dir="rtl">
            <head>
                <meta charset="UTF-8">
                <title>ورود به سیستم امنیت پنل</title>
                <style>
                    body {{ font-family: sans-serif; background-color: #0b0f19; color: #f1f5f9; display: flex; align-items: center; justify-content: center; height: 100vh; margin: 0; }}
                    .login-card {{ background: #151d30; padding: 25px; border-radius: 16px; border: 1px solid #222f4c; width: 300px; }}
                    h3 {{ text-align: center; color: #38bdf8; }}
                    .form-control {{ width: 100%; padding: 10px; background: #0b0f19; border: 1px solid #2d3d5f; border-radius: 10px; color: #fff; margin-bottom: 15px; box-sizing: border-box; }}
                    .btn {{ width: 100%; padding: 10px; background: #2563eb; color: white; border: none; border-radius: 10px; font-weight: bold; cursor: pointer; }}
                </style>
            </head>
            <body>
                <div class="login-card">
                    <h3>🔓 ورود به پنل kill_pv2</h3>
                    {err_msg}
                    <form method="POST" action="/login">
                        <input type="text" name="username" class="form-control" placeholder="نام کاربری" required>
                        <input type="password" name="password" class="form-control" placeholder="رمز عبور اختصاصی" required>
                        <button type="submit" class="btn">ورود ایمن</button>
                    </form>
                </div>
            </body>
            </html>
            """
            self.wfile.write(login_html.encode('utf-8'))
            return

        if url_path == "" or url_path == "index.html":
            self.send_response(200)
            self.send_header('Content-Type', 'text/html; charset=utf-8')
            self.end_headers()
            
            html_content = f"""
            <!DOCTYPE html>
            <html lang="fa" dir="rtl">
            <head>
                <meta charset="UTF-8">
                <meta name="viewport" content="width=device-width, initial-scale=1.0">
                <title>پنل مدیریت | kill_pv2</title>
                <style>
                    body {{ font-family: system-ui, sans-serif; background-color: #0b0f19; color: #f1f5f9; margin: 0; padding: 12px; }}
                    .panel-container {{ max-width: 700px; margin: 0 auto; }}
                    .header-board {{ background: linear-gradient(135deg, #1e40af, #1d4ed8); padding: 20px; border-radius: 16px; margin-bottom: 15px; text-align: center; }}
                    .card {{ background: #151d30; border-radius: 16px; padding: 16px; margin-bottom: 15px; border: 1px solid #222f4c; }}
                    .form-control {{ width: 100%; padding: 10px; background: #0b0f19; border: 1px solid #2d3d5f; border-radius: 10px; color: #fff; margin-bottom: 10px; box-sizing: border-box; }}
                    .btn {{ width: 100%; padding: 11px; border: none; border-radius: 10px; font-weight: bold; cursor: pointer; background: #2563eb; color: #fff; }}
                    .user-row {{ background: #1a243d; border-radius: 12px; padding: 12px; margin-bottom: 10px; border: 1px solid #273659; }}
                    .user-flex {{ display: flex; justify-content: space-between; align-items: center; margin-bottom: 8px; }}
                    .data-grid {{ display: grid; grid-template-columns: 1fr 1fr; gap: 6px; font-size: 0.8rem; color: #94a3b8; border-top: 1px solid #273659; padding-top: 8px; }}
                    .p-bar-bg {{ width: 100%; background: #2d3d5f; height: 6px; border-radius: 10px; margin-top: 6px; overflow: hidden; }}
                    .p-bar-fill {{ background: #2563eb; height: 100%; width: 0%; }}
                    .action-bar {{ display: flex; gap: 5px; margin-top: 10px; }}
                    .action-bar button, .action-bar a {{ flex: 1; padding: 8px 4px; border-radius: 6px; font-size: 0.75rem; font-weight: bold; border: none; cursor: pointer; color: white; }}
                    .btn-sub {{ background: #3b82f6; }} .btn-conf {{ background: #8b5cf6; }} .btn-tog {{ background: #f59e0b; color: black; }} .btn-del {{ background: #ef4444; }}
                    .terminal-box {{ background: #020617; border: 1px solid #1e293b; border-radius: 12px; height: 150px; overflow-y: auto; font-family: monospace; font-size: 0.78rem; padding: 10px; color: #cbd5e1; direction: ltr; text-align: left; }}
                </style>
                <script>
                    let cachedConfigs = {{}};
                    async function loadLiveStats() {{
                        try {{
                            let res = await fetch('/api/stats');
                            let data = await res.json();
                            document.getElementById('online_count').innerText = data.total_online;
                            
                            const term = document.getElementById('sys_terminal');
                            term.innerHTML = "";
                            data.sys_logs.forEach(l => {{ term.innerHTML += "<div>" + l + "</div>"; }});
                            
                            data.users.forEach(u => {{
                                let row = document.getElementById('u_' + u.username);
                                if(row) {{
                                    row.querySelector('.u-used').innerText = u.used;
                                    row.querySelector('.u-rem').innerText = u.remaining;
                                    row.querySelector('.u-days').innerText = u.rem_days;
                                    row.querySelector('.p-bar-fill').style.width = u.progress + '%';
                                    cachedConfigs[u.username] = u.config_raw;
                                }}
                            }});
                        }} catch(e) {{}}
                    }}
                    function copyConfig(user) {{
                        if(cachedConfigs[user]) {{
                            navigator.clipboard.writeText(cachedConfigs[user]);
                            alert('📋 کانفیگ با موفقیت کپی شد داداش!');
                        }}
                    }}
                    function copyFixedSubscription(user) {{
                        let fixedSubUrl = window.location.protocol + "//" + window.location.host + "/sub/" + user;
                        navigator.clipboard.writeText(fixedSubUrl);
                        alert("🔗 لینک ساب پایدار کپی شد داداش!");
                    }}
                    setInterval(loadLiveStats, 3000);
                </script>
            </head>
            <body>
                <div class="panel-container">
                    <div class="header-board">
                        <h2>🎛️ سیستم مدیریت هوشمند kill_pv2</h2>
                        <div>کاربران آنلاین: <span id="online_count">0</span></div>
                    </div>

                    <div class="card">
                        <h4>➕ افزودن کلاینت VLESS جدید</h4>
                        <form method="POST" action="/">
                            <input type="hidden" name="action" value="create">
                            <input type="text" name="username" class="form-control" placeholder="نام کاربری (انگلیسی)" required>
                            <input type="number" step="0.1" name="volume_value" class="form-control" placeholder="حجم مجاز (گیگابایت)" value="50">
                            <input type="number" name="expire_days" class="form-control" placeholder="اعتبار (روز)" value="30">
                            <input type="text" name="clean_ip" class="form-control" placeholder="آی‌پی تمیز کلودفلر">
                            <button type="submit" class="btn">⚡ ایجاد کانفیگ</button>
                        </form>
                    </div>

                    <div class="card">
                        <h4>👤 لیست کلاینت‌ها</h4>
                        <div id="users_container">
            """
            for user_name, user_data in configs_db.items():
                html_content += f"""
                            <div class="user-row" id="u_{user_name}">
                                <div class="user-flex">
                                    <strong>{user_name}</strong>
                                    <span>{user_data['status']}</span>
                                </div>
                                <div class="data-grid">
                                    <div>مصرف: <span class="u-used">0 B</span></div>
                                    <div>باقی‌مانده: <span class="u-rem">0 B</span></div>
                                    <div>زمان: <span class="u-days">0 روز</span></div>
                                </div>
                                <div class="p-bar-bg"><div class="p-bar-fill"></div></div>
                                <div class="action-bar">
                                    <button class="btn-sub" onclick="copyFixedSubscription('{user_name}')">🔗 ساب ثابت</button>
                                    <button class="btn-conf" onclick="copyConfig('{user_name}')">📋 کانفیگ</button>
                                    <form method="POST" action="/" style="flex:1; display:flex;"><input type="hidden" name="action" value="toggle"><input type="hidden" name="username" value="{user_name}"><button type="submit" class="btn-tog">⚙️ سوییچ</button></form>
                                    <form method="POST" action="/" style="flex:1; display:flex;" onsubmit="return confirm('حذف بشه داداش؟');"><input type="hidden" name="action" value="delete"><input type="hidden" name="username" value="{user_name}"><button type="submit" class="btn-del">🗑️ حذف</button></form>
                                </div>
                            </div>
                """
            html_content += f"""
                        </div>
                    </div>

                    <div class="card">
                        <h4>📟 لاگ زنده سیستم</h4>
                        <div class="terminal-box" id="sys_terminal">در حال بارگذاری...</div>
                    </div>
                </div>
            </body>
            </html>
            """
            self.wfile.write(html_content.encode('utf-8'))
            return
        
        self.send_response(404)
        self.end_headers()

def xray_live_log_sniffer():
    global SYSTEM_LIVE_LOGS
    while not os.path.exists(XRAY_LOG_PATH): time.sleep(1)
    log_file = open(XRAY_LOG_PATH, "r")
    log_file.seek(0, os.SEEK_END)

    while True:
        line = log_file.readline()
        if not line:
            time.sleep(0.2)
            continue
        clean_line = line.strip()
        if clean_line:
            SYSTEM_LIVE_LOGS.append(clean_line)
            if len(SYSTEM_LIVE_LOGS) > 40: SYSTEM_LIVE_LOGS.pop(0)

        for user_name in list(configs_db.keys()):
            if user_name in clean_line or configs_db[user_name]["uuid"] in clean_line:
                configs_db[user_name]["status"] = "ONLINE"
                configs_db[user_name]["last_active_time"] = time.time()
                
                size_match = re.search(r'size\s+(\d+)|uploaded\s+(\d+)', clean_line, re.IGNORECASE)
                if size_match:
                    configs_db[user_name]["used_bytes"] += int(size_match.group(1) or size_match.group(2))
                save_database()

sync_xray_core()
threading.Thread(target=lambda: HTTPServer(('127.0.0.1', 8086), SanaeiMobileXuiServer).serve_forever(), daemon=True).start()
threading.Thread(target=xray_live_log_sniffer, daemon=True).start()

total_duration = 19500
elapsed = 0
while elapsed < total_duration:
    time.sleep(10)
    elapsed += 10
    check_expiration_and_limits()
