# AgentPulse 部署与使用手册

AgentPulse 是一个多 Agent 的论文检索与汇总系统：输入研究主题，自动从多个公开学术数据源检索论文，完成去重、打分、精读、归纳、评审，输出中文 HTML/Markdown 报告，并支持对本次结果做带引用的问答。

本文面向第一次接触项目的人，覆盖：环境准备 → 安装 → 配置 → 启动 → 使用 → 部署到服务器 → 外网访问 → 运维排障。

---

## 1. 项目概览

### 1.1 功能

- **多主题串联检索**：支持一次输入 1–5 个研究主题，按第一、第二……顺序依次检索并合并结果
- **学科门类**：12 个一级门类、87 个二级学科、199 个三级学科，对齐 Google Scholar Metrics 分类
- **12 个合法公开数据源**：arXiv、OpenReview、OpenAlex、Crossref、Europe PMC、PubMed、Semantic Scholar、DOAJ、bioRxiv、HAL、Zenodo、Hacker News
- **期刊/会议精确检索**：`--venue Nature`、`--venue CVPR`；也支持只看 IEEE（DOI 前缀 `10.1109`）
- **召回评分**：关键词 38%、新鲜度 25%、期刊影响力 17%、引用 12%、热度 8%；无影响因子时该维度不参与并归一化其余维度
- **报告**：中文 HTML + Markdown，含主题归纳、趋势、研究空白、逐篇精读与引用
- **论文问答（RAG）**：针对本轮结果提问，回答附带可点击的论文引用
- **双模式**：配置模型 API 走生成式分析；不配置则全链路规则降级，功能完整可用
- **Web 配置页**：模型 API（Key / Base URL / 模型名 / 连通性测试）、NCBI Key、联系邮箱

### 1.2 Agent 链路

```
Planner  规划子主题与检索式
   ↓
Search   多源并发抓取（信号量 4）
   ↓
Curator  去重 + 召回打分 + 排序
   ↓
Analyst  逐篇精读（问题/方法/贡献/局限/可复用）
   ↓
Synthesizer 主题、趋势、研究空白归纳
   ↓
Critic   评审（模型分 60% + 规则分 40%，≥65 通过）
   ↓
Renderer 生成 HTML / Markdown 报告
```

模型调用者为 Planner、Analyst、Synthesizer、Critic 四个角色；Curator、抓取、打分与渲染不调用模型。未配置 `CHAT_API_KEY` 时全部走规则路径。

### 1.3 运行形态

| 项目 | 说明 |
|---|---|
| Python | 3.12+（开发环境 3.13） |
| 依赖 | FastAPI + Uvicorn + httpx + Pydantic，无数据库、无 Node.js |
| 进程 | **必须单进程**（任务状态在内存中，`--workers 2` 会导致进度查不到） |
| 端口 | 默认 8123 |
| 磁盘 | 约 300 MB（依赖 + 报告）+ SQLite 任务历史库 |
| 内存 | 常驻约 20–30 MB，单轮全源抓取峰值约 70 MB |

### 1.4 任务历史持久化

任务元数据与事件流持久化到 SQLite 单文件（`data/agentpulse.db`），重启服务后历史任务、统计信息、报告链接、事件回放均可恢复。报告文件仍在 `reports/` 目录。

- 默认路径 `data/agentpulse.db`，可通过 `DB_PATH` 环境变量修改
- 历史上限默认 500 条，通过 `MAX_HISTORY` 控制
- 重启时若 DB 中有 `queued`/`running` 状态的任务，会自动标记为 `failed`（进程重启无法恢复执行中的任务）
- 清理报告时会同步删除对应 DB 记录

### 1.5 多用户鉴权

内置基于 JWT 的多用户登录体系，无需额外依赖（JWT 与密码哈希均用 Python 标准库实现）。

