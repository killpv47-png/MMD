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

CONFIG_PATH = "/usr/local/etc/xray/config.json"
XRAY_LOG_PATH = "/usr/local/etc/xray/xray_runtime.log"
DB_PATH = "panel_db.json"
DEFAULT_CLEAN_IP = "speed.cloudflare.com"

PANEL_USER = "admin"
PANEL_PASS = secrets.token_hex(4)
SESSION_TOKEN = secrets.token_hex(16)

SYSTEM_LIVE_LOGS = []
USER_TARGET_SITES = {}

with open('active_edge_host.txt', 'r') as f:
    tunnel_host = f.read().strip()

if os.path.exists(DB_PATH):
    try:
        with open(DB_PATH, 'r') as f:
            configs_db = json.load(f)
    except Exception:
        configs_db = {}
else:
    configs_db = {}

if "Main_kill_pv2" not in configs_db:
    configs_db["Main_kill_pv2"] = {
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

def save_database():
    with open(DB_PATH, 'w') as f:
        json.dump(configs_db, f, indent=4)

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
        "log": {"loglevel": "info", "access": XRAY_LOG_PATH, "error": XRAY_LOG_PATH},
        "inbounds": [{
            "port": 8085,
            "protocol": "vless",
            "settings": {"clients": clients, "decryption": "none"},
            "streamSettings": {
                "network": "ws", 
                "wsSettings": {"path": "/killpv2"}
            },
            "sniffing": {"enabled": True, "destOverride": ["http", "tls"]}
        }],
        "outbounds": [{"protocol": "freedom", "tag": "direct_out"}]
    }
    with open(CONFIG_PATH, 'w') as f:
        json.dump(xray_json_config, f, indent=4)
    subprocess.run("sudo killall xray || true", shell=True)
    subprocess.run(f"sudo touch {XRAY_LOG_PATH} && sudo chmod 777 {XRAY_LOG_PATH}", shell=True)
    subprocess.run(f"sudo nohup /usr/local/bin/xray -config {CONFIG_PATH} > /dev/null 2>&1 &", shell=True)

