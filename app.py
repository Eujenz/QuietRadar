import os
import sys
import json
import yaml
import subprocess
import threading
import webbrowser
from datetime import datetime
from http.server import HTTPServer, BaseHTTPRequestHandler
from urllib.parse import urlparse, parse_qs

# 確保 UTF-8
if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except Exception:
        pass

PORT = 8765
RUNNING_LOGS = []
IS_RUNNING = False

HTML_TEMPLATE = r"""<!DOCTYPE html>
<html lang="zh-TW" class="dark">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>QuietRadar 控制台</title>
  <script src="https://cdn.tailwindcss.com"></script>
  <script src="https://cdn.jsdelivr.net/npm/marked/marked.min.js"></script>
  <link href="https://cdnjs.cloudflare.com/ajax/libs/font-awesome/6.4.0/css/all.min.css" rel="stylesheet">
  <script>
    tailwind.config = {
      darkMode: 'class',
      theme: {
        extend: {
          colors: {
            brand: { 50: '#f0fdf4', 500: '#22c55e', 600: '#16a34a', 700: '#15803d' },
            dark: { 800: '#1e293b', 850: '#172033', 900: '#0f172a', 950: '#020617' }
          }
        }
      }
    }
  </script>
  <style>
    body { font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, "Noto Sans TC", sans-serif; }
    .prose a { color: #38bdf8; text-decoration: underline; }
    .prose h1, .prose h2, .prose h3 { color: #f8fafc; font-weight: 700; }
    .prose blockquote { border-left: 4px solid #38bdf8; padding-left: 1rem; color: #94a3b8; }
  </style>
</head>
<body class="bg-dark-950 text-slate-100 min-h-screen flex flex-col">

  <!-- 頂部導航列 -->
  <header class="bg-dark-900 border-b border-slate-800 sticky top-0 z-50">
    <div class="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8 h-16 flex items-center justify-between">
      <div class="flex items-center space-x-3">
        <div class="w-10 h-10 rounded-xl bg-gradient-to-tr from-green-500 to-emerald-400 flex items-center justify-center shadow-lg shadow-green-500/20">
          <i class="fa-solid fa-satellite-dish text-dark-950 text-lg"></i>
        </div>
        <div>
          <h1 class="font-bold text-lg text-white leading-tight">QuietRadar <span class="text-xs bg-emerald-500/20 text-emerald-400 border border-emerald-500/30 px-2 py-0.5 rounded-full font-mono">v1.0 MVP</span></h1>
          <p class="text-xs text-slate-400">對抗演算法綁架 · 奪回資訊主控權</p>
        </div>
      </div>
      <div class="flex items-center space-x-3">
        <button id="btn-run" onclick="triggerPipeline()" class="inline-flex items-center space-x-2 bg-emerald-500 hover:bg-emerald-600 text-dark-950 font-bold px-4 py-2 rounded-lg transition shadow-lg shadow-emerald-500/20 text-sm">
          <i class="fa-solid fa-bolt"></i>
          <span>立即執行雷達</span>
        </button>
        <button onclick="triggerTestBark()" class="inline-flex items-center space-x-2 bg-slate-800 hover:bg-slate-700 text-slate-200 font-medium px-3 py-2 rounded-lg transition border border-slate-700 text-sm">
          <i class="fa-solid fa-mobile-screen-button"></i>
          <span>測試 Bark 推播</span>
        </button>
      </div>
    </div>
  </header>

  <!-- 主工作區 -->
  <main class="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8 py-6 flex-1 w-full grid grid-cols-1 lg:grid-cols-12 gap-6">

    <!-- 左側：設定區塊 (7 cols) -->
    <div class="lg:col-span-7 space-y-6">
      
      <!-- 標籤切換 -->
      <div class="flex border-b border-slate-800 space-x-4">
        <button onclick="switchTab('sources')" id="tab-sources-btn" class="pb-3 text-sm font-semibold border-b-2 border-emerald-500 text-emerald-400 flex items-center space-x-2">
          <i class="fa-solid fa-rss"></i>
          <span>訂閱來源管理</span>
        </button>
        <button onclick="switchTab('profile')" id="tab-profile-btn" class="pb-3 text-sm font-semibold border-b-2 border-transparent text-slate-400 hover:text-slate-200 flex items-center space-x-2">
          <i class="fa-solid fa-filter"></i>
          <span>關注主題與降噪</span>
        </button>
        <button onclick="switchTab('settings')" id="tab-settings-btn" class="pb-3 text-sm font-semibold border-b-2 border-transparent text-slate-400 hover:text-slate-200 flex items-center space-x-2">
          <i class="fa-solid fa-key"></i>
          <span>金鑰與模型設定</span>
        </button>
      </div>

      <!-- 分頁 1: 訂閱來源 -->
      <div id="tab-sources" class="space-y-4">
        <div class="flex items-center justify-between">
          <p class="text-xs text-slate-400">管理目前訂閱的 RSS / Atom 頻道</p>
          <button onclick="openAddSourceModal()" class="text-xs bg-slate-800 hover:bg-slate-700 text-emerald-400 border border-slate-700 px-3 py-1.5 rounded-md flex items-center space-x-1.5">
            <i class="fa-solid fa-plus"></i>
            <span>新增來源</span>
          </button>
        </div>
        <div id="sources-list" class="space-y-2"></div>
      </div>

      <!-- 分頁 2: 關注領域與負向過濾 -->
      <div id="tab-profile" class="hidden space-y-4">
        <div class="bg-dark-900 border border-slate-800 rounded-xl p-5 space-y-4">
          <div>
            <label class="block text-sm font-semibold text-emerald-400 mb-1 flex items-center space-x-2">
              <i class="fa-solid fa-bullseye"></i>
              <span>核心關注主題 (Interests - 每行一項)</span>
            </label>
            <p class="text-xs text-slate-400 mb-2">LLM 會依此標準挑選最相關的 5~10 則精選文章</p>
            <textarea id="profile-interests" rows="5" class="w-full bg-dark-950 border border-slate-800 rounded-lg p-3 text-sm text-slate-200 font-mono focus:border-emerald-500 focus:outline-none"></textarea>
          </div>
          <div>
            <label class="block text-sm font-semibold text-rose-400 mb-1 flex items-center space-x-2">
              <i class="fa-solid fa-ban"></i>
              <span>強烈排斥主題 (Negative Topics - 直接淘汰)</span>
            </label>
            <p class="text-xs text-slate-400 mb-2">砍掉 80% 雜訊的關鍵：八卦、農場標題、業配新聞</p>
            <textarea id="profile-negative" rows="4" class="w-full bg-dark-950 border border-slate-800 rounded-lg p-3 text-sm text-slate-200 font-mono focus:border-rose-500 focus:outline-none"></textarea>
          </div>
          <button onclick="saveProfile()" class="w-full bg-emerald-500 hover:bg-emerald-600 text-dark-950 font-bold py-2.5 rounded-lg text-sm transition">
            儲存關注與降噪偏好
          </button>
        </div>
      </div>

      <!-- 分頁 3: 金鑰與模型 -->
      <div id="tab-settings" class="hidden space-y-4">
        <div class="bg-dark-900 border border-slate-800 rounded-xl p-5 space-y-4">
          <div>
            <label class="block text-xs font-medium text-slate-300 mb-1">LLM API Key</label>
            <input type="password" id="cfg-llm-key" class="w-full bg-dark-950 border border-slate-800 rounded-lg px-3 py-2 text-sm font-mono text-slate-200 focus:border-emerald-500 focus:outline-none">
          </div>
          <div>
            <label class="block text-xs font-medium text-slate-300 mb-1">LLM Model (預設 meta/llama-3.2-11b-vision-instruct 或 moonshotai/kimi-k3)</label>
            <input type="text" id="cfg-llm-model" class="w-full bg-dark-950 border border-slate-800 rounded-lg px-3 py-2 text-sm font-mono text-slate-200 focus:border-emerald-500 focus:outline-none">
          </div>
          <div>
            <label class="block text-xs font-medium text-slate-300 mb-1">LLM Base URL</label>
            <input type="text" id="cfg-llm-url" class="w-full bg-dark-950 border border-slate-800 rounded-lg px-3 py-2 text-sm font-mono text-slate-200 focus:border-emerald-500 focus:outline-none">
          </div>
          <div>
            <label class="block text-xs font-medium text-slate-300 mb-1">Bark Device Key (iPhone 推播)</label>
            <input type="text" id="cfg-bark-key" class="w-full bg-dark-950 border border-slate-800 rounded-lg px-3 py-2 text-sm font-mono text-slate-200 focus:border-emerald-500 focus:outline-none">
          </div>
          <button onclick="saveSettings()" class="w-full bg-emerald-500 hover:bg-emerald-600 text-dark-950 font-bold py-2.5 rounded-lg text-sm transition">
            儲存系統設定與金鑰
          </button>
        </div>
      </div>

      <!-- 執行狀態與即時日誌 -->
      <div class="bg-dark-900 border border-slate-800 rounded-xl p-4">
        <div class="flex items-center justify-between mb-2">
          <span class="text-xs font-bold text-slate-300 flex items-center space-x-2">
            <span class="w-2 h-2 rounded-full bg-emerald-500 animate-pulse"></span>
            <span>執行日誌 (Live Console Log)</span>
          </span>
          <button onclick="fetchLogs()" class="text-xs text-slate-500 hover:text-slate-300"><i class="fa-solid fa-rotate-right"></i> 重新整理</button>
        </div>
        <pre id="log-console" class="bg-dark-950 border border-slate-850 p-3 rounded-lg text-xs font-mono text-emerald-400/90 h-36 overflow-y-auto whitespace-pre-wrap">等待執行指令...</pre>
      </div>

    </div>

    <!-- 右側：最新電子報預覽 (5 cols) -->
    <div class="lg:col-span-5 flex flex-col space-y-3">
      <div class="flex items-center justify-between">
        <h2 class="text-sm font-bold text-slate-200 flex items-center space-x-2">
          <i class="fa-solid fa-newspaper text-emerald-400"></i>
          <span>最新電子報預覽 (latest_newsletter.md)</span>
        </h2>
        <button onclick="loadNewsletter()" class="text-xs text-emerald-400 hover:underline">重新讀取</button>
      </div>
      <div class="bg-dark-900 border border-slate-800 rounded-xl p-5 flex-1 overflow-y-auto max-h-[750px]">
        <div id="newsletter-content" class="prose prose-invert prose-sm max-w-none">
          <p class="text-slate-500 text-sm">載入中...</p>
        </div>
      </div>
    </div>

  </main>

  <!-- 彈窗：新增來源 -->
  <div id="add-modal" class="fixed inset-0 bg-black/70 backdrop-blur-sm z-50 flex items-center justify-center hidden">
    <div class="bg-dark-900 border border-slate-800 rounded-2xl w-full max-w-md p-6 space-y-4 shadow-2xl">
      <h3 class="text-base font-bold text-white flex items-center space-x-2">
        <i class="fa-solid fa-rss text-emerald-400"></i>
        <span>新增訂閱來源</span>
      </h3>
      <div>
        <label class="block text-xs text-slate-400 mb-1">來源名稱</label>
        <input id="new-src-name" placeholder="例如: Hacker News (Best)" class="w-full bg-dark-950 border border-slate-800 rounded-lg px-3 py-2 text-sm text-slate-200 focus:outline-none focus:border-emerald-500">
      </div>
      <div>
        <label class="block text-xs text-slate-400 mb-1">RSS / Feed URL</label>
        <input id="new-src-url" placeholder="https://example.com/rss" class="w-full bg-dark-950 border border-slate-800 rounded-lg px-3 py-2 text-sm text-slate-200 focus:outline-none focus:border-emerald-500">
      </div>
      <div class="flex justify-end space-x-2 pt-2">
        <button onclick="closeAddSourceModal()" class="px-4 py-2 text-xs text-slate-400 hover:text-white">取消</button>
        <button onclick="confirmAddSource()" class="px-4 py-2 text-xs bg-emerald-500 hover:bg-emerald-600 text-dark-950 font-bold rounded-lg">確認新增</button>
      </div>
    </div>
  </div>

  <script>
    let currentConfig = null;

    function switchTab(tab) {
      ['sources', 'profile', 'settings'].forEach(t => {
        document.getElementById(`tab-${t}`).classList.add('hidden');
        document.getElementById(`tab-${t}-btn`).classList.remove('border-emerald-500', 'text-emerald-400');
        document.getElementById(`tab-${t}-btn`).classList.add('border-transparent', 'text-slate-400');
      });
      document.getElementById(`tab-${tab}`).classList.remove('hidden');
      document.getElementById(`tab-${tab}-btn`).classList.add('border-emerald-500', 'text-emerald-400');
      document.getElementById(`tab-${tab}-btn`).classList.remove('border-transparent', 'text-slate-400');
    }

    async function loadData() {
      const res = await fetch('/api/config');
      const data = await res.json();
      currentConfig = data;

      // 渲染來源
      renderSources(data.sources);

      // 渲染 Profile
      document.getElementById('profile-interests').value = (data.profile.interests || []).join('\n');
      document.getElementById('profile-negative').value = (data.profile.negative_topics || []).join('\n');

      // 渲染 Settings
      document.getElementById('cfg-llm-key').value = data.env.LLM_API_KEY || '';
      document.getElementById('cfg-llm-model').value = data.env.LLM_MODEL || '';
      document.getElementById('cfg-llm-url').value = data.env.LLM_BASE_URL || '';
      document.getElementById('cfg-bark-key').value = data.env.BARK_DEVICE_KEY || '';

      loadNewsletter();
      fetchLogs();
    }

    function renderSources(sources) {
      const container = document.getElementById('sources-list');
      container.innerHTML = '';
      sources.forEach((src, idx) => {
        const item = document.createElement('div');
        item.className = 'bg-dark-900 border border-slate-800 rounded-xl p-4 flex items-center justify-between hover:border-slate-700 transition';
        item.innerHTML = `
          <div class="flex items-center space-x-3 overflow-hidden">
            <button onclick="toggleSource(${idx})" class="text-lg ${src.enabled ? 'text-emerald-400' : 'text-slate-600'}">
              <i class="fa-solid ${src.enabled ? 'fa-toggle-on' : 'fa-toggle-off'} text-2xl"></i>
            </button>
            <div class="truncate">
              <h4 class="font-bold text-sm text-slate-200 truncate ${!src.enabled ? 'line-through text-slate-500' : ''}">${src.name}</h4>
              <p class="text-xs text-slate-500 truncate font-mono">${src.url}</p>
            </div>
          </div>
          <div class="flex items-center space-x-2">
            <span class="text-[10px] px-2 py-0.5 rounded-full ${src.category === 'curated_rss' ? 'bg-emerald-500/10 text-emerald-400 border border-emerald-500/20' : 'bg-blue-500/10 text-blue-400 border border-blue-500/20'}">${src.category}</span>
            <button onclick="deleteSource(${idx})" class="text-slate-500 hover:text-rose-400 p-1.5"><i class="fa-solid fa-trash-can"></i></button>
          </div>
        `;
        container.appendChild(item);
      });
    }

    async function toggleSource(idx) {
      currentConfig.sources[idx].enabled = !currentConfig.sources[idx].enabled;
      await saveSources();
    }

    async function deleteSource(idx) {
      if (confirm('確定刪除此來源？')) {
        currentConfig.sources.splice(idx, 1);
        await saveSources();
      }
    }

    async function saveSources() {
      await fetch('/api/sources', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(currentConfig.sources)
      });
      loadData();
    }

    function openAddSourceModal() {
      document.getElementById('new-src-name').value = '';
      document.getElementById('new-src-url').value = '';
      document.getElementById('add-modal').classList.remove('hidden');
    }

    function closeAddSourceModal() {
      document.getElementById('add-modal').classList.add('hidden');
    }

    async function confirmAddSource() {
      const name = document.getElementById('new-src-name').value.trim();
      const url = document.getElementById('new-src-url').value.trim();
      if (!name || !url) return alert('請填寫完整資訊');
      currentConfig.sources.push({ name, url, category: 'curated_rss', enabled: true });
      closeAddSourceModal();
      await saveSources();
    }

    async function saveProfile() {
      const interests = document.getElementById('profile-interests').value.split('\n').map(s => s.trim()).filter(Boolean);
      const negative_topics = document.getElementById('profile-negative').value.split('\n').map(s => s.trim()).filter(Boolean);
      await fetch('/api/profile', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ interests, negative_topics })
      });
      alert('關注與降噪設定已成功儲存！');
    }

    async function saveSettings() {
      const payload = {
        LLM_API_KEY: document.getElementById('cfg-llm-key').value.trim(),
        LLM_MODEL: document.getElementById('cfg-llm-model').value.trim(),
        LLM_BASE_URL: document.getElementById('cfg-llm-url').value.trim(),
        BARK_DEVICE_KEY: document.getElementById('cfg-bark-key').value.trim()
      };
      await fetch('/api/settings', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload)
      });
      alert('金鑰與設定已成功更新！');
    }

    async function triggerPipeline() {
      const btn = document.getElementById('btn-run');
      btn.disabled = true;
      btn.innerHTML = '<i class="fa-solid fa-spinner fa-spin"></i> <span>執行中...</span>';
      await fetch('/api/run', { method: 'POST' });
      const interval = setInterval(async () => {
        const res = await fetch('/api/status');
        const status = await res.json();
        fetchLogs();
        if (!status.is_running) {
          clearInterval(interval);
          btn.disabled = false;
          btn.innerHTML = '<i class="fa-solid fa-bolt"></i> <span>立即執行雷達</span>';
          loadNewsletter();
        }
      }, 2000);
    }

    async function triggerTestBark() {
      const res = await fetch('/api/test_bark', { method: 'POST' });
      const data = await res.json();
      alert(data.message);
    }

    async function fetchLogs() {
      const res = await fetch('/api/logs');
      const data = await res.json();
      const box = document.getElementById('log-console');
      box.textContent = data.logs.join('\n') || '無日誌紀錄';
      box.scrollTop = box.scrollHeight;
    }

    async function loadNewsletter() {
      const res = await fetch('/api/newsletter');
      const data = await res.json();
      document.getElementById('newsletter-content').innerHTML = marked.parse(data.content || '*尚無電子報存檔*');
    }

    loadData();
    setInterval(fetchLogs, 5000);
  </script>
</body>
</html>
"""