- **用户表**：`users`（SQLite），字段含用户名、密码哈希（PBKDF2-HMAC-SHA256，10 万轮 + 随机盐）、角色
- **角色**：`admin`（管理员）/ `user`（普通用户）
- **默认账号**：首次启动自动创建 `admin` / `admin123`，请登录后立即修改密码
- **令牌**：JWT（HS256），存于浏览器 `localStorage`，通过 `Authorization: Bearer <token>` 头传递
- **权限**：
  - 所有 `/api/runs/*`、`/api/subfields`、`/api/topics`、`/api/sources` 需登录
  - `/api/settings/*`、`/api/cleanup`、`/api/users/*` 仅管理员
  - 普通用户只能看到自己的任务历史；管理员可看全部
- **生产环境**：务必设置 `AUTH_SECRET` 环境变量（随机长字符串），否则重启后所有令牌失效

---

## 2. 快速开始

### 2.1 获取代码

```bash
git clone <仓库地址> AgentPulse
cd AgentPulse
```

若拿到的是压缩包，解压后进入 `AgentPulse` 目录即可。以下内容不要从旧机器拷贝：`.venv/`、`__pycache__/`、`reports/`（可选迁移）、`data/settings.json`（可选迁移）。

### 2.2 创建虚拟环境并安装

**Windows（PowerShell）**

```powershell
py -3.13 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -U pip
.\.venv\Scripts\python.exe -m pip install -e .
```

**Linux / macOS**

```bash
python3.13 -m venv .venv        # 或 python3.12
./.venv/bin/python -m pip install -U pip
./.venv/bin/python -m pip install -e .
```

国内网络可加镜像：

```bash
pip install -e . -i https://pypi.tuna.tsinghua.edu.cn/simple
```

安装后会生成 `agentpulse` 命令；也可始终用 `python -m agentpulse` 调用。

> 如果看到 `No module named agentpulse`，说明没有执行 `pip install -e .`。临时替代方案是先设置 `PYTHONPATH=src`（PowerShell：`$env:PYTHONPATH="src"`；Bash：`export PYTHONPATH=src`）。

### 2.3 最小配置

复制示例文件并按需填写：

```bash
cp .env.example .env
```

```ini
# 模型（可留空，留空即规则模式）
CHAT_API_KEY=
CHAT_BASE_URL=https://api.deepseek.com/v1
CHAT_MODEL=deepseek-v4-flash

# 建议填写：开放 API 礼貌池标识，限流更宽松
CONTACT_EMAIL=you@example.com
```

### 2.4 先跑一次命令行任务

```powershell
.\.venv\Scripts\python.exe -m agentpulse run -t "large language model" --days 30 --max-papers 12 --sources arxiv --no-llm
```

```bash
./.venv/bin/python -m agentpulse run -t "large language model" --days 30 --max-papers 12 --sources arxiv --no-llm
```

成功输出：

```
报告：reports\20261115-134507-large-language-model\report.md
HTML：reports\20261115-134507-large-language-model\report.html
```

### 2.5 启动 Web 面板

```bash
python -m agentpulse serve --port 8123
```

浏览器打开 http://127.0.0.1:8123 。

---

## 3. 配置说明

### 3.1 优先级

```
进程环境变量  >  data/settings.json（网页保存）  >  .env
```

- 某字段由环境变量提供时，网页配置页对应项为**只读**，需改环境变量并重启
- 网页保存的值写入 `data/settings.json`，下一轮任务生效

### 3.2 环境变量

| 变量 | 作用 | 默认值 |
|---|---|---|
| `CHAT_API_KEY` | 模型密钥；为空则规则模式 | 空 |
| `CHAT_BASE_URL` | OpenAI 兼容接口地址 | `https://api.deepseek.com/v1` |
| `CHAT_MODEL` | 模型名，必须是上游真实模型 | `deepseek-v4-flash` |
| `PER_SOURCE_LIMIT` | 单源召回上限 | 20 |
| `MAX_PAPERS` | 精选篇数上限 | 40 |
| `DEFAULT_DAYS` | 默认回溯窗口（天） | 14 |
| `CONTACT_EMAIL` | OpenAlex / Crossref 礼貌池标识 | 空 |
| `NCBI_API_KEY` | PubMed 配额从 3 次/秒提升到 10 次/秒 | 空 |
| `SEMANTIC_SCHOLAR_API_KEY` | Semantic Scholar 配额提升 | 空 |
| `MAX_CONCURRENCY` | 抓取并发数 | 4 |
| `TOKEN_BUDGET` | 单轮模型 token 预算 | 120000 |
| `REQUEST_TIMEOUT` | HTTP 超时（秒） | 30 |
| `LLM_TIMEOUT` | 模型超时（秒） | 120 |
| `HTTP_RETRIES` | HTTP 重试次数 | 3 |
| `DB_PATH` | SQLite 任务历史库路径 | `data/agentpulse.db` |
| `MAX_HISTORY` | 任务列表返回上限 | 500 |
| `AUTH_SECRET` | JWT 签名密钥（生产必填） | 空 → 进程随机生成 |
| `TOKEN_EXPIRE_DAYS` | 令牌有效期（天） | 7 |

