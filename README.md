# Senflare-IP（Docker 版）

Cloudflare 优选 IP 采集器 —— 自动收集、测速、评分并输出可用 IP 列表。

> 📦 **本仓库为 Docker 部署版本**，基于原作者 [Senflare](https://github.com/Senflare/Senflare-IP) 的开源项目 **IP Test - Cloudflare优选IP采集器** 改造而来，在其基础上提供容器化部署、定时循环运行、结果自动推送 GitHub 等能力。

- **原作者**：[Senflare](https://github.com/Senflare/Senflare-IP)（IP Test - Cloudflare优选IP采集器）
- **现维护者**：[AllureLove98](https://github.com/AllureLove98)（Docker 化改造与维护）
- 采集 16 个公开 IP 源 + 可选 Cloudflare 官方网段扫描 → TCP 连通性测试 → 地区识别 → 带宽测速 → 综合评分排序
- 支持**地区定向扫描**：按国家/地区筛选（如 HK/JP/US），确保每个地区凑够指定数量的有效节点（参考 [CloudflareSpeedTest](https://github.com/XIU2/CloudflareSpeedTest) 思路）
- 输出：`IPlist.txt`（基础可用 IP）、`Senflare.txt`（按地区）、`IPlist-Pro.txt`/`Senflare-Pro.txt`（进阶，含测速 Mbps）、`Ranking.txt`（评分排行）、`Region-{CODE}.txt`/`Region-All.txt`（地区定向）
- 支持代理采集、结果定时推送 GitHub、缓存复用

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
      # - ./config:/app/config   # 可选：自定义配置
      # - ./output:/app/output                 # 可选：结果同步到宿主机
    environment:
      TZ: Asia/Shanghai
      RUN_INTERVAL_SECONDS: "10800" # 运行间隔（秒）
      GIT_PUSH_ENABLED: "false" # 是否推送结果到 GitHub
      GITHUB_TOKEN: ""
      GITHUB_REPOSITORY: "AllureLove98/Senflare-IP"
      HTTP_PROXY: "" # 代理（如 Clash）
      HTTPS_PROXY: ""
      ALL_PROXY: ""
    networks:
      VBR-LAN2-vlan:
        ipv4_address: 10.0.240.1

networks:
  VBR-LAN2-vlan:
    external: true
```

> 网络可换成自己的 `external` 网络或直接删除 networks 段使用默认 bridge。

---

## ⚙️ 配置文件（可选）

程序启动时自动加载 `config.json`（不存在则使用内置默认配置，开箱即用）。

- 本地运行：复制 `config.example.json` 为 `config.json` 后修改
- Docker 运行：挂载 `./config.json:/app/config.json:ro`
- 也可用环境变量 `CONFIG_FILE` 指定其他路径

```bash
cp config.example.json config.json
```

| 配置项                       | 默认      | 说明                                      |
| ---------------------------- | --------- | ----------------------------------------- |
| `ip_sources`                 | 16 个源   | 采集的公开 IP 列表地址                    |
| `cidr_scan_enabled`          | true      | IP 段扫描总开关（可选）                   |
| `ips_sources`                | 1 个源    | CIDR 网段源（可选，扫描 CF 官方段）       |
| `cidr_ips_per_segment`       | 10        | 每网段采样 IP 数（可选）                  |
| `region_targets`             | []        | 地区定向扫描目标地区（可选，如 HK,JP,US） |
| `region_target_count`        | 10        | 每个地区需要的有效节点数                  |
| `region_valid_max_delay`     | 300       | 有效节点延迟上限（ms）                    |
| `region_valid_min_bandwidth` | 5         | 有效节点带宽下限（Mbps）                  |
| `region_scan_per_segment`    | 50        | 补采时每网段采样数（可选）                |
| `region_max_rounds`          | 3         | 最大补采轮数（可选）                      |
| `test_ports`                 | 11 个端口 | TCP 测试端口                              |
| `timeout`                    | 15        | 请求超时（秒）                            |
| `api_timeout`                | 5         | API 超时（秒）                            |
| `query_interval`             | 0.5       | 采集请求间隔                              |
| `max_workers`                | 15        | 采集并发                                  |
| `batch_size`                 | 30        | 批次大小                                  |
| `cache_ttl_hours`            | 168       | IP 缓存有效期（小时）                     |
| `quick_filter_ports`         | [443]     | 快速过滤端口                              |
| `region_workers`             | 10        | 地区识别并发                              |
| `bandwidth_workers`          | 5         | 测速并发                                  |
| `advanced_mode`              | true      | 进阶模式（Pro/Ranking）                   |
| `bandwidth_test_count`       | 3         | 测速次数                                  |
| `bandwidth_test_size_mb`     | 50        | 测速文件大小（MB）                        |
| `latency_filter_percentage`  | 40        | 延迟过滤比例（%）                         |
| `use_proxy_for_collection`   | true      | 采集是否走代理                            |

### 运行参数（`env` 区块）

`GITHUB_TOKEN` 等运行/推送参数通过 `env` 区块配置，也可直接用环境变量（环境变量优先）。

| 配置项                                 | 默认                     | 说明                                 |
| -------------------------------------- | ------------------------ | ------------------------------------ |
| `RUN_INTERVAL_SECONDS`                 | 10800                    | 运行间隔（秒）                       |
| `GIT_PUSH_ENABLED`                     | false                    | 是否推送结果到 GitHub                |
| `GIT_RESULT_BRANCH`                    | results                  | 结果推送到的分支（与代码 main 分离） |
| `GITHUB_TOKEN`                         | ""                       | GitHub Token（敏感，建议用环境变量） |
| `GITHUB_REPOSITORY`                    | AllureLove98/Senflare-IP | 推送目标仓库                         |
| `GIT_USER_NAME`                        | GitHub Action            | Git 提交用户名                       |
| `GIT_USER_EMAIL`                       | action@github.com        | Git 提交邮箱                         |
| `COMMIT_MESSAGE`                       | Update IP results        | 提交信息                             |
| `HTTP_PROXY`/`HTTPS_PROXY`/`ALL_PROXY` | ""                       | 代理地址（如 Clash）                 |
| `NO_PROXY`                             | localhost,127.0.0.1      | 不走代理的地址                       |

> 💡 `GITHUB_TOKEN` 等敏感信息不建议写入 `config.json` 并提交，推荐用 `.env` 或 Docker 环境变量注入。

### 地区定向扫描（可选）

参考 [XIU2/CloudflareSpeedTest](https://github.com/XIU2/CloudflareSpeedTest) 的 IP 段扫描 + 测速思路（其本身不支持按国家筛选），程序内置了**按国家/地区筛选**的整合实现：

- 配置 `region_targets` 选择目标地区（如 `["HK","JP","US"]`），程序从 Cloudflare 官方网段（`ips_sources`）采样并深度测速
- 配置 `region_target_count` 指定每个地区要多少个**有效节点**——"有效"由 `region_valid_max_delay`（延迟上限）和 `region_valid_min_bandwidth`（带宽下限）定义，均可自定义
- 候选不足会自动**补采**（`region_scan_per_segment` / `region_max_rounds`），保证输出的是测速后仍达标的节点，而不是筛出来一堆最后只剩几个
- 输出：每个地区一个纯 IP 文件 `Region-{CODE}.txt`（如 `Region-HK.txt`）+ 汇总文件 `Region-All.txt`（含测速 Mbps，速度快的排前面）
- 全部国家/地区代码见 [常见国家地区参考表.md](常见国家地区参考表.md)

### 结果推送机制（无需 GitHub Actions）

结果**不推送到 `main` 代码分支**，而是推送到独立的 `results` 分支（可用 `GIT_RESULT_BRANCH` 修改）：

```
GitHub 仓库
├── main 分支     → 代码（IPtest.py、Dockerfile 等），由 Docker 构建 workflow 使用
└── results 分支  → 运行结果（IPlist.txt、Senflare.txt、Ranking.txt 等），由容器自动推送
```

- 容器内 `entrypoint.sh` 在独立目录 `/app/results-repo` 维护 git，只提交输出文件，**不触碰代码**
- 每次推送前以远端 `results` 分支为基准 `reset --hard` 对齐，彻底避免 `fetch first` 冲突
- 推送失败自动 `--force` 兜底（结果分支历史由容器掌控，安全）
- 无需 GitHub Actions、无需定时 workflow，容器自己按 `RUN_INTERVAL_SECONDS` 循环运行

---

## 🔄 Docker Hub 自动构建

GitHub Actions 会在推送 `main` 分支或打 `v*` tag 时自动构建并推送镜像。

### 首次配置（只需一次）

1. 在 Docker Hub 创建 Access Token：https://hub.docker.com/settings/security
2. GitHub 仓库 → Settings → Secrets and variables → Actions，添加：
   - `DOCKERHUB_USERNAME`：Docker Hub 用户名
   - `DOCKERHUB_TOKEN`：上一步创建的 Token
3. （可选）Variables 中添加 `DOCKERHUB_REPO`，默认 `allurelove98/senflare-ip`

### 构建规则

| 触发              | Tag                            |
| ----------------- | ------------------------------ |
| push main         | `latest`                       |
| push tag v1.0.0   | `1.0.0`、`v1.0.0`、`sha-xxxxx` |
| workflow_dispatch | 手动触发                       |

自动构建 **linux/amd64 + linux/arm64** 双架构镜像。

---

## 🖥️ 本地运行

```bash
pip install -r requirements.txt
python IPtest.py
```

---

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
