# Senflare-IP

Cloudflare 优选 IP 采集器 —— 自动收集、测速、评分并输出可用 IP 列表。

- 采集 16 个公开 IP 源 → TCP 连通性测试 → 地区识别 → 带宽测速 → 综合评分排序
- 输出：`IPlist.txt`（基础可用 IP）、`Senflare.txt`（按地区）、`IPlist-Pro.txt`/`Senflare-Pro.txt`（进阶）、`Ranking.txt`（评分排行）
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
      # - ./config.json:/app/config.json:ro   # 可选：自定义配置
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

| 配置项                      | 默认      | 说明                    |
| --------------------------- | --------- | ----------------------- |
| `ip_sources`                | 16 个源   | 采集的公开 IP 列表地址  |
| `test_ports`                | 11 个端口 | TCP 测试端口            |
| `timeout`                   | 15        | 请求超时（秒）          |
| `api_timeout`               | 5         | API 超时（秒）          |
| `query_interval`            | 0.5       | 采集请求间隔            |
| `max_workers`               | 15        | 采集并发                |
| `batch_size`                | 30        | 批次大小                |
| `cache_ttl_hours`           | 168       | IP 缓存有效期（小时）   |
| `quick_filter_ports`        | [443]     | 快速过滤端口            |
| `region_workers`            | 10        | 地区识别并发            |
| `bandwidth_workers`         | 5         | 测速并发                |
| `advanced_mode`             | true      | 进阶模式（Pro/Ranking） |
| `bandwidth_test_count`      | 3         | 测速次数                |
| `bandwidth_test_size_mb`    | 50        | 测速文件大小（MB）      |
| `latency_filter_percentage` | 40        | 延迟过滤比例（%）       |
| `use_proxy_for_collection`  | true      | 采集是否走代理          |

### 运行参数（`env` 区块）

`GITHUB_TOKEN` 等运行/推送参数通过 `env` 区块配置，也可直接用环境变量（环境变量优先）。

| 配置项                                 | 默认                     | 说明                                 |
| -------------------------------------- | ------------------------ | ------------------------------------ |
| `RUN_INTERVAL_SECONDS`                 | 10800                    | 运行间隔（秒）                       |
| `GIT_PUSH_ENABLED`                     | false                    | 是否推送结果到 GitHub                |
| `GITHUB_TOKEN`                         | ""                       | GitHub Token（敏感，建议用环境变量） |
| `GITHUB_REPOSITORY`                    | AllureLove98/Senflare-IP | 推送目标仓库                         |
| `GIT_USER_NAME`                        | GitHub Action            | Git 提交用户名                       |
| `GIT_USER_EMAIL`                       | action@github.com        | Git 提交邮箱                         |
| `COMMIT_MESSAGE`                       | Update IP results        | 提交信息                             |
| `HTTP_PROXY`/`HTTPS_PROXY`/`ALL_PROXY` | ""                       | 代理地址（如 Clash）                 |
| `NO_PROXY`                             | localhost,127.0.0.1      | 不走代理的地址                       |

> 💡 `GITHUB_TOKEN` 等敏感信息不建议写入 `config.json` 并提交，推荐用 `.env` 或 Docker 环境变量注入。

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
├── requirements.txt
├── entrypoint.sh        # Docker 循环调度入口
├── Dockerfile
├── docker-compose.yaml
└── .github/workflows/docker-build.yml  # DockerHub 自动构建
```

## 📄 License

MIT