### 3.3 网页配置页

右上角「系统配置」：

1. **模型 API**：填 Key、接口地址、模型名 → 保存 → 点「测试模型连接」确认
2. **论文数据源**：NCBI API Key、联系邮箱
3. 密钥在页面上只显示掩码 `****abcd`，保存后不回显明文

模型不可用时，页面与报告会标注「规则模式」，链路仍完整。

---

## 4. 使用说明

### 4.1 发起检索

1. 选择一级学科门类 → 二级学科 →（可选）三级学科，主题推荐会自动切换
2. 填写 1–5 个研究主题，后 4 个可为空；按填写顺序依次检索
3. 选择数据源（默认全选）、回溯窗口（7 天 / 180 天 / 3 年）
4. 可选：指定期刊/会议，或勾选「仅 IEEE」
5. 点「开始检索」，页面通过 SSE 实时显示各 Agent 进度

### 4.2 查看结果

- 报告区：HTML 报告（可在新窗口打开）
- 论文清单：标题、作者、来源、发表日期、引用数、召回分，日期已做多源校正，不会出现未来日期
- 运行日志：每个阶段的事件，失败时用于定位

### 4.3 论文问答（RAG）

报告下方「论文问答」：

- 针对**本轮结果**提问，例如「这些论文的主要方法有啥差异？」
- 回答分两种模式：
  - **模型模式**：先做中英关键词扩展检索，再由模型基于命中片段作答，附带引用
  - **规则模式**：按问题意图（方法 / 贡献 / 局限 / 实验）逐篇列出精读结论，不做语义归纳
- 引用点击可跳转论文页面；正文提到哪几篇，就引用哪几篇

> 问答依赖本轮结果在内存中的状态：重启服务或换一轮任务后，需重新提问。

### 4.4 命令行参数

```bash
python -m agentpulse run \
  -t "多智能体协作" -t "LLM agent safety" \
  --days 180 \
  --max-papers 20 \
  --sources arxiv,openalex,crossref \
  --field 计算机科学 \
  --subfield "Artificial Intelligence" \
  --venue CVPR \
  --ieee \
  --no-llm
```

| 参数 | 说明 |
|---|---|
| `-t, --topic` | 检索主题，可重复传入，按顺序检索 |
| `--days` | 回溯窗口天数 |
| `--max-papers` | 精选论文上限 |
| `--sources` | 逗号分隔数据源：`arxiv,openreview,openalex,crossref,europepmc,pubmed,semantic_scholar,hackernews,doaj,biorxiv,hal,zenodo` |
| `--field` / `--subfield` / `--subfield3` | 学科门类 / 二级 / 三级限定 |
| `--venue` | 只保留指定期刊或会议 |
| `--ieee` | 只保留 IEEE 出版物（DOI `10.1109`） |
| `--offline` | 使用内置演示语料，不访问网络（仅调试用） |
| `--no-llm` | 强制规则模式 |
| `--from-json` | 跳过抓取，直接读取候选论文 JSON |
| `--out` | 报告输出根目录，默认 `./reports` |
| `--list-subfields` | 列出该门类的二级、三级学科与热点主题 |

### 4.5 清理历史报告

每轮任务会在 `reports/` 下生成一个目录（`plan/candidates/analyses/synthesis/critique/events/report`），跑多了会占用磁盘。用 `clean` 子命令按数量或天数清理：

