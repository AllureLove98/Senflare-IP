# Senflare-IP（Docker 版）

Cloudflare 优选 IP 采集器 —— 自动收集、测速、评分并输出可用 IP 列表。

> 📦 本仓库为 Docker 部署版本，基于原作者 [Senflare](https://github.com/Senflare/Senflare-IP) 的开源项目 **IP Test - Cloudflare优选IP采集器** 改造而来，在其基础上提供容器化部署、定时循环运行、结果自动推送 GitHub 等能力。

- **原作者**：[Senflare](https://github.com/Senflare/Senflare-IP)（IP Test - Cloudflare优选IP采集器）
- **现维护者**：[AllureLove98](https://github.com/AllureLove98)（Docker 化改造与维护）
- 流程：采集多个公开 IP 源 + 可选 Cloudflare 官方网段扫描 → TCP 连通性测试 → 地区识别 → 带宽测速 → 综合评分排序
- 支持**地区定向扫描**：按国家/地区筛选（如 HK/JP/US）
- 输出：`IPlist.txt`、`Senflare.txt`、`IPlist-Pro.txt`、`Senflare-Pro.txt`、`Ranking.txt`、`Region-{CODE}.txt`/`Region-All.txt`

---

## 🚀 开箱即用（Docker）

镜像已内置全部程序与默认配置，无需手动往容器里放任何文件：

```bash
docker pull allurelove98/senflare-ip:latest
docker run -d \
  --name senflare-ip \
  --restart unless-stopped \
  -e TZ=Asia/Shanghai \
  allurelove98/senflare-ip:latest
```

### docker-compose（推荐）

```yaml
version: "3.9"
services:
  senflare-ip:
    image: allurelove98/senflare-ip:latest
    container_name: senflare-ip
    restart: unless-stopped
    working_dir: /app
    volumes:
      # - ./config.json:/app/config.json:ro  # 可选：自定义配置
      # - ./output:/app/output                # 可选：结果同步到宿主机
    environment:
      TZ: Asia/Shanghai
      RUN_INTERVAL_SECONDS: "10800" # 运行间隔（秒）
      GIT_PUSH_ENABLED: "false"     # 是否推送结果到 GitHub
      GITHUB_TOKEN: ""
      GITHUB_REPOSITORY: "AllureLove98/Senflare-IP"
      HTTP_PROXY: "" # 代理（如 Clash）
      HTTPS_PROXY: ""
      ALL_PROXY: ""
```

---

## ⚙️ 配置文件

程序启动时自动加载 `config.json`（不存在则使用内置默认配置，开箱即用）。

- 本地运行：`cp config.example.json config.json` 后修改
- Docker 运行：挂载 `./config.json:/app/config.json:ro`
- 也可用环境变量 `CONFIG_FILE` 指定其他路径

config 文件保持干净不写注释，**每个配置项的详细说明见下方下拉菜单**：

<details>
<summary><b>📖 配置项说明（点击展开）</b></summary>

### 顶层配置项

| 配置项 | 必填 | 默认 | 说明 |
| --- | --- | --- | --- |
| `ip_sources` | ✅ | 16 个源 | IP 采集源列表（数组，至少 1 个）。程序并发请求所有源并提取 IPv4 地址，每个源的响应单独记录状态码/字节数/提取数量（DEBUG 级别）。失效的源直接从数组里删除即可。 |
| `cidr_scan_enabled` | 可选 | true | IP 段扫描总开关：true=启用段扫描（需配置 ips_sources）；false=关闭，候选池仅来自 ip_sources。 |
| `ips_sources` | 可选 | 1 个源 | CIDR 网段源（数组，留空 [] 不扫描）。程序从每个网段随机采样 `cidr_ips_per_segment` 个 IP 加入候选池，用于发现公开列表未覆盖的优选 IP。推荐 https://www.cloudflare.com/ips-v4。 |
| `cidr_ips_per_segment` | 可选 | 10 | 每个网段随机采样的 IP 数（自动限制 1-200）。建议 5-20，过大显著拖慢快速筛选阶段。 |
| `region_targets` | 可选 | [] | 地区定向扫描目标（数组，如 `["HK","JP","US"]`，空数组=关闭）。常用代码见 [常见国家地区参考表.md](常见国家地区参考表.md)。 |
| `region_target_count` | 可选 | 10 | 每个目标地区需要的有效节点数（延迟达标且带宽达标，不是候选数；凑不够自动补采）。 |
| `region_valid_max_delay` | 可选 | 300 | 有效节点延迟上限（ms），低于此值才算有效。 |
| `region_valid_min_bandwidth` | 可选 | 5 | 有效节点带宽下限（Mbps），高于此值才算有效。 |
| `region_scan_per_segment` | 可选 | 50 | 补采时每个网段采样的数量（上限 200）。 |
| `region_max_rounds` | 可选 | 3 | 每个地区最大补采轮数，每轮从网段重新采样一批。 |
| `test_ports` | ✅ | 11 个端口 | TCP 连接测试端口：443=HTTPS 标准端口；2052~8444=Cloudflare 专用端口。逐个端口 connect 取最小延迟，端口越多越全面但越慢。 |
| `timeout` | ✅ | 15 | 采集阶段单个源的请求超时（秒）。 |
| `api_timeout` | ✅ | 5 | 地区识别 API（ipinfo.io / ip-api.com）查询超时（秒）。 |
| `query_interval` | ✅ | 0.5 | 采集源之间的请求间隔（秒），过快可能被源限流。 |
| `max_workers` | ✅ | 15 | 快速筛选 + TCP Ping 阶段最大并发线程数。越大越快但占用 CPU/带宽越高。 |
| `batch_size` | ✅ | 30 | 并发处理的批次大小。 |
| `cache_ttl_hours` | ✅ | 168 | 地区信息缓存有效期（小时），到期后重新查询，缓存保存在 Cache.json。 |
| `quick_filter_ports` | ✅ | [443] | 快速筛选阶段测试端口（只测这些端口快速剔除不可用 IP）。 |
| `region_workers` | ✅ | 10 | 地区识别并发线程数。 |
| `bandwidth_workers` | ✅ | 5 | 带宽测试并发线程数（每个测速请求占满单连接带宽，建议 ≤5）。 |
| `advanced_mode` | ✅ | true | 高级模式：true=延迟排名筛选→TCP Ping→带宽测试→生成 Pro/Ranking 文件；false=只生成基础版 IPlist.txt/Senflare.txt。 |
| `bandwidth_test_count` | ✅ | 3 | 每个 IP 的带宽测速次数，取最高值；单 IP 总超时 15 秒，速度 >100Mbps 提前结束。 |
| `bandwidth_test_size_mb` | ✅ | 50 | 带宽测试下载文件大小（MB），越大越准但越慢；下载超时 5 秒，按已下载量折算。 |
| `rate_limit_pause_seconds` | ✅ | 60 | 测速遇 HTTP 429（限流）自动暂停秒数，暂停后自动恢复。 |
| `bandwidth_retry_rounds` | ✅ | 2 | 因 429 限流失败的 IP 自动重试轮数，0=不重试。 |
| `latency_filter_percentage` | ✅ | 40 | 延迟排名筛选：按 TCP 延迟排序后只保留前 N% 进入带宽测试，100=全部测试。 |
| `save_filter_mode` | ✅ | - | 保存过滤模式（只能是 1 或 2，其他值启动报错退出）：1=按延迟过滤；2=按速度过滤。运行结束后对全部输出文件统一执行「读取→过滤→覆写」。 |
| `delay_threshold` | 可选 | 200 | 延迟过滤阈值（ms）。仅 `save_filter_mode=1` 生效：延迟低于此值舍弃。 |
| `speed_threshold` | 可选 | 50 | 速度过滤阈值（Mbps）。仅 `save_filter_mode=2` 生效：速度低于此值舍弃。 |
| `use_proxy_for_collection` | ✅ | true | 是否只在采集 IP 阶段走代理（env 的 HTTP_PROXY/HTTPS_PROXY/ALL_PROXY）；带宽测试/TCP 测试永远直连。 |

### env 区块（运行参数/机密，环境变量优先）

已设置的容器环境变量 > 此处配置（compose 里写死的环境变量优先，此处用于兜底）。`GITHUB_TOKEN` 等敏感信息建议用 compose environment 或 .env 注入，避免提交到仓库。

| 配置项 | 默认 | 说明 |
| --- | --- | --- |
| `LOG_LEVEL` | INFO | 日志级别：DEBUG=输出逐 IP/逐端口/逐请求明细；INFO=只显示汇总；WARNING=仅警告+错误；ERROR=仅错误。注意：compose environment 写死了 LOG_LEVEL（哪怕空串）会覆盖此处。 |
| `RUN_INTERVAL_SECONDS` | 10800 | 两次运行之间的间隔（秒），由 entrypoint.sh 的循环调度使用。 |
| `GIT_PUSH_ENABLED` | false | 是否把运行结果推送到 GitHub 的 results 分支。 |
| `GIT_RESULT_BRANCH` | results | 结果推送目标分支名（与 main 代码分支分离）。 |
| `GITHUB_TOKEN` | "" | GitHub 访问令牌（repo 权限），留空则跳过推送。强烈建议通过 compose environment 注入。 |
| `GITHUB_REPOSITORY` | AllureLove98/Senflare-IP | 目标仓库（所有者/仓库名）。 |
| `GIT_USER_NAME` | GitHub Action | 结果提交使用的 git 用户名。 |
| `GIT_USER_EMAIL` | action@github.com | 结果提交使用的 git 邮箱。 |
| `COMMIT_MESSAGE` | Update IP results | 结果提交的 commit message 前缀，自动追加 UTC 时间戳。 |
| `HTTP_PROXY`/`HTTPS_PROXY`/`ALL_PROXY` | "" | 代理地址（如 http://Clash:xxx@10.0.0.3:7893），留空=直连。仅采集 IP 阶段使用。 |
| `NO_PROXY` | localhost,127.0.0.1 | 不走代理的地址白名单，逗号分隔。 |

</details>

---

## 🗺️ 地区定向扫描（可选）

参考 [XIU2/CloudflareSpeedTest](https://github.com/XIU2/CloudflareSpeedTest) 思路（其本身不支持按国家筛选），程序内置**按国家/地区筛选**的整合实现：

- 配置 `region_targets` 选择目标地区（如 `["HK","JP","US"]`），程序从 Cloudflare 官方网段（`ips_sources`）采样并深度测速
- `region_target_count` 指定每个地区要多少个**有效节点**——"有效"由 `region_valid_max_delay`（延迟上限）和 `region_valid_min_bandwidth`（带宽下限）定义
- 候选不足会自动**补采**（`region_scan_per_segment` / `region_max_rounds`），保证输出的是测速后仍达标的节点
- 输出：每个地区一个纯 IP 文件 `Region-{CODE}.txt`（如 `Region-HK.txt`）+ 汇总文件 `Region-All.txt`（含测速 Mbps，速度快的排前面）
- 全部国家/地区代码见 [常见国家地区参考表.md](常见国家地区参考表.md)

## 📤 结果推送机制

结果**不推送到 `main` 代码分支**，而是推送到独立的 `results` 分支（可用 `GIT_RESULT_BRANCH` 修改）：

```
GitHub 仓库
├── main 分支     → 代码（IPtest.py、Dockerfile 等）
└── results 分支  → 运行结果（IPlist.txt、Senflare.txt、Ranking.txt 等）
```

- 容器内 `entrypoint.sh` 在独立目录 `/app/results-repo` 维护 git，只提交输出文件，**不触碰代码**
- 每次推送前以远端 `results` 分支为基准 `reset --hard` 对齐，避免冲突；推送失败自动 `--force` 兜底
- 无需 GitHub Actions，容器自己按 `RUN_INTERVAL_SECONDS` 循环运行

---

## 🔄 Docker Hub 自动构建

GitHub Actions 会在推送 `main` 分支或打 `v*` tag 时自动构建并推送镜像（linux/amd64 + linux/arm64 双架构）。

**首次配置**：Docker Hub 创建 Access Token → GitHub 仓库 Settings → Secrets and variables → Actions，添加 `DOCKERHUB_USERNAME`、`DOCKERHUB_TOKEN`（可选 Variables 加 `DOCKERHUB_REPO`，默认 `allurelove98/senflare-ip`）。

| 触发 | Tag |
| --- | --- |
| push main | `latest` |
| push tag v1.0.0 | `1.0.0`、`v1.0.0`、`sha-xxxxx` |
| workflow_dispatch | 手动触发 |

---

## 🖥️ 本地运行

```bash
pip install -r requirements.txt
python IPtest.py
```

## 📁 项目结构

```
├── IPtest.py            # 主程序
├── config.example.json  # 配置模板
├── 常见国家地区参考表.md  # 地区代码对照表（region_targets 用）
├── requirements.txt
├── entrypoint.sh        # Docker 循环调度入口
├── Dockerfile
├── docker-compose.yaml
└── .github/workflows/docker-build.yml  # DockerHub 自动构建
```

## 📄 License

[MIT](LICENSE)

本项目由 [Senflare](https://github.com/Senflare/Senflare-IP) 的原作衍生而来，与原项目许可证一致（MIT），版权归原作者 Senflare 与现维护者 AllureLove98 共同所有。