class QuietRadarHandler(BaseHTTPRequestHandler):
    def _send_json(self, data, status=200):
        self.send_response(status)
        self.send_header('Content-Type', 'application/json; charset=utf-8')
        self.end_headers()
        self.wfile.write(json.dumps(data, ensure_ascii=False).encode('utf-8'))

    def do_GET(self):
        parsed = urlparse(self.path)
        path = parsed.path

        if path == "/" or path == "/index.html":
            self.send_response(200)
            self.send_header('Content-Type', 'text/html; charset=utf-8')
            self.end_headers()
            self.wfile.write(HTML_TEMPLATE.encode('utf-8'))

        elif path == "/api/config":
            # 讀取 sources.yaml 與 .env
            with open("sources.yaml", "r", encoding="utf-8") as f:
                cfg = yaml.safe_load(f)
            
            env_data = {}
            if os.path.exists(".env"):
                with open(".env", "r", encoding="utf-8") as f:
                    for line in f:
                        line = line.strip()
                        if line and not line.startswith("#") and "=" in line:
                            k, v = line.split("=", 1)
                            env_data[k.strip()] = v.strip()

            self._send_json({
                "sources": cfg.get("sources", []),
                "profile": cfg.get("profile", {}),
                "env": env_data
            })

        elif path == "/api/status":
            global IS_RUNNING
            self._send_json({"is_running": IS_RUNNING})

        elif path == "/api/logs":
            global RUNNING_LOGS
            self._send_json({"logs": RUNNING_LOGS[-50:]})

        elif path == "/api/newsletter":
            content = "*尚未生成電子報*"
            if os.path.exists("latest_newsletter.md"):
                with open("latest_newsletter.md", "r", encoding="utf-8") as f:
                    content = f.read()
            self._send_json({"content": content})

        else:
            self.send_response(404)
            self.end_headers()

    def do_POST(self):
        parsed = urlparse(self.path)
        path = parsed.path
        length = int(self.headers.get('content-length', 0))
        body = self.rfile.read(length).decode('utf-8') if length > 0 else "{}"
        payload = json.loads(body) if body else {}

        if path == "/api/sources":
            with open("sources.yaml", "r", encoding="utf-8") as f:
                cfg = yaml.safe_load(f)
            cfg["sources"] = payload
            with open("sources.yaml", "w", encoding="utf-8") as f:
                yaml.dump(cfg, f, allow_unicode=True, sort_keys=False)
            self._send_json({"status": "ok"})

        elif path == "/api/profile":
            with open("sources.yaml", "r", encoding="utf-8") as f:
                cfg = yaml.safe_load(f)
            cfg["profile"] = payload
            with open("sources.yaml", "w", encoding="utf-8") as f:
                yaml.dump(cfg, f, allow_unicode=True, sort_keys=False)
            self._send_json({"status": "ok"})

        elif path == "/api/settings":
            env_map = {}
            if os.path.exists(".env"):
                with open(".env", "r", encoding="utf-8") as f:
                    for line in f:
                        l = line.strip()
                        if l and not l.startswith("#") and "=" in l:
                            k, v = l.split("=", 1)
                            env_map[k.strip()] = v.strip()
            env_map.update(payload)
            with open(".env", "w", encoding="utf-8") as f:
                for k, v in env_map.items():
                    f.write(f"{k}={v}\n")
            self._send_json({"status": "ok"})

        elif path == "/api/run":
            global IS_RUNNING, RUNNING_LOGS
            if not IS_RUNNING:
                IS_RUNNING = True
                def _worker():
                    global IS_RUNNING, RUNNING_LOGS
                    RUNNING_LOGS.append(f"[{datetime.now().strftime('%H:%M:%S')}] 🚀 開始執行 QuietRadar 管道...")
                    proc = subprocess.Popen(
                        [sys.executable, "pipeline.py"],
                        stdout=subprocess.PIPE,
                        stderr=subprocess.STDOUT,
                        text=True,
                        encoding="utf-8",
                        errors="replace"
                    )
                    for line in iter(proc.stdout.readline, ''):
                        if line:
                            RUNNING_LOGS.append(line.strip())
                    proc.stdout.close()
                    proc.wait()
                    RUNNING_LOGS.append(f"[{datetime.now().strftime('%H:%M:%S')}] 🏁 執行完成，Exit Code: {proc.returncode}")
                    IS_RUNNING = False

                threading.Thread(target=_worker, daemon=True).start()
            self._send_json({"status": "started"})

        elif path == "/api/test_bark":
            try:
                proc = subprocess.run([sys.executable, "test_pure_links_bark.py"], capture_output=True, text=True, encoding="utf-8")
                self._send_json({"status": "ok", "message": "Bark 推播指令已發送！請檢查手機。"})
            except Exception as e:
                self._send_json({"status": "error", "message": str(e)}, status=500)

        else:
            self.send_response(404)
            self.end_headers()

def run_server():
    server_address = ('127.0.0.1', PORT)
    httpd = HTTPServer(server_address, QuietRadarHandler)
    url = f"http://127.0.0.1:{PORT}"
    print(f"✨ QuietRadar 控制台已啟動：{url}")
    threading.Timer(1.0, lambda: webbrowser.open(url)).start()
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\n👋 服務已停止")

if __name__ == "__main__":
    run_server()