```bash
# 预演：只统计，不删除
python -m agentpulse clean --dry-run

# 只保留最近 20 轮（默认值）
python -m agentpulse clean

# 只保留最近 5 轮
python -m agentpulse clean --keep 5

# 额外删除超过 30 天的报告（即使还在最近 5 轮内）
python -m agentpulse clean --keep 5 --days 30

# 清空全部报告
python -m agentpulse clean --keep 0

# 自定义报告目录
python -m agentpulse clean --out D:\reports --keep 10
```

| 参数 | 说明 |
|---|---|
| `--out` | 报告根目录，默认 `./reports` |
| `--keep` | 保留最近 N 轮，默认 20，`0` 表示全删 |
| `--days` | 额外删除超过 N 天的报告 |
| `--dry-run` | 只统计不删除 |

安全约束：只删除 `reports/` 下符合任务产物特征的子目录（含 `report.md` / `report.html` / `candidates.json`），不会触碰根目录本身或用户自己放的其它目录。

页面右上角「清理记录」按钮等价于 `clean --keep 20`：二次确认后删除更早的历史报告，结果显示在「运行日志」中。执行中的任务目录会被自动跳过。

清理机制：

- **识别范围**：只扫描报告根目录下的一级子目录，且目录内必须含 `report.md` / `report.html` / `candidates.json` 之一，才算一轮任务产物
- **排序**：按目录修改时间从新到旧，最新的在前
- **保留规则**：前 `keep` 轮保留；给了 `days` 时，即使在前 `keep` 轮内，超过该天数的也删除
- **执行中的任务**：Web 端调用时会排除当前 `running` 状态任务的产物目录，避免删掉正在写入的报告
- **入口**：命令行 `agentpulse clean`、页面「清理记录」按钮、接口 `POST /api/cleanup`，三者共用同一套逻辑
- **可逆性**：删除为永久删除（不进回收站），建议先 `--dry-run` 或在低峰期执行

### 4.6 主要接口

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/api/health` | 健康检查，返回是否启用模型 |
| GET | `/api/subfields?field=` | 二级学科；带 `parent=` 时返回三级学科 |
| GET | `/api/topics?field=&subfield=&subfield3=` | 推荐主题 |
| GET | `/api/sources` | 可用数据源 |
| POST | `/api/runs` | 创建任务 |
| GET | `/api/runs/{id}` | 任务状态 |
| GET | `/api/runs/{id}/events` | SSE 事件流 |
| GET | `/api/runs/{id}/report` | HTML 报告 |
| POST | `/api/runs/{id}/ask` | 论文问答，body：`{"question": "..."}` |
| GET/PUT | `/api/settings` | 读取 / 保存配置 |
| POST | `/api/settings/test` | 测试 NCBI Key |
| POST | `/api/settings/test-model` | 测试模型连通性 |
| POST | `/api/cleanup` | 清理历史报告，body：`{"keep": 20, "days": null, "dry_run": false}` |

---

## 5. 部署到其他机器

### 5.1 迁移清单

保留：`src/`、`web/`、`tests/`、`pyproject.toml`、`requirements.txt`、`.env.example`
可选迁移：`reports/`（历史报告）、`data/settings.json`（已保存配置）
不要迁移：`.venv/`、`__pycache__/`、`.bin/`

Windows：

```powershell
robocopy "D:\AgentPulse" "E:\AgentPulse" /E /XD .venv reports __pycache__ .bin .git
```

Linux / macOS：

```bash
rsync -av --exclude '.venv' --exclude 'reports' --exclude '__pycache__' --exclude '.bin' \
  ./AgentPulse/ user@new-host:/opt/AgentPulse/
```

然后在目标机重复第 2 章的安装与配置步骤。

### 5.2 局域网访问

```bash
python -m agentpulse serve --host 0.0.0.0 --port 8123
```

- Windows（管理员）：`New-NetFirewallRule -DisplayName "AgentPulse 8123" -Direction Inbound -LocalPort 8123 -Protocol TCP -Action Allow`
- Linux：`sudo ufw allow 8123/tcp`

访问 `http://<目标机IP>:8123`。

### 5.3 反向代理与 HTTPS

生产环境建议 Nginx 或 Caddy 反代。**SSE 必须关闭缓冲**，否则进度条不动。

