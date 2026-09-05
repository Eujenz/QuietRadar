import os
import sys
import re
import json
import yaml
import subprocess
import threading
import webbrowser
from datetime import datetime, timezone, timedelta
try:
    from zoneinfo import ZoneInfo
    TAIPEI_TZ = ZoneInfo("Asia/Taipei")
except Exception:
    TAIPEI_TZ = timezone(timedelta(hours=8))

def get_taipei_now() -> datetime:
    return datetime.now(TAIPEI_TZ)

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
        <button id="btn-run" onclick="triggerPipeline(false)" class="inline-flex items-center space-x-2 bg-emerald-500 hover:bg-emerald-600 text-dark-950 font-bold px-4 py-2 rounded-lg transition shadow-lg shadow-emerald-500/20 text-sm">
          <i class="fa-solid fa-bolt"></i>
          <span>立即執行雷達</span>
        </button>
        <button id="btn-test-run" onclick="triggerPipeline(true)" class="inline-flex items-center space-x-2 bg-amber-500 hover:bg-amber-600 text-dark-950 font-bold px-3.5 py-2 rounded-lg transition shadow-lg shadow-amber-500/20 text-sm" title="無新文章時自動使用現有文章測試 LLM 蒸餾與 OUTPUT 生成，且不寫入防重">
          <i class="fa-solid fa-vial"></i>
          <span>測試 OUTPUT (忽略防重)</span>
        </button>
        <button onclick="triggerTestBark()" class="inline-flex items-center space-x-2 bg-slate-800 hover:bg-slate-700 text-slate-200 font-medium px-3 py-2 rounded-lg transition border border-slate-700 text-sm" title="將最新電子報 (latest_newsletter.md) 實際推送至您的 Bark 手機">
          <i class="fa-solid fa-mobile-screen-button text-emerald-400"></i>
          <span>推播最新電子報至 Bark</span>
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
        <button onclick="switchTab('prompt')" id="tab-prompt-btn" class="pb-3 text-sm font-semibold border-b-2 border-transparent text-slate-400 hover:text-slate-200 flex items-center space-x-2">
          <i class="fa-solid fa-wand-magic-sparkles"></i>
          <span>分析提示詞與框架</span>
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

      <!-- 分頁 3: 提示詞微調與固定輸出框架 -->
      <div id="tab-prompt" class="hidden space-y-4">
        <div class="bg-dark-900 border border-slate-800 rounded-xl p-5 space-y-4">
          <div class="space-y-2">
            <div class="flex items-center justify-between mb-1">
              <label class="block text-sm font-semibold text-emerald-400 flex items-center space-x-2">
                <i class="fa-solid fa-wand-magic-sparkles"></i>
                <span>情報雷達視角與自訂提示詞 (Prompt Templates)</span>
              </label>
              <span class="text-[11px] font-mono text-slate-400">
                目前啟用：<span id="active-template-badge" class="text-emerald-400 font-bold">載入中...</span>
              </span>
            </div>
            
            <!-- 快速抽換模板選擇欄 -->
            <div class="flex flex-wrap items-center justify-between gap-2 p-2.5 bg-dark-950 border border-slate-800 rounded-lg">
              <div class="flex items-center space-x-2">
                <span class="text-xs text-slate-400 font-medium">切換視角模板：</span>
                <select id="prompt-template-select" onchange="onTemplateChange()" class="bg-dark-900 border border-slate-700 text-xs text-slate-200 rounded px-2.5 py-1.5 font-mono focus:outline-none focus:border-emerald-500"></select>
                <button onclick="applyActiveTemplate()" class="text-xs bg-emerald-500 hover:bg-emerald-600 text-dark-950 font-bold px-3 py-1.5 rounded transition shadow-sm">設為啟用</button>
              </div>
              <div class="flex items-center space-x-2">
                <button onclick="saveAsNewTemplate()" class="text-xs bg-dark-900 hover:bg-slate-800 text-slate-300 border border-slate-700 px-3 py-1.5 rounded transition">
                  <i class="fa-solid fa-plus mr-1"></i>另存為新模板
                </button>
              </div>
            </div>

            <p class="text-xs text-slate-400 mb-1">編輯當前模板的分析視角、評估協定或專案審查準則：</p>
            <textarea id="custom-prompt" rows="9" class="w-full bg-dark-950 border border-slate-800 rounded-lg p-3 text-xs text-slate-200 font-mono focus:border-emerald-500 focus:outline-none"></textarea>
          </div>

          <button onclick="saveCurrentTemplate()" class="w-full bg-emerald-500 hover:bg-emerald-600 text-dark-950 font-bold py-2 rounded-lg text-sm transition">
            儲存當前模板內容
          </button>

          <!-- 標題前置漏斗與內文研讀深度設定 -->
          <div class="border-t border-slate-800 pt-4 space-y-3">
            <div class="flex items-center justify-between">
              <label class="block text-sm font-semibold text-amber-400 flex items-center space-x-2">
                <i class="fa-solid fa-filter text-amber-400"></i>
                <span>標題前置漏斗過濾、兩階段洗滌與研讀深度設定</span>
              </label>
              <span class="text-[11px] text-amber-400/80 font-mono">Title Funnel & Humanizer</span>
            </div>
            <p class="text-xs text-slate-400">控制標題前置過濾條件與 Stage 2 speak-human-tw 語言洗滌模組：</p>
            
            <div class="grid grid-cols-1 sm:grid-cols-3 gap-3 pt-1">
              <div class="space-y-1">
                <label class="block text-[11px] font-mono text-slate-300">
                  標題前置漏斗開關
                </label>
                <div class="flex items-center space-x-2 bg-dark-950 border border-slate-800 rounded-lg p-2">
                  <input type="checkbox" id="cfg-title-filter-enabled" class="accent-amber-500 w-4 h-4 cursor-pointer">
                  <label for="cfg-title-filter-enabled" class="text-xs text-slate-300 cursor-pointer">啟用標題漏斗</label>
                </div>
              </div>
              <div class="space-y-1">
                <label class="block text-[11px] font-mono text-slate-300">
                  兩階段語言洗滌 (Stage 2)
                </label>
                <div class="flex items-center space-x-2 bg-dark-950 border border-slate-800 rounded-lg p-2">
                  <input type="checkbox" id="cfg-two-stage-humanizer" class="accent-emerald-500 w-4 h-4 cursor-pointer">
                  <label for="cfg-two-stage-humanizer" class="text-xs text-emerald-400 cursor-pointer">啟用 speak-human-tw</label>
                </div>
              </div>
              <div class="space-y-1">
                <label class="block text-[11px] font-mono text-slate-300">
                  漏斗過濾模式
                </label>
                <select id="cfg-title-filter-mode" class="w-full bg-dark-950 border border-slate-800 rounded-lg px-3 py-2 text-xs font-mono text-slate-200 focus:border-amber-500 focus:outline-none">
                  <option value="smart">Smart 智慧相關度（殺黑名單，優先高分）</option>
                  <option value="strict">Strict 嚴格模式（明確命中關鍵字）</option>
                </select>
              </div>
            </div>

            <div class="grid grid-cols-1 sm:grid-cols-3 gap-3 pt-1">
              <div>
                <label class="block text-[11px] font-mono text-slate-300 mb-1">
                  每篇內文研讀字數 (字元)
                </label>
                <input type="number" id="cfg-snippet-len" min="0" max="10000" step="100" class="w-full bg-dark-950 border border-slate-800 rounded-lg px-3 py-2 text-xs font-mono text-slate-200 focus:border-amber-500 focus:outline-none" placeholder="預設 900 (填 0 代表完整不截斷)">
                <p class="text-[10px] text-slate-500 mt-1">一般 800~1500 字可涵蓋完整核心論點。填 0 代表全送。</p>
              </div>
              <div>
                <label class="block text-[11px] font-mono text-slate-300 mb-1">
                  送入研讀候選池上限 (篇數)
                </label>
                <input type="number" id="cfg-max-pool" min="5" max="100" step="1" class="w-full bg-dark-950 border border-slate-800 rounded-lg px-3 py-2 text-xs font-mono text-slate-200 focus:border-amber-500 focus:outline-none" placeholder="建議 30~50 篇">
                <p class="text-[10px] text-slate-500 mt-1">單次送進大模型大腦的候選總數（自動配置約 28% 跨界漫遊素材）。</p>
              </div>
              <div>
                <label class="block text-[11px] font-mono text-slate-300 mb-1">
                  最終精選篇數上限 (篇數)
                </label>
                <input type="number" id="cfg-max-output" min="3" max="30" step="1" class="w-full bg-dark-950 border border-slate-800 rounded-lg px-3 py-2 text-xs font-mono text-slate-200 focus:border-amber-500 focus:outline-none" placeholder="預設 15 篇 (AI 品質自適應)">
                <p class="text-[10px] text-slate-500 mt-1">大模型依素材質量自由精選，寧缺毋濫；維持 7~8成核心 : 2~3成跨界。</p>
              </div>
            </div>
            <button onclick="savePipelineSettings()" class="w-full bg-amber-500 hover:bg-amber-600 text-dark-950 font-bold py-2 rounded-lg text-sm transition shadow-lg shadow-amber-500/20">
              儲存標題漏斗與研讀深度設定
            </button>
          </div>

          <!-- 輸出結構框架 (開放自訂與儲存) -->
          <div class="border-t border-slate-800 pt-4 space-y-3">
            <div class="flex items-center justify-between">
              <label class="block text-sm font-semibold text-sky-400 flex items-center space-x-2">
                <i class="fa-solid fa-layer-group"></i>
                <span>輸出結構框架模板（可自訂與儲存）</span>
              </label>
              <span class="text-[11px] text-slate-400">系統注入渲染規則</span>
            </div>
            <p class="text-xs text-slate-400">可自由調整電子報與 Bark 推播的開頭、來源標題與單則排版：</p>
            
            <div class="space-y-3">
              <div>
                <label class="block text-[11px] font-mono text-slate-300 mb-1">
                  1. 頂部標題與 Meta (Header) <span class="text-slate-500">支援 {time}, {count}</span>
                </label>
                <textarea id="tpl-header" rows="3" oninput="updateTemplatePreview()" class="w-full bg-dark-950 border border-slate-800 rounded-lg p-2.5 text-xs text-slate-200 font-mono focus:border-sky-500 focus:outline-none"></textarea>
              </div>

              <div>
                <label class="block text-[11px] font-mono text-slate-300 mb-1">
                  2. 彙整、重點論述 (Overview) <span class="text-slate-500">支援 {overview}</span>
                </label>
                <textarea id="tpl-overview" rows="3" oninput="updateTemplatePreview()" class="w-full bg-dark-950 border border-slate-800 rounded-lg p-2.5 text-xs text-slate-200 font-mono focus:border-sky-500 focus:outline-none" placeholder="例如: > 💡 **今日情報洞察**：&#10;> {overview}&#10;---"></textarea>
              </div>

              <div class="grid grid-cols-1 sm:grid-cols-2 gap-3">
                <div>
                  <label class="block text-[11px] font-mono text-slate-300 mb-1">
                    3. 來源分類標題 (Group) <span class="text-slate-500">支援 {source}, {count}</span>
                  </label>
                  <input id="tpl-group" oninput="updateTemplatePreview()" class="w-full bg-dark-950 border border-slate-800 rounded-lg px-3 py-2 text-xs text-slate-200 font-mono focus:border-sky-500 focus:outline-none">
                </div>
                <div>
                  <label class="block text-[11px] font-mono text-slate-300 mb-1">
                    4. 單則項目排版 (Item) <span class="text-slate-500">支援 {index}, {title}, {url}</span>
                  </label>
                  <input id="tpl-item" oninput="updateTemplatePreview()" class="w-full bg-dark-950 border border-slate-800 rounded-lg px-3 py-2 text-xs text-slate-200 font-mono focus:border-sky-500 focus:outline-none">
                </div>
              </div>

              <div>
                <label class="block text-[11px] font-mono text-slate-300 mb-1">
                  5. 底部結尾附註 (Footer) <span class="text-slate-500">支援 {time}, {count}</span>
                </label>
                <textarea id="tpl-footer" rows="2" oninput="updateTemplatePreview()" class="w-full bg-dark-950 border border-slate-800 rounded-lg p-2.5 text-xs text-slate-200 font-mono focus:border-sky-500 focus:outline-none"></textarea>
              </div>

              <!-- 6. Bark 手機推播標題格式控制 -->
              <div class="border-t border-slate-800/80 pt-3 space-y-2.5">
                <div class="flex items-center justify-between">
                  <label class="block text-xs font-semibold text-emerald-400 flex items-center space-x-1.5">
                    <i class="fa-solid fa-mobile-screen"></i>
                    <span>6. Bark 手機推播卡片標題自訂格式（完全由框架控制，非程式硬編碼）</span>
                  </label>
                  <span class="text-[10px] text-slate-400 font-mono">支援 {title}, {page}, {total}, {count}, {time}</span>
                </div>
                <div class="grid grid-cols-1 sm:grid-cols-3 gap-3">
                  <div>
                    <label class="block text-[11px] font-mono text-slate-300 mb-1">首頁/首段卡片標題</label>
                    <input id="tpl-bark-title" class="w-full bg-dark-950 border border-slate-800 rounded-lg px-3 py-2 text-xs text-slate-200 font-mono focus:border-sky-500 focus:outline-none" placeholder="預設空白（無標題，可填 {title} 等）">
                  </div>
                  <div>
                    <label class="block text-[11px] font-mono text-slate-300 mb-1">分頁續接卡片標題</label>
                    <input id="tpl-bark-continue" class="w-full bg-dark-950 border border-slate-800 rounded-lg px-3 py-2 text-xs text-slate-200 font-mono focus:border-sky-500 focus:outline-none" placeholder="預設空白（無標題，可填 ({page}/{total}) 等）">
                  </div>
                  <div>
                    <label class="block text-[11px] font-mono text-slate-300 mb-1">引用來源卡片標題</label>
                    <input id="tpl-bark-sources" class="w-full bg-dark-950 border border-slate-800 rounded-lg px-3 py-2 text-xs text-slate-200 font-mono focus:border-sky-500 focus:outline-none" placeholder="預設空白（無標題，可填 ({page}/{total}) 等）">
                  </div>
                </div>
              </div>

              <button onclick="saveTemplate()" class="w-full bg-sky-500 hover:bg-sky-600 text-dark-950 font-bold py-2 rounded-lg text-sm transition shadow-lg shadow-sky-500/20">
                儲存輸出結構框架
              </button>

              <!-- 即時 Markdown 渲染預覽視窗 -->
              <div class="mt-4">
                <label class="block text-[11px] font-bold text-slate-300 mb-2 flex items-center space-x-1.5">
                  <i class="fa-solid fa-eye text-emerald-400"></i>
                  <span>框架渲染即時預覽 (Live Markdown Preview - 如同電子報實況)</span>
                </label>
                <div class="bg-dark-950 border border-slate-800 rounded-xl p-5 overflow-y-auto max-h-[380px]">
                  <div id="tpl-preview" class="prose prose-invert prose-sm max-w-none"></div>
                </div>
              </div>
            </div>
          </div>
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
      <div>
        <label class="block text-xs text-slate-400 mb-1">信源分類與定位 (Category)</label>
        <select id="new-src-cat" class="w-full bg-dark-950 border border-slate-800 rounded-lg px-3 py-2 text-xs font-mono text-slate-200 focus:outline-none focus:border-emerald-500">
          <option value="curated_rss">🎯 核心業務信源 (curated_rss - 經受讀者關注主題嚴格審查)</option>
          <option value="cross_domain_ideas">✨ 跨界漫遊靈感 (cross_domain_ideas - 槓鈴特赦漏斗，異花授粉)</option>
        </select>
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
      ['sources', 'profile', 'prompt', 'settings'].forEach(t => {
        const el = document.getElementById(`tab-${t}`);
        const btn = document.getElementById(`tab-${t}-btn`);
        if (el) el.classList.add('hidden');
        if (btn) {
          btn.classList.remove('border-emerald-500', 'text-emerald-400');
          btn.classList.add('border-transparent', 'text-slate-400');
        }
      });
      const targetEl = document.getElementById(`tab-${tab}`);
      const targetBtn = document.getElementById(`tab-${tab}-btn`);
      if (targetEl) targetEl.classList.remove('hidden');
      if (targetBtn) {
        targetBtn.classList.add('border-emerald-500', 'text-emerald-400');
        targetBtn.classList.remove('border-transparent', 'text-slate-400');
      }
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

      // 渲染 Prompt & 模板清單
      await loadPrompts();

      // 渲染研讀深度與候選池設定
      const ps = data.pipeline_settings || {};
      if (document.getElementById('cfg-title-filter-enabled')) {
        document.getElementById('cfg-title-filter-enabled').checked = ps.title_filter_enabled !== false;
      }
      if (document.getElementById('cfg-two-stage-humanizer')) {
        document.getElementById('cfg-two-stage-humanizer').checked = ps.enable_two_stage_humanizer !== false;
      }
      if (document.getElementById('cfg-title-filter-mode')) {
        document.getElementById('cfg-title-filter-mode').value = ps.title_filter_mode || 'smart';
      }
      document.getElementById('cfg-snippet-len').value = ps.content_snippet_length !== undefined ? ps.content_snippet_length : 900;
      document.getElementById('cfg-max-pool').value = ps.max_candidate_pool !== undefined ? ps.max_candidate_pool : 50;
      document.getElementById('cfg-max-output').value = ps.max_output_items !== undefined ? ps.max_output_items : 15;

      // 渲染 Output Template
      const tpl = data.output_template || {};
      document.getElementById('tpl-header').value = tpl.header || '# ⚡ QuietRadar 降噪科技電子報\n> 出刊時間：{time} | 本期精選：{count} 則 | 去 AI 雜訊率：約 80%\n---';
      document.getElementById('tpl-overview').value = tpl.overview !== undefined ? tpl.overview : '> 💡 **今日情報洞察**：\n> {overview}\n---';
      document.getElementById('tpl-group').value = tpl.group_header || '## 📰 【{source}】({count} 則)';
      document.getElementById('tpl-item').value = tpl.item_format || '{index}. [{title}]({url})';
      document.getElementById('tpl-footer').value = tpl.footer || '---\n*本電子報由 QuietRadar 依據讀者關注特徵自動蒸餾產出，遵守 speak-human-tw 去 AI 味與台灣在地化規範。*';
      document.getElementById('tpl-bark-title').value = tpl.bark_title || '';
      document.getElementById('tpl-bark-continue').value = tpl.bark_continuation_title || '';
      document.getElementById('tpl-bark-sources').value = tpl.bark_sources_title || '';
      updateTemplatePreview();

      // 渲染 Settings
      document.getElementById('cfg-llm-key').value = data.env.LLM_API_KEY || '';
      document.getElementById('cfg-llm-model').value = data.env.LLM_MODEL || '';
      document.getElementById('cfg-llm-url').value = data.env.LLM_BASE_URL || '';
      document.getElementById('cfg-bark-key').value = data.env.BARK_DEVICE_KEY || '';

      loadNewsletter();
      fetchLogs();
    }

    let promptTemplatesCache = {};
    let activeTemplateName = 'solopreneur';

    async function loadPrompts() {
      try {
        const res = await fetch('/api/prompts');
        const data = await res.json();
        activeTemplateName = data.active || 'solopreneur';
        promptTemplatesCache = {};
        const sel = document.getElementById('prompt-template-select');
        sel.innerHTML = '';
        
        (data.templates || []).forEach(t => {
          promptTemplatesCache[t.name] = t.content;
          const opt = document.createElement('option');
          opt.value = t.name;
          opt.textContent = t.name + (t.name === activeTemplateName ? ' (啟用中)' : '');
          sel.appendChild(opt);
        });

        sel.value = activeTemplateName;
        document.getElementById('active-template-badge').textContent = activeTemplateName;
        document.getElementById('custom-prompt').value = promptTemplatesCache[activeTemplateName] || '';
      } catch (e) {
        console.error('Failed to load prompts:', e);
      }
    }

    function onTemplateChange() {
      const sel = document.getElementById('prompt-template-select');
      const selectedName = sel.value;
      if (promptTemplatesCache[selectedName]) {
        document.getElementById('custom-prompt').value = promptTemplatesCache[selectedName];
      }
    }

    async function applyActiveTemplate() {
      const sel = document.getElementById('prompt-template-select');
      const selectedName = sel.value;
      await fetch('/api/prompts/select', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ template_name: selectedName })
      });
      activeTemplateName = selectedName;
      await loadPrompts();
      alert(`已成功將【${selectedName}】設為當前執行啟用模板！`);
    }

    async function saveCurrentTemplate() {
      const sel = document.getElementById('prompt-template-select');
      const currentName = sel.value || 'solopreneur';
      const content = document.getElementById('custom-prompt').value;
      await fetch('/api/prompts/save', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ template_name: currentName, content: content })
      });
      promptTemplatesCache[currentName] = content;
      alert(`模板【${currentName}】內容已成功儲存！`);
    }

    async function saveAsNewTemplate() {
      const newName = prompt('請輸入新模板的英文代號名稱（例如：marketing、tech_lead、investor）：');
      if (!newName || !newName.trim()) return;
      const cleanName = newName.trim().replace(/[^a-zA-Z0-9_\-]/g, '_');
      const content = document.getElementById('custom-prompt').value;
      await fetch('/api/prompts/save', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ template_name: cleanName, content: content, set_active: true })
      });
      await loadPrompts();
      alert(`新模板【${cleanName}】已建立並設為啟用！`);
    }

    function updateTemplatePreview() {
      const header = (document.getElementById('tpl-header').value || '').replace('{time}', '2026-09-03 13:30').replace('{count}', '2');
      const overview_tpl = document.getElementById('tpl-overview').value || '';
      const overview_rendered = overview_tpl ? overview_tpl.replace('{overview}', '本期重點聚焦於開源模型生態突破與後端系統架構效能調優，多項工具迎來重大技術革新。') : '';
      const group = (document.getElementById('tpl-group').value || '').replace('{source}', '數位時代 BusinessNext').replace('{count}', '2');
      const item1 = (document.getElementById('tpl-item').value || '').replace('{index}', '1').replace('{title}', '科技巨頭最新 AI 架構發布').replace('{url}', 'https://example.com/article1').replace('{source}', '數位時代 BusinessNext');
      const item2 = (document.getElementById('tpl-item').value || '').replace('{index}', '2').replace('{title}', '開源社群突破性成果解析').replace('{url}', 'https://example.com/article2').replace('{source}', '數位時代 BusinessNext');
      const footer = (document.getElementById('tpl-footer').value || '').replace('{time}', '2026-09-03 13:30').replace('{count}', '2');
      
      let parts = [];
      if (header.trim()) parts.push(header.trim());
      if (overview_rendered.trim()) parts.push(overview_rendered.trim());
      if (group.trim() || item1.trim()) parts.push(`${group}\n\n${item1}\n\n${item2}`.trim());
      if (footer.trim()) parts.push(footer.trim());

      const fullMarkdown = parts.join('\n\n');
      document.getElementById('tpl-preview').innerHTML = marked.parse(fullMarkdown);
    }

    async function saveTemplate() {
      const headerVal = document.getElementById('tpl-header').value;
      const output_template = {
        header: headerVal,
        overview: document.getElementById('tpl-overview').value,
        group_header: document.getElementById('tpl-group').value,
        item_format: document.getElementById('tpl-item').value,
        footer: document.getElementById('tpl-footer').value,
        bark_title: document.getElementById('tpl-bark-title').value.trim(),
        bark_continuation_title: document.getElementById('tpl-bark-continue').value.trim(),
        bark_sources_title: document.getElementById('tpl-bark-sources').value.trim()
      };
      await fetch('/api/template', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(output_template)
      });
      await loadNewsletter();
      alert('輸出結構框架模板已成功儲存至 sources.yaml，並已即時以新版型重新渲染最新電子報！');
    }

    async function savePipelineSettings() {
      const title_filter_enabled = document.getElementById('cfg-title-filter-enabled').checked;
      const enable_two_stage_humanizer = document.getElementById('cfg-two-stage-humanizer').checked;
      const title_filter_mode = document.getElementById('cfg-title-filter-mode').value;
      const snippet_len = parseInt(document.getElementById('cfg-snippet-len').value, 10);
      const max_pool = parseInt(document.getElementById('cfg-max-pool').value, 10);
      const max_output = parseInt(document.getElementById('cfg-max-output').value, 10);
      await fetch('/api/pipeline_settings', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          title_filter_enabled,
          enable_two_stage_humanizer,
          title_filter_mode,
          content_snippet_length: isNaN(snippet_len) ? 900 : snippet_len,
          max_candidate_pool: isNaN(max_pool) ? 50 : max_pool,
          max_output_items: isNaN(max_output) ? 15 : max_output
        })
      });
      alert('已成功儲存標題前置漏斗、語言洗滌與研讀深度設定！');
    }

    function renderSources(sources) {
      const container = document.getElementById('sources-list');
      container.innerHTML = '';
      sources.forEach((src, idx) => {
        const item = document.createElement('div');
        item.className = 'bg-dark-900 border border-slate-800 rounded-xl p-4 flex flex-col sm:flex-row sm:items-center justify-between gap-3 hover:border-slate-700 transition';
        const isCross = src.category === 'cross_domain_ideas';
        item.innerHTML = `
          <div class="flex items-center space-x-3 overflow-hidden flex-1">
            <button onclick="toggleSource(${idx})" class="text-lg ${src.enabled ? 'text-emerald-400' : 'text-slate-600'} flex-shrink-0" title="${src.enabled ? '點擊停用' : '點擊啟用'}">
              <i class="fa-solid ${src.enabled ? 'fa-toggle-on' : 'fa-toggle-off'} text-2xl"></i>
            </button>
            <div class="truncate">
              <div class="flex items-center space-x-2">
                <h4 class="font-bold text-sm text-slate-200 truncate ${!src.enabled ? 'line-through text-slate-500' : ''}">${src.name}</h4>
                ${isCross ? '<span class="text-[10px] bg-amber-500/10 text-amber-400 border border-amber-500/20 px-1.5 py-0.2 rounded font-mono">✨ 跨界漫遊 (特赦漏斗)</span>' : '<span class="text-[10px] bg-emerald-500/10 text-emerald-400 border border-emerald-500/20 px-1.5 py-0.2 rounded font-mono">🎯 核心業務 (主題審查)</span>'}
              </div>
              <p class="text-xs text-slate-500 truncate font-mono">${src.url}</p>
            </div>
          </div>
          <div class="flex items-center space-x-2 flex-shrink-0">
            <!-- 手動切換 curated_rss 與 cross_domain_ideas -->
            <select onchange="changeSourceCategory(${idx}, this.value)" class="bg-dark-950 border border-slate-700 text-xs text-slate-200 rounded px-2.5 py-1.5 font-mono focus:outline-none focus:border-emerald-500 cursor-pointer" title="手動切換信源分類與槓鈴特赦屬性">
              <option value="curated_rss" ${!isCross ? 'selected' : ''}>🎯 curated_rss (核心)</option>
              <option value="cross_domain_ideas" ${isCross ? 'selected' : ''}>✨ cross_domain_ideas (跨界)</option>
            </select>
            <button onclick="deleteSource(${idx})" class="text-slate-500 hover:text-rose-400 p-1.5" title="刪除此來源"><i class="fa-solid fa-trash-can"></i></button>
          </div>
        `;
        container.appendChild(item);
      });
    }

    async function changeSourceCategory(idx, newCategory) {
      const src = currentConfig.sources[idx];
      src.category = newCategory;
      src.serendipity = (newCategory === 'cross_domain_ideas');
      await saveSources();
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
      document.getElementById('new-src-cat').value = 'curated_rss';
      document.getElementById('add-modal').classList.remove('hidden');
    }

    function closeAddSourceModal() {
      document.getElementById('add-modal').classList.add('hidden');
    }

    async function confirmAddSource() {
      const name = document.getElementById('new-src-name').value.trim();
      const url = document.getElementById('new-src-url').value.trim();
      const category = document.getElementById('new-src-cat').value;
      if (!name || !url) return alert('請填寫完整資訊');
      const serendipity = (category === 'cross_domain_ideas');
      currentConfig.sources.push({ name, url, category, enabled: true, serendipity });
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

    async function triggerPipeline(isTest = false) {
      const btnRun = document.getElementById('btn-run');
      const btnTest = document.getElementById('btn-test-run');
      const activeBtn = isTest ? btnTest : btnRun;
      
      btnRun.disabled = true;
      btnTest.disabled = true;
      activeBtn.innerHTML = '<i class="fa-solid fa-spinner fa-spin"></i> <span>執行中...</span>';
      
      await fetch('/api/run', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ mode: isTest ? 'test' : 'normal' })
      });

      const interval = setInterval(async () => {
        const res = await fetch('/api/status');
        const status = await res.json();
        fetchLogs();
        if (!status.is_running) {
          clearInterval(interval);
          btnRun.disabled = false;
          btnTest.disabled = false;
          btnRun.innerHTML = '<i class="fa-solid fa-bolt"></i> <span>立即執行雷達</span>';
          btnTest.innerHTML = '<i class="fa-solid fa-vial"></i> <span>測試 OUTPUT (忽略防重)</span>';
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
                "custom_prompt": cfg.get("custom_prompt", ""),
                "output_template": cfg.get("output_template", {}),
                "pipeline_settings": cfg.get("pipeline_settings", {"content_snippet_length": 800, "max_candidate_pool": 10}),
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

        elif path == "/api/prompts":
            prompts_dir = "prompts"
            os.makedirs(prompts_dir, exist_ok=True)
            with open("sources.yaml", "r", encoding="utf-8") as f:
                cfg = yaml.safe_load(f)
            active_tpl = cfg.get("pipeline_settings", {}).get("active_prompt_template", "solopreneur")
            
            templates = []
            for fn in sorted(os.listdir(prompts_dir)):
                if fn.endswith(".md"):
                    tpl_name = fn[:-3]
                    fpath = os.path.join(prompts_dir, fn)
                    try:
                        with open(fpath, "r", encoding="utf-8") as tf:
                            content = tf.read()
                        templates.append({"name": tpl_name, "content": content})
                    except Exception:
                        pass
            
            if not templates:
                cur_prompt = cfg.get("custom_prompt", "")
                templates.append({"name": "solopreneur", "content": cur_prompt})
                
            self._send_json({"active": active_tpl, "templates": templates})

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

        elif path == "/api/prompt":
            with open("sources.yaml", "r", encoding="utf-8") as f:
                cfg = yaml.safe_load(f)
            cfg["custom_prompt"] = payload.get("custom_prompt", "")
            with open("sources.yaml", "w", encoding="utf-8") as f:
                yaml.dump(cfg, f, allow_unicode=True, sort_keys=False)
            self._send_json({"status": "ok"})

        elif path == "/api/prompts/select":
            tpl_name = payload.get("template_name", "solopreneur")
            with open("sources.yaml", "r", encoding="utf-8") as f:
                cfg = yaml.safe_load(f)
            if "pipeline_settings" not in cfg:
                cfg["pipeline_settings"] = {}
            cfg["pipeline_settings"]["active_prompt_template"] = tpl_name
            with open("sources.yaml", "w", encoding="utf-8") as f:
                yaml.dump(cfg, f, allow_unicode=True, sort_keys=False)
            self._send_json({"status": "ok", "active": tpl_name})

        elif path == "/api/prompts/save":
            tpl_name = payload.get("template_name", "custom").strip()
            content = payload.get("content", "")
            set_active = payload.get("set_active", False)
            
            prompts_dir = "prompts"
            os.makedirs(prompts_dir, exist_ok=True)
            fpath = os.path.join(prompts_dir, f"{tpl_name}.md")
            with open(fpath, "w", encoding="utf-8") as f:
                f.write(content)
                
            with open("sources.yaml", "r", encoding="utf-8") as f:
                cfg = yaml.safe_load(f)
            if set_active:
                if "pipeline_settings" not in cfg:
                    cfg["pipeline_settings"] = {}
                cfg["pipeline_settings"]["active_prompt_template"] = tpl_name
            cfg["custom_prompt"] = content
            with open("sources.yaml", "w", encoding="utf-8") as f:
                yaml.dump(cfg, f, allow_unicode=True, sort_keys=False)
            self._send_json({"status": "ok", "template_name": tpl_name})

        elif path == "/api/template":
            with open("sources.yaml", "r", encoding="utf-8") as f:
                cfg = yaml.safe_load(f)
            cfg["output_template"] = payload
            with open("sources.yaml", "w", encoding="utf-8") as f:
                yaml.dump(cfg, f, allow_unicode=True, sort_keys=False)

            # 關鍵修復：儲存新模板後，立即讀取 latest_distilled.json 重新渲染 latest_newsletter.md
            if os.path.exists("latest_distilled.json"):
                try:
                    with open("latest_distilled.json", "r", encoding="utf-8") as df:
                        d_data = json.load(df)
                    from pipeline import generate_newsletter_file
                    generate_newsletter_file(
                        d_data.get("items", []),
                        filepath="latest_newsletter.md",
                        template=payload,
                        overview=d_data.get("overview", "")
                    )
                except Exception as e:
                    print(f"Error re-rendering newsletter with new template: {e}")

            self._send_json({"status": "ok"})

        elif path == "/api/pipeline_settings":
            with open("sources.yaml", "r", encoding="utf-8") as f:
                cfg = yaml.safe_load(f)
            if "pipeline_settings" not in cfg:
                cfg["pipeline_settings"] = {}
            cfg["pipeline_settings"].update(payload)
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
            is_test_mode = (payload.get("mode") == "test")
            if not IS_RUNNING:
                IS_RUNNING = True
                def _worker():
                    global IS_RUNNING, RUNNING_LOGS
                    mode_label = "【🧪 測試模式 (忽略防重，無新資料自動回退)】" if is_test_mode else "【⚡ 標準模式】"
                    RUNNING_LOGS.append(f"[{get_taipei_now().strftime('%H:%M:%S')}] 🚀 開始執行 QuietRadar 管線 {mode_label}...")
                    cmd = [sys.executable, "pipeline.py"]
                    if is_test_mode:
                        cmd.extend(["--test", "--force"])
                    proc = subprocess.Popen(
                        cmd,
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
                    RUNNING_LOGS.append(f"[{get_taipei_now().strftime('%H:%M:%S')}] 🏁 執行完成，Exit Code: {proc.returncode}")
                    IS_RUNNING = False

                threading.Thread(target=_worker, daemon=True).start()
            self._send_json({"status": "started"})

        elif path == "/api/test_bark":
            try:
                from pipeline import SimpleBarkNotifier, format_newsletter_markdown
                notifier = SimpleBarkNotifier()
                if not notifier.device_keys:
                    self._send_json({"status": "warning", "message": "⚠️ 尚未在 .env 中設定有效的 BARK_DEVICE_KEY！"})
                else:
                    with open("sources.yaml", "r", encoding="utf-8") as f:
                        cfg = yaml.safe_load(f)
                    cur_tpl = cfg.get("output_template", {})

                    if os.path.exists("latest_distilled.json"):
                        with open("latest_distilled.json", "r", encoding="utf-8") as df:
                            d_data = json.load(df)
                        items = d_data.get("items", [])
                        overview = d_data.get("overview", "")
                        # 依據當前最新 sources.yaml 中的 output_template 即時重新格式化！
                        fresh_content = format_newsletter_markdown(items, template=cur_tpl, overview=overview)
                        with open("latest_newsletter.md", "w", encoding="utf-8") as f:
                            f.write(fresh_content)
                        success = notifier.send_digest(items, template=cur_tpl, overview=overview, full_markdown=fresh_content)
                    elif os.path.exists("latest_newsletter.md"):
                        with open("latest_newsletter.md", "r", encoding="utf-8") as f:
                            content = f.read()
                        count_match = re.search(r'本期精選：(\d+)\s*則', content)
                        item_count = int(count_match.group(1)) if count_match else 1
                        dummy_items = [{"title": "newsletter", "original_url": "", "source_name": ""}] * item_count
                        success = notifier.send_digest(dummy_items, template=cur_tpl, full_markdown=content)
                    else:
                        self._send_json({"status": "warning", "message": "⚠️ 尚未生成 latest_newsletter.md 電子報檔案，請先執行一次雷達或點擊「測試 OUTPUT」！"})
                        return

                    if success:
                        self._send_json({"status": "ok", "message": "📱 已套用最新自訂版型並成功推播至您的 Bark 手機！"})
                    else:
                        self._send_json({"status": "warning", "message": "❌ Bark 推播失敗，請檢查伺服器或 Key 設定。"})
            except Exception as e:
                self._send_json({"status": "error", "message": f"測試異常: {str(e)}"}, status=500)

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