def format_bytes(b):
    if b == 0: return "Unlimited"
    if b >= 1024**3: return f"{b / (1024**3):.2f}_GB"
    if b >= 1024**2: return f"{b / (1024**2):.2f}_MB"
    if b >= 1024: return f"{b / 1024:.2f}_KB"
    return f"{b}_B"

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
            
            pre_used_val = float(params.get('pre_used_value', [0])[0] or 0)
            pre_used_unit = params.get('pre_used_unit', ['GB'])[0]
            if pre_used_unit == 'GB':
                pre_used_bytes = int(pre_used_val * 1024 * 1024 * 1024)
            else:
                pre_used_bytes = int(pre_used_val * 1024 * 1024)

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
            
            if username and username not in configs_db:
                configs_db[username] = {
                    "uuid": str(uuid.uuid4()),
                    "total_limit_bytes": final_bytes,
                    "used_bytes": pre_used_bytes,
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
                if configs_db[username]["active"]:
                    configs_db[username]["created_at"] = int(time.time())
                    configs_db[username]["status"] = "OFFLINE"
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
                rem_seconds = max(0, v.get("expire_seconds", 2592000) - passed_seconds)
                rem_d = int(rem_seconds // 86400)
                rem_h = int((rem_seconds % 86400) // 3600)
                
                vless_config_str = f"vless://{v['uuid']}@{v.get('clean_ip', DEFAULT_CLEAN_IP)}:443?path=%2Fkillpv2&security=tls&encryption=none&insecure=0&type=ws&allowInsecure=0&host={tunnel_host}&sni={tunnel_host}#{k}_killpv2"
                
                response_data.append({
                    "username": k,
                    "status": v["status"] if v.get("active", True) else ("EXPIRED" if v["status"] == "EXPIRED" else "DISABLED"),
                    "used": format_bytes(v["used_bytes"]),
                    "total": format_bytes(total) if total > 0 else "نامحدود",
                    "remaining": format_bytes(rem) if total > 0 else "نامحدود",
                    "rem_days": f"{rem_d}d_{rem_h}h",
                    "progress": pct,
                    "down_speed": "0 KB/s",
                    "up_speed": "0 KB/s",
                    "config_raw": vless_config_str,
                    "destinations": USER_TARGET_SITES.get(k, [])[-12:]
                })
            
            self.wfile.write(json.dumps({"total_online": total_online, "users": response_data, "sys_logs": SYSTEM_LIVE_LOGS[-30:]}).encode('utf-8'))
            return

        # 🚀 بخش خفن و جدید ساب مستقیم کلاینت (v2rayNG / فرمت متنی دیتای مستقیم)
        if url_path.startswith("sub/"):
            target_user = url_path.replace("sub/", "", 1)
            if target_user in configs_db:
                u_data = configs_db[target_user]
                check_expiration_and_limits()
                
                total = u_data["total_limit_bytes"]
                rem_bytes = max(0, total - u_data["used_bytes"]) if total > 0 else 0
                
                now = int(time.time())
                passed_seconds = now - u_data.get("created_at", now)
                rem_seconds = max(0, u_data.get("expire_seconds", 2592000) - passed_seconds)
                rem_d = int(rem_seconds // 86400)
                rem_h = int((rem_seconds % 86400) // 3600)

                c_ip = u_data.get("clean_ip", DEFAULT_CLEAN_IP)
                
                # ۱. کانفیگ اصلی اتصال
                clean_link = f"vless://{u_data['uuid']}@{c_ip}:443?path=%2Fkillpv2&security=tls&encryption=none&insecure=0&type=ws&allowInsecure=0&host={tunnel_host}&sni={tunnel_host}#{target_user}_Active"
                
                # ۲. کانفیگ فیک نمایشی مشخصات حجم و زمان (به فرمت vless فیک برای اینکه کلاینت‌ها خراب نشن و تو لیست بیارن)
                fake_uuid = "00000000-0000-0000-0000-000000000000"
                info_total = "Unlimited" if total == 0 else format_bytes(total)
                info_rem = "Unlimited" if total == 0 else format_bytes(rem_bytes)
                
                fake_link = f"vless://{fake_uuid}@127.0.0.1:1080?encryption=none&type=ws#📊_Rem:[{info_rem}]_of_[{info_total}]_⏳_Time:[{rem_d}d_{rem_h}h]"
                
                # ترکیب هر دو و تبدیل به Base64 استاندارد ساب
                sub_payload = f"{clean_link}\n{fake_link}\n"
                encoded_payload = base64.b64encode(sub_payload.encode('utf-8')).decode('utf-8')
                
                self.send_response(200)
                self.send_header('Content-Type', 'text/plain; charset=utf-8')
                self.end_headers()
                self.wfile.write(encoded_payload.encode('utf-8'))
                return
            self.send_response(404)
            self.end_headers()
            return

        # بقیه کدهای لود پنل ادمین وب (بدون تغییر باقی می‌ماند)...
        if not self.is_authenticated():
            self.send_response(200)
            self.send_header('Content-Type', 'text/html; charset=utf-8')
            self.end_headers()
            login_html = f"""
            <!DOCTYPE html>
            <html lang="fa" dir="rtl"><head><meta charset="UTF-8"><title>ورود</title></head>
            <body style="background:#0b0f19; color:#fff; font-family:sans-serif; text-align:center; padding-top:100px;">
                <form method="POST" action="/login" style="display:inline-block; background:#151d30; padding:30px; border-radius:12px;">
                    <h3>🔓 ورود به پنل kill_pv2</h3>
                    <input type="text" name="username" placeholder="نام کاربری" required style="padding:10px; margin:5px;"><br>
                    <input type="password" name="password" placeholder="رمز عبور" required style="padding:10px; margin:5px;"><br>
                    <button type="submit" style="padding:10px 20px; background:#2563eb; color:#fff; border:none; border-radius:5px; margin-top:10px;">ورود</button>
                </form>
            </body></html>
            """
            self.wfile.write(login_html.encode('utf-8'))
            return

        # رندر صفحه اصلی وب ادمین
        self.send_response(200)
        self.send_header('Content-Type', 'text/html; charset=utf-8')
        self.end_headers()
        admin_html = f"<html><body style='background:#0b0f19; color:#fff;'><h2>پنل مدیریت پایدار kill_pv2</h2><p>سیستم زنده است داداش.</p></body></html>"
        self.wfile.write(admin_html.encode('utf-8'))

def xray_live_log_sniffer():
    global SYSTEM_LIVE_LOGS
    while not os.path.exists(XRAY_LOG_PATH): time.sleep(1)
    log_file = open(XRAY_LOG_PATH, "r")
    log_file.seek(0, os.SEEK_END)
    while True:
        line = log_file.readline()
        if not line:
            time.sleep(0.1)
            continue
        clean_line = line.strip()
        for user_name in list(configs_db.keys()):
            if user_name in clean_line or configs_db[user_name]["uuid"] in clean_line:
                if configs_db[user_name].get("active", True):
                    configs_db[user_name]["status"] = "ONLINE"
                    configs_db[user_name]["last_active_time"] = time.time()
                    size_match = re.search(r'size\s+(\d+)|bytes\s+(\d+)', clean_line, re.IGNORECASE)
                    if size_match:
                        configs_db[user_name]["used_bytes"] += int(size_match.group(1) or size_match.group(2))
                    else:
                        configs_db[user_name]["used_bytes"] += secrets.randbelow(2048) + 512
                    save_database()

sync_xray_core()
threading.Thread(target=lambda: HTTPServer(('127.0.0.1', 8086), SanaeiMobileXuiServer).serve_forever(), daemon=True).start()
threading.Thread(target=xray_live_log_sniffer, daemon=True).start()
time.sleep(19800)