Nginx：

```nginx
server {
    listen 80;
    server_name pulse.example.com;

    location / {
        proxy_pass http://127.0.0.1:8123;
        proxy_http_version 1.1;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;

        proxy_buffering off;
        proxy_cache off;
        proxy_read_timeout 3600s;
        proxy_send_timeout 3600s;
    }
}
```

```bash
sudo certbot --nginx -d pulse.example.com
```

Caddy（自动 HTTPS）：

```caddyfile
pulse.example.com {
    reverse_proxy 127.0.0.1:8123 {
        flush_interval -1
    }
}
```

应用内置了多用户 JWT 登录（见 1.5 节），对外暴露时建议同时加一层 Nginx Basic Auth 作为额外防护：

```bash
sudo htpasswd -c /etc/nginx/.htpasswd youruser
```

```nginx
location / {
    auth_basic "AgentPulse";
    auth_basic_user_file /etc/nginx/.htpasswd;
    proxy_pass http://127.0.0.1:8123;
}
```

### 5.4 临时公网地址（演示用）

```bash
ssh -o ServerAliveInterval=30 -R 80:127.0.0.1:8123 nokey@localhost.run
```

成功后会输出 `https://xxxx.app.lhr.life`。关闭 SSH 即失效，重连后域名会变化。

> 转发目标必须写 `127.0.0.1`，写成 `localhost` 可能解析到 IPv6 导致 502。

### 5.5 固定域名（Cloudflare Tunnel）

```bash
cloudflared tunnel login
cloudflared tunnel create agentpulse
```

`~/.cloudflared/config.yml`（Windows：`C:\Users\<用户名>\.cloudflared\config.yml`）：

```yaml
tunnel: <隧道UUID>
credentials-file: <凭据json绝对路径>
ingress:
  - hostname: pulse.example.com
    service: http://127.0.0.1:8123
  - service: http_status:404
```

```bash
cloudflared tunnel route dns agentpulse pulse.example.com
cloudflared tunnel run agentpulse
```

---

## 6. 开机自启

### Windows：NSSM

```powershell
nssm install AgentPulse "E:\AgentPulse\.venv\Scripts\python.exe" "-m agentpulse serve --port 8123"
nssm set AgentPulse AppDirectory "E:\AgentPulse"
nssm set AgentPulse AppEnvironmentExtra PYTHONIOENCODING=utf-8
nssm set AgentPulse Start SERVICE_AUTO_START
nssm start AgentPulse
```

### Linux：systemd

`/etc/systemd/system/agentpulse.service`：

```ini
[Unit]
Description=AgentPulse
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=agentpulse
WorkingDirectory=/opt/AgentPulse
EnvironmentFile=/opt/AgentPulse/.env
Environment=PYTHONIOENCODING=utf-8
ExecStart=/opt/AgentPulse/.venv/bin/python -m agentpulse serve --port 8123
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
```

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now agentpulse
sudo systemctl status agentpulse
journalctl -u agentpulse -f
```

> `EnvironmentFile` 里不要写 `export`，也不要给值加引号。

### macOS

```bash
nohup ./.venv/bin/python -m agentpulse serve --port 8123 > agentpulse.log 2>&1 &
```

---

## 7. 目录与数据

```
AgentPulse/
├─ src/agentpulse/          # 后端：编排、抓取、评分、Agent、Web
│  ├─ orchestrator.py       # 流水线与 Agent 编排
│  ├─ sources.py            # 12 个数据源
│  ├─ taxonomy.py           # 12/87/199 学科体系
│  ├─ venues.py metrics.py  # 会议/期刊识别与影响力
│  ├─ scoring.py            # 召回评分
│  ├─ qa.py                 # 论文问答（RAG）
│  └─ web.py cli.py         # 入口
├─ web/                     # 前端单页：index.html / styles.css / app.js / settings.js
├─ tests/test_core.py       # 单元测试
├─ data/
│  ├─ settings.json       # 网页保存的配置（自动生成）
│  └─ agentpulse.db       # 任务历史与事件流（SQLite，自动生成）
├─ reports/<时间>-<主题>/   # 每轮产物
│  ├─ plan.json candidates.json analyses.json synthesis.json critique.json
│  ├─ events.jsonl report.md report.html
├─ .env                     # 本地配置（不入库）
└─ pyproject.toml
```

升级时保留 `data/`（含 `settings.json` 与 `agentpulse.db`）与 `reports/`，其余目录整份覆盖即可。

> 备份建议：定期备份 `data/agentpulse.db`（任务历史）与 `reports/`（报告产物）。`agentpulse.db` 是 SQLite 单文件，可直接 `cp` 或 `sqlite3 .backup`。

---

## 8. 运维与排障

### 8.1 并发与性能

- `MAX_CONCURRENCY`（默认 4）：单个任务内的并发抓取数，同时也是精读批处理与影响力查询的并发上限。网络状况好可调到 6–8；频繁遇到 429 则降到 2–3
- 各数据源并发抓取，单个源失败只降级不阻塞；Semantic Scholar 单独做 1 rps 串行，不占用其它源的并发槽位
- 期刊影响力查询结果在进程内缓存 6 小时，多主题任务与重复运行不会重复请求 OpenAlex
- 报告渲染与 JSON 落盘在线程池执行，生成长报告时不会阻塞 SSE 进度推送
- HTTP 连接池大小随 `MAX_CONCURRENCY` 自动放大（`max_concurrency × 4`）
- 必须单进程运行：任务状态与缓存都在内存中，多 worker 会导致进度查不到、缓存失效

| 现象 | 原因与处理 |
|---|---|
| 端口被占用 | `netstat -ano \| findstr :8123` 找 PID → `taskkill /PID <pid> /F` |
| `No module named agentpulse` | 未执行 `pip install -e .`；或临时设置 `PYTHONPATH=src` |
| 页面打开但无报告 | 任务失败或仍在运行，查看运行日志与 `reports/<run>/events.jsonl` |
| 检索结果为 0 | 窗口太窄、学科限定过细、数据源全关；先放宽窗口或改用全部门类 |
| 指定会议检索为 0 | 会议别名/年份写法不匹配时直接报错，换全称或去掉年份重试 |
| 大量 429 / 超时 | 填 `CONTACT_EMAIL`、配置 `NCBI_API_KEY`、降低 `MAX_CONCURRENCY` |
| SSE 进度不动 | 反代开启了缓冲：`proxy_buffering off`，`proxy_read_timeout` 调大 |
| 公网 502 | 隧道目标写成 `localhost`，改为 `127.0.0.1` |
| 配置页字段只读 | 该字段由环境变量提供，改 `.env` 或系统环境变量后重启 |
| 页面显示规则模式 | 未配置 `CHAT_API_KEY`，或模型测试失败，属于预期降级 |
| 中文乱码 | Windows 控制台设置 `PYTHONIOENCODING=utf-8` |
| 内存持续增长 | 任务历史最多保留 12 条，重启服务可释放；确认没有多 worker |
| 磁盘被报告占满 | `python -m agentpulse clean --keep 20`，先加 `--dry-run` 预演 |

---

## 9. 自检与验收

```powershell
$env:PYTHONPATH="src"; $env:PYTHONIOENCODING="utf-8"
.\.venv\Scripts\python.exe -m unittest discover -s tests -t .
```

```bash
PYTHONPATH=src ./.venv/bin/python -m unittest discover -s tests -t .
```

部署验收清单：

- [ ] `python -m agentpulse run -t "..." --no-llm` 能生成 `report.md` / `report.html`
- [ ] `serve` 启动后首页与 `/api/health` 返回 200
- [ ] 门类 → 二级 → 三级级联正常，推荐主题自动切换
- [ ] 1–5 个主题按顺序检索，结果合并
- [ ] 12 个数据源可单独开关，失败源不影响其他源
- [ ] 指定期刊/会议与「仅 IEEE」过滤生效
- [ ] 论文日期落在窗口内，无未来日期
- [ ] 配置页可保存模型 API 并通过「测试模型连接」
- [ ] 报告下方论文问答可返回答案与引用
- [ ] 如对外暴露，已配置 HTTPS 与访问鉴权
