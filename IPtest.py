"""
Cloudflare优选IP采集器 v2.6.0
===============================================

一个高效、智能的Cloudflare优选IP采集和检测工具，专为网络优化而设计。

🎯 核心功能
-----------
• IP采集：多API源并发采集，获取大量候选IP地址
• IP段扫描：从CIDR网段源（如Cloudflare官方ips-v4）随机采样IP，发现公开列表外的优选IP（可用开关控制）
• 地区定向扫描：按国家/地区筛选Cloudflare优选IP，确保每个目标地区凑够指定数量的有效节点（参考 CloudflareSpeedTest 的IP段扫描+测速思路）
• 智能筛选：TCP连接测试快速剔除不可用IP
• 性能测试：TCP Ping延迟测试 + HTTP带宽测试
• 地区识别：自动识别IP地理位置，支持缓存机制
• 智能排序：综合延迟、带宽、稳定性进行评分排名
• 多格式输出：生成基础版和高级版IP列表文件

⚡ 技术特性
-----------
• 智能缓存：TTL机制减少重复API调用，提升效率
• 高并发处理：多线程并发检测，大幅提升速度
• 容错机制：完善的异常处理和重试策略
• 详细日志：完整的操作日志记录，支持文件输出
• 资源优化：自动缓存管理，防止内存溢出
• CI优化：针对GitHub Actions等CI环境特别优化
• 多端口支持：可配置测试端口，适应不同需求
• 评分系统：综合性能指标，智能排名推荐

📊 输出文件
-----------
• IPlist.txt - 基础版IP列表（快速筛选结果）
• Senflare.txt - 基础版格式化IP列表（按地区分组）
• IPlist-Pro.txt - 高级版IP列表（性能测试结果）
• Senflare-Pro.txt - 高级版格式化IP列表（按地区分组，含测速 Mbps，速度快的排前面）
• Ranking.txt - 详细排名信息（延迟、带宽、评分）
• Region-All.txt - 地区定向扫描汇总（格式化，含测速Mbps，速度快的排前面）
• Region-{CODE}.txt - 单个地区的纯IP列表（如 Region-HK.txt）
• Cache.json - 地区信息缓存文件
• IPtest.log - 详细运行日志

🔧 配置说明
-----------
• 支持自定义测试端口、超时时间、并发数等参数
• 可开启/关闭高级模式（带宽测试、综合评分）
• 支持延迟排名筛选（取前N%的IP进行深度测试）
• 智能缓存管理，支持TTL和大小限制

作者：Senflare
版本：v2.8.0
更新：2026年8月4日
"""

# ===== 标准库导入 =====
# 正则表达式、文件操作、时间处理
from __future__ import annotations

import re
import os
import time
import ssl
import socket
import json
import logging
import sys
import threading
import random
import ipaddress
from datetime import datetime, timedelta
from concurrent.futures import ThreadPoolExecutor, as_completed
import glob
import urllib.parse
from collections import defaultdict

# ===== 第三方库导入 =====
# HTTP请求库和SSL警告处理
import requests
# from urllib3.exceptions import InsecureRequestWarning
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
# ===== 配置和初始化 =====

# 禁用SSL证书警告，避免HTTPS请求时的警告信息
import urllib3
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
# requests.packages.urllib3.disable_warnings(category=InsecureRequestWarning)

# 配置日志系统 - 同时输出到文件和控制台
# 日志级别可用环境变量 LOG_LEVEL 控制（DEBUG/INFO/WARNING/ERROR），默认 INFO
# 支持两种设置方式（优先级从高到低）：
#   1. 进程启动前注入：docker-compose environment / .env / shell export
#   2. config.json 的 env 区块：写入 LOG_LEVEL 键即可（load_config 后会自动重新应用）
_LOG_LEVELS = {
    'DEBUG': logging.DEBUG,
    'INFO': logging.INFO,
    'WARNING': logging.WARNING,
    'ERROR': logging.ERROR,
}


def _apply_log_level() -> None:
    """根据环境变量 LOG_LEVEL 重新设置日志级别。

    本函数可重复调用：load_config() 读取 config.json 的 env 区块后，
    再次调用即可让配置文件中设置的 LOG_LEVEL 生效。
    """
    level = _LOG_LEVELS.get(os.getenv('LOG_LEVEL', 'INFO').upper(), logging.INFO)
    logging.getLogger().setLevel(level)  # 根 logger 级别（控制台/文件 handler 均受其约束）
    if 'logger' in globals():
        logger.setLevel(level)  # 模块 logger 显式同步，避免 NOTSET 继承歧义


logging.basicConfig(
    level=_LOG_LEVELS.get(os.getenv('LOG_LEVEL', 'INFO').upper(), logging.INFO),
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler('IPtest.log', encoding='utf-8'),  # 文件日志
        logging.StreamHandler()  # 控制台日志
    ]
)
logger = logging.getLogger(__name__)

# ===== 核心配置 =====
# 程序运行的核心参数配置，可根据需要调整
PROXY_URL = os.getenv('HTTP_PROXY') or os.getenv('HTTPS_PROXY') or os.getenv('ALL_PROXY') or ''
PROXY_ENABLED = bool(PROXY_URL)

# ⚠️ 本程序不内置任何默认参数：全部配置必须来自外部 config.json（配置项）或环境变量（机密/运行参数）
# 缺失配置项时程序启动即报错退出，杜绝"配置没生效"类问题
CONFIG: dict = {}

# 程序运行所必需的全部配置键（来自 config.json 顶层，缺失任一都会启动失败并明确提示）
REQUIRED_CONFIG_KEYS = [
    'ip_sources',
    'test_ports',
    'timeout',
    'api_timeout',
    'query_interval',
    'max_workers',
    'batch_size',
    'cache_ttl_hours',
    'quick_filter_ports',
    'region_workers',
    'bandwidth_workers',
    'advanced_mode',
    'bandwidth_test_count',
    'bandwidth_test_size_mb',
    'speed_test_url',
    'rate_limit_pause_seconds',
    'bandwidth_retry_rounds',
    'latency_filter_percentage',
    'use_proxy_for_collection',
]

# ===== 配置文件加载 =====
# 本程序不内置默认配置：全部配置项必须完整写在外部 config.json，机密/运行参数用 env 区块或环境变量
# 配置文件路径可通过环境变量 CONFIG_FILE 指定，默认读取当前目录的 config.json
CONFIG_FILE = os.getenv('CONFIG_FILE', 'config.json')


def load_config() -> None:
    """从外部 config.json 加载全部配置（不内置默认值，配置缺失即报错退出）。

    - 配置项（ip_sources、test_ports 等）：必须完整写在 config.json 顶层
    - env 区块：运行参数/机密（LOG_LEVEL、GITHUB_TOKEN、HTTP_PROXY 等），
      写入环境变量（已设置的容器环境变量优先）
    """
    global CONFIG, PROXY_URL, PROXY_ENABLED
    if not os.path.exists(CONFIG_FILE):
        logger.error(f"❌ 未找到配置文件 {CONFIG_FILE}（本程序不内置默认配置，必须挂载 config.json）")
        logger.error("   请参考 config.example.json 创建 config.json，并通过 compose 挂载到容器 /app/config.json")
        sys.exit(1)

    try:
        with open(CONFIG_FILE, 'r', encoding='utf-8') as f:
            user_config = json.load(f)
    except Exception as e:
        logger.error(f"❌ 读取配置文件 {CONFIG_FILE} 失败: {str(e)[:80]}，程序退出")
        sys.exit(1)

    if not isinstance(user_config, dict):
        logger.error(f"❌ 配置文件 {CONFIG_FILE} 格式错误（应为JSON对象），程序退出")
        sys.exit(1)

    # 过滤以 // 开头的注释键（如 config.example.json 中的 "// 说明"）
    user_config = {k: v for k, v in user_config.items() if not k.startswith('//')}

    # 提取 env 区块（运行参数/机密，如 LOG_LEVEL、GITHUB_TOKEN、HTTP_PROXY 等）
    # 优先级：已设置的容器环境变量 > config.json 中的 env 区块
    env_block = user_config.pop('env', None)
    if isinstance(env_block, dict):
        applied = 0
        for key, value in env_block.items():
            # 跳过注释键（config.example.json 中以 // 开头的键，如 "// LOG_LEVEL"）
            if key.startswith('//') or value is None:
                continue
            # 非空环境变量才优先（空串视为未设置），否则 env 区块永远被 compose 默认值拦截
            if not os.getenv(key):
                if isinstance(value, bool):
                    value = 'true' if value else 'false'
                os.environ[key] = str(value)
                applied += 1
        if applied:
            logger.info(f"⚙️ 已从配置文件 env 区块设置 {applied} 个环境变量")
        # env 区块可能包含 LOG_LEVEL，重新应用日志级别使其生效
        _apply_log_level()
        # 若配置了代理，同步更新代理相关全局变量
        proxy = (os.getenv('HTTP_PROXY') or os.getenv('HTTPS_PROXY')
                 or os.getenv('ALL_PROXY') or '')
        if proxy:
            PROXY_URL = proxy
            PROXY_ENABLED = True

    # 校验必需配置键：不内置默认值，缺失即启动失败，避免运行中途 KeyError
    missing = [k for k in REQUIRED_CONFIG_KEYS if k not in user_config]
    if missing:
        logger.error(f"❌ 配置文件 {CONFIG_FILE} 缺少必需配置项: {', '.join(missing)}")
        logger.error("   请参考 config.example.json 补齐后重新启动")
        sys.exit(1)

    CONFIG = user_config
    logger.info(f"⚙️ 已加载配置文件 {CONFIG_FILE}，共 {len(CONFIG)} 个配置项")


load_config()

# ===== 国家/地区映射表 =====
# 将ISO国家代码映射为中文名称，支持全球主要国家和地区
COUNTRY_MAPPING = {
    # 统一添加常见国家和地区
    # 🌎 北美地区
    'US': '美国', 'CA': '加拿大', 'MX': '墨西哥', 'CR': '哥斯达黎加', 'GT': '危地马拉', 'HN': '洪都拉斯',
    'NI': '尼加拉瓜', 'PA': '巴拿马', 'CU': '古巴', 'JM': '牙买加', 'TT': '特立尼达和多巴哥',
    'BZ': '伯利兹', 'SV': '萨尔瓦多', 'DO': '多米尼加', 'HT': '海地',
    # 🌎 南美地区
    'BR': '巴西', 'AR': '阿根廷', 'CL': '智利', 'CO': '哥伦比亚', 'PE': '秘鲁', 'VE': '委内瑞拉',
    'UY': '乌拉圭', 'PY': '巴拉圭', 'BO': '玻利维亚', 'EC': '厄瓜多尔', 'GY': '圭亚那',
    'SR': '苏里南', 'FK': '福克兰群岛',
    # 🌍 欧洲地区
    'UK': '英国', 'GB': '英国', 'FR': '法国', 'DE': '德国', 'IT': '意大利', 'ES': '西班牙', 'NL': '荷兰',
    'RU': '俄罗斯', 'SE': '瑞典', 'CH': '瑞士', 'BE': '比利时', 'AT': '奥地利', 'IS': '冰岛',
    'PL': '波兰', 'DK': '丹麦', 'NO': '挪威', 'FI': '芬兰', 'PT': '葡萄牙', 'IE': '爱尔兰',
    'UA': '乌克兰', 'CZ': '捷克', 'GR': '希腊', 'HU': '匈牙利', 'RO': '罗马尼亚', 'TR': '土耳其',
    'BG': '保加利亚', 'LT': '立陶宛', 'LV': '拉脱维亚', 'EE': '爱沙尼亚', 'BY': '白俄罗斯',
    'LU': '卢森堡', 'LUX': '卢森堡', 'SI': '斯洛文尼亚', 'SK': '斯洛伐克', 'MT': '马耳他',
    'HR': '克罗地亚', 'RS': '塞尔维亚', 'BA': '波黑', 'ME': '黑山', 'MK': '北马其顿',
    'AL': '阿尔巴尼亚', 'XK': '科索沃', 'MD': '摩尔多瓦', 'GE': '格鲁吉亚', 'AM': '亚美尼亚',
    'AZ': '阿塞拜疆', 'CY': '塞浦路斯', 'MC': '摩纳哥', 'SM': '圣马力诺', 'VA': '梵蒂冈',
    'AD': '安道尔', 'LI': '列支敦士登',
    # 🌏 亚洲地区
    'CN': '中国', 'HK': '中国香港', 'TW': '中国台湾', 'MO': '中国澳门', 'JP': '日本', 'KR': '韩国',
    'SG': '新加坡', 'SGP': '新加坡', 'IN': '印度', 'ID': '印度尼西亚', 'MY': '马来西亚', 'MYS': '马来西亚',
    'TH': '泰国', 'PH': '菲律宾', 'VN': '越南', 'PK': '巴基斯坦', 'BD': '孟加拉', 'KZ': '哈萨克斯坦',
    'IL': '以色列', 'ISR': '以色列', 'SA': '沙特阿拉伯', 'SAU': '沙特阿拉伯', 'AE': '阿联酋', 
    'QAT': '卡塔尔', 'OMN': '阿曼', 'KW': '科威特', 'BH': '巴林', 'IQ': '伊拉克', 'IR': '伊朗',
    'AF': '阿富汗', 'UZ': '乌兹别克斯坦', 'KG': '吉尔吉斯斯坦', 'TJ': '塔吉克斯坦', 'TM': '土库曼斯坦',
    'MN': '蒙古', 'NP': '尼泊尔', 'BT': '不丹', 'LK': '斯里兰卡', 'MV': '马尔代夫',
    'MM': '缅甸', 'LA': '老挝', 'KH': '柬埔寨', 'BN': '文莱', 'TL': '东帝汶',
    'LK': '斯里兰卡', 'MV': '马尔代夫', 'NP': '尼泊尔', 'BT': '不丹',
    # 🌊 大洋洲地区
    'AU': '澳大利亚', 'NZ': '新西兰', 'FJ': '斐济', 'PG': '巴布亚新几内亚', 'NC': '新喀里多尼亚',
    'VU': '瓦努阿图', 'SB': '所罗门群岛', 'TO': '汤加', 'WS': '萨摩亚', 'KI': '基里巴斯',
    'TV': '图瓦卢', 'NR': '瑙鲁', 'PW': '帕劳', 'FM': '密克罗尼西亚', 'MH': '马绍尔群岛',
    # 🌍 非洲地区
    'ZA': '南非', 'EG': '埃及', 'NG': '尼日利亚', 'KE': '肯尼亚', 'ET': '埃塞俄比亚',
    'GH': '加纳', 'TZ': '坦桑尼亚', 'UG': '乌干达', 'DZ': '阿尔及利亚', 'MA': '摩洛哥',
    'TN': '突尼斯', 'LY': '利比亚', 'SD': '苏丹', 'SS': '南苏丹', 'ER': '厄立特里亚',
    'DJ': '吉布提', 'SO': '索马里', 'ET': '埃塞俄比亚', 'KE': '肯尼亚', 'TZ': '坦桑尼亚',
    'UG': '乌干达', 'RW': '卢旺达', 'BI': '布隆迪', 'MW': '马拉维', 'ZM': '赞比亚',
    'ZW': '津巴布韦', 'BW': '博茨瓦纳', 'NA': '纳米比亚', 'SZ': '斯威士兰', 'LS': '莱索托',
    'MZ': '莫桑比克', 'MG': '马达加斯加', 'MU': '毛里求斯', 'SC': '塞舌尔', 'KM': '科摩罗',
    'CV': '佛得角', 'ST': '圣多美和普林西比', 'GW': '几内亚比绍', 'GN': '几内亚', 'SL': '塞拉利昂',
    'LR': '利比里亚', 'CI': '科特迪瓦', 'GH': '加纳', 'TG': '多哥', 'BJ': '贝宁',
    'NE': '尼日尔', 'BF': '布基纳法索', 'ML': '马里', 'SN': '塞内加尔', 'GM': '冈比亚',
    'GN': '几内亚', 'GW': '几内亚比绍', 'ST': '圣多美和普林西比', 'CV': '佛得角',
    # ❓ 其他/未知
    'Unknown': '未知'
}

# ===== 输出文件定义 =====
# 统一管理所有输出文件路径，避免硬编码散落
FILE_BASIC_IP = 'IPlist.txt'            # 基础版IP列表
FILE_BASIC_REGION = 'Senflare.txt'      # 基础版格式化IP列表（按地区分组）
FILE_PRO_IP = 'IPlist-Pro.txt'          # 高级版IP列表
FILE_PRO_REGION = 'Senflare-Pro.txt'    # 高级版格式化IP列表（按地区分组）
FILE_RANKING = 'Ranking.txt'            # 详细排名信息
FILE_CACHE = 'Cache.json'               # 地区信息缓存
FILE_REGION_ALL = 'Region-All.txt'      # 地区定向扫描汇总（格式化，含测速Mbps）
FILE_REGION_PREFIX = 'Region-'          # 地区定向扫描单地区文件前缀（如 Region-HK.txt 纯IP）

# ===== 全局变量 =====
# 地区信息缓存，用于存储IP地理位置查询结果
region_cache = {}

# ===== 网络会话配置 =====
# 配置HTTP会话，优化网络请求性能
session = requests.Session()
collection_session = requests.Session()

for s in (session, collection_session):
    s.headers.update({
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36',
        'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8,application/signed-exchange;v=b3;q=0.7',
        'Accept-Language': 'zh-CN,zh;q=0.9,en;q=0.8',
        'Connection': 'keep-alive',
        'Upgrade-Insecure-Requests': '1',
        'Sec-Fetch-Dest': 'document',
        'Sec-Fetch-Mode': 'navigate',
        'Sec-Fetch-Site': 'none',
        'Cache-Control': 'max-age=0'
    })

# 配置连接池 - 优化网络连接性能
def configure_session(session_obj: requests.Session, proxy_url: str, label: str) -> None:
    # 完整的重试策略：对连接/读取错误和 429/5xx 状态码均重试，指数退避
    retry = Retry(
        total=3,                       # 总重试次数
        connect=3,                     # 连接失败重试次数
        read=3,                        # 读取失败重试次数
        backoff_factor=0.5,            # 指数退避因子（0.5/1/2秒）
        status_forcelist=[429, 500, 502, 503, 504],  # 触发重试的状态码
        respect_retry_after_header=True,             # 尊重 Retry-After 响应头
    )
    adapter = HTTPAdapter(
        pool_connections=100,    # 连接池大小
        pool_maxsize=100,         # 最大连接数
        max_retries=retry       # 智能重试策略
    )
    session_obj.mount('http://', adapter)
    session_obj.mount('https://', adapter)

    if proxy_url:
        session_obj.proxies.update({
            'http': proxy_url,
            'https': proxy_url,
        })
        logger.info(f"🔐 {label} 已启用代理: {proxy_url}")
    else:
        logger.info(f"🔐 {label} 未配置代理，使用直连")

configure_session(session, '', '默认检测会话')
collection_proxy = PROXY_URL if (PROXY_ENABLED and CONFIG["use_proxy_for_collection"]) else ''
configure_session(collection_session, collection_proxy, '采集会话')

# ===== 缓存管理模块 =====
# 智能缓存系统，支持TTL机制和自动清理

def load_region_cache() -> None:
    """
    加载地区信息缓存
    
    从Cache.json文件中加载已缓存的IP地理位置信息，
    如果文件不存在或加载失败，则使用空缓存。
    
    Returns:
        None: 直接修改全局变量region_cache
    """
    global region_cache
    if os.path.exists(FILE_CACHE):
        try:
            with open(FILE_CACHE, 'r', encoding='utf-8') as f:
                region_cache = json.load(f)
            logger.info(f"📦 成功加载缓存文件，包含 {len(region_cache)} 个条目")
        except Exception as e:
            logger.warning(f"⚠️ 加载缓存文件失败: {str(e)[:50]}")
            region_cache = {}
    else:
        logger.info("📦 缓存文件不存在，使用空缓存")
        region_cache = {}

def save_region_cache() -> None:
    """
    保存地区信息缓存
    
    将当前内存中的地区缓存数据保存到Cache.json文件中，
    用于下次启动时快速加载已查询过的IP地理位置信息。
    
    Returns:
        None: 直接保存到文件，无返回值
    """
    try:
        with open(FILE_CACHE, 'w', encoding='utf-8') as f:
            json.dump(region_cache, f, ensure_ascii=False)
        logger.info(f"💾 成功保存缓存文件，包含 {len(region_cache)} 个条目")
    except Exception as e:
        logger.error(f"❌ 保存缓存文件失败: {str(e)[:50]}")
        pass

def is_cache_valid(timestamp: str, ttl_hours: int = 24) -> bool:
    """
    检查缓存是否有效
    
    Args:
        timestamp (str): 缓存时间戳（ISO格式）
        ttl_hours (int): 缓存有效期（小时），默认24小时
    
    Returns:
        bool: True表示缓存有效，False表示已过期
    """
    if not timestamp:
        return False
    try:
        cache_time = datetime.fromisoformat(timestamp)
    except (ValueError, TypeError):
        return False
    return datetime.now() - cache_time < timedelta(hours=ttl_hours)

def clean_expired_cache() -> None:
    """
    清理过期缓存和限制缓存大小
    
    自动清理过期的缓存条目，并限制缓存大小以防止内存溢出。
    支持TTL机制和LRU策略。
    
    Returns:
        None: 直接修改全局变量region_cache
    """
    global region_cache
    current_time = datetime.now()
    expired_keys = []
    
    # 清理过期缓存
    for ip, data in region_cache.items():
        if isinstance(data, dict) and 'timestamp' in data:
            try:
                cache_time = datetime.fromisoformat(data['timestamp'])
            except (ValueError, TypeError):
                expired_keys.append(ip)  # 时间戳损坏，视为过期
                continue
            if current_time - cache_time >= timedelta(hours=CONFIG["cache_ttl_hours"]):
                expired_keys.append(ip)
    
    for key in expired_keys:
        del region_cache[key]
    
    # 限制缓存大小（最多保留1000个条目）
    if len(region_cache) > 1000:
        # 按时间排序，删除最旧的条目
        sorted_items = sorted(region_cache.items(), 
                            key=lambda x: x[1].get('timestamp', '') if isinstance(x[1], dict) else '')
        items_to_remove = len(region_cache) - 1000
        for i in range(items_to_remove):
            del region_cache[sorted_items[i][0]]
        logger.info(f"缓存过大，清理了 {items_to_remove} 个旧条目")
    
    if expired_keys:
        logger.info(f"清理了 {len(expired_keys)} 个过期缓存条目")

# ===== 文件操作模块 =====
# 文件管理功能，包括删除、创建等操作

def delete_file_if_exists(file_path: str) -> None:
    """
    删除指定文件（如果存在）
    
    在程序开始前清理旧的结果文件，避免结果累积。
    
    Args:
        file_path (str): 要删除的文件路径
    
    Returns:
        None: 无返回值，仅执行删除操作
    """
    if os.path.exists(file_path):
        try:
            os.remove(file_path)
            logger.info(f"🗑️ 已删除原有文件: {file_path}")
        except Exception as e:
            logger.warning(f"⚠️ 删除文件失败: {str(e)}")

# ===== IP段采集模块 =====
# Cloudflare CIDR 网段扫描支持：从段源（如官方 ips-v4）获取网段并采样IP

def sample_ips_from_cidr(network: ipaddress.IPv4Network, count: int) -> list:
    """
    从单个CIDR网段中采样IP地址

    小网段（地址总数 <= count）直接枚举全部；大网段随机采样 count 个，
    避免为巨型网段生成海量候选。网络/广播地址也会被采样，反正会被TCP筛选剔除。

    Args:
        network (ipaddress.IPv4Network): 已解析的IPv4网段
        count (int): 大网段随机采样数量

    Returns:
        list: 采样出的IP字符串列表
    """
    start = int(network.network_address)
    end = int(network.broadcast_address)
    total = end - start + 1

    if total <= count:
        # 小网段：全部生成
        return [str(ipaddress.IPv4Address(i)) for i in range(start, end + 1)]

    # 大网段：随机采样 count 个（带重试上限，避免极端情况下死循环）
    picked = set()
    attempts = 0
    while len(picked) < count and attempts < count * 20:
        picked.add(random.randint(start, end))
        attempts += 1
    return [str(ipaddress.IPv4Address(i)) for i in picked]


def collect_cidr_ips(cidr_sources: list, per_segment: int = 10) -> list:
    """
    从CIDR段源采集网段并采样IP地址

    请求每个段源，用正则提取其中的 IPv4 CIDR（如 173.245.48.0/20），
    解析后按 per_segment 从每个网段采样若干 IP 返回。

    Args:
        cidr_sources (list): 段源 URL 列表
        per_segment (int): 每个网段采样的IP数量（自动限制在 1-200）

    Returns:
        list: 采样出的IP字符串列表
    """
    per_segment = max(1, min(int(per_segment), 200))
    sampled_ips = []
    successful = 0
    failed = 0
    total_cidrs = 0

    for i, url in enumerate(cidr_sources):
        try:
            logger.info(f"🔍 从IP段源 {url} 采集CIDR网段...")
            if i > 0:
                time.sleep(CONFIG["query_interval"])
            resp = collection_session.get(url, timeout=CONFIG["timeout"])
            logger.debug(f"🔍 {url} 响应状态 {resp.status_code}，内容 {len(resp.text)} 字节")

            if resp.status_code == 200:
                cidrs = re.findall(r'\b(?:[0-9]{1,3}\.){3}[0-9]{1,3}/\d{1,2}\b', resp.text)
                networks = []
                for cidr in cidrs:
                    try:
                        network = ipaddress.ip_network(cidr, strict=False)
                        if network.version == 4:
                            networks.append(network)
                    except ValueError:
                        continue
                # 网段去重
                networks = list(dict.fromkeys(networks))
                total_cidrs += len(networks)

                segment_count = 0
                for network in networks:
                    segment_ips = sample_ips_from_cidr(network, per_segment)
                    sampled_ips.extend(segment_ips)
                    segment_count += len(segment_ips)

                successful += 1
                logger.info(f"✅ 段源 {url} 解析到 {len(networks)} 个IPv4网段，采样 {segment_count} 个IP")
            else:
                failed += 1
                logger.warning(f"❌ 段源 {url} 失败（状态码 {resp.status_code}）")
        except Exception as e:
            failed += 1
            logger.error(f"❌ 段源 {url} 出错: {str(e)[:50]}")

    logger.info(f"📊 段源采集统计: 成功 {successful} 个源，失败 {failed} 个源，共解析 {total_cidrs} 个网段")
    return sampled_ips

# ===== 地区定向扫描模块 =====
# 参考 CloudflareSpeedTest 的 IP段扫描+测速筛选思路，按国家/地区维度筛选：
# 从IP段源采样 → 快速筛选 → 地区识别 → 补采凑够候选 → 深度测试 → 有效定义筛选

def scan_region_targets(region_targets: list, target_count: int = 10,
                        valid_max_delay: float = 300, valid_min_bandwidth: float = 5,
                        per_segment: int = 50, max_rounds: int = 3,
                        existing_ips: list | None = None) -> dict:
    """
    地区定向扫描：为每个目标地区凑够 target_count 个有效节点

    有效节点定义（可配置）：延迟 < valid_max_delay ms 且 带宽 > valid_min_bandwidth Mbps。
    候选数量 = target_count * 3（深度测试淘汰余量），不足则自动补采，保证输出的是
    测速后仍达标的有效节点，而不是"筛出来一堆最后只剩1个"。

    Args:
        region_targets (list): 目标地区代码列表，如 ["HK","JP","US"]
        target_count (int): 每个地区需要的有效节点数
        valid_max_delay (float): 有效节点延迟上限（毫秒）
        valid_min_bandwidth (float): 有效节点带宽下限（Mbps）
        per_segment (int): 补采时每个网段采样数
        max_rounds (int): 最大补采轮数
        existing_ips (list | None): 主流程已快速筛选通过的IP，作为初始候选

    Returns:
        dict: {地区代码: [(ip, min_delay, bandwidth, score), ...]} 每个地区最多 target_count 个，按带宽降序
    """
    targets = set()
    for r in region_targets:
        r = str(r).strip().upper()
        if r and r.isalpha():
            targets.add(r)
    if not targets:
        logger.info("ℹ️ 地区定向扫描未配置目标地区（region_targets 为空），跳过")
        return {}

    cidr_sources = CONFIG.get('ips_sources') or []
    if not cidr_sources:
        logger.warning("⚠️ 地区定向扫描需要配置 ips_sources（IP段源）才能补采候选，跳过")
        return {}

    candidate_goal = max(int(target_count) * 3, 30)
    candidates = {t: set() for t in targets}
    already_quick_tested = set()

    logger.info("🎯 ===== 地区定向扫描 =====")
    logger.info(f"🎯 目标地区: {', '.join(sorted(targets))} | 每个地区 {target_count} 个有效节点 | "
                f"有效定义: 延迟<{valid_max_delay}ms 且 带宽>{valid_min_bandwidth}Mbps | 候选目标 {candidate_goal} 个")

    # 第0轮：利用主流程已有的快速筛选通过IP作为初始候选
    if existing_ips:
        logger.info(f"🎯 复用主流程 {len(existing_ips)} 个已通过快速筛选的IP作为初始候选")
        seed_results = get_regions_concurrently([(ip, 0, 0) for ip in existing_ips])
        for ip, region_code, _, _ in seed_results:
            already_quick_tested.add(ip)
            if region_code in targets:
                candidates[region_code].add(ip)

    # 补采循环：候选不足则继续采样
    for round_idx in range(1, max_rounds + 1):
        if all(len(candidates[t]) >= candidate_goal for t in targets):
            logger.info("🎯 所有目标地区候选数量已达标，提前结束补采")
            break

        logger.info(f"🎯 补采第 {round_idx}/{max_rounds} 轮：从IP段源采样（每网段 {per_segment} 个）")
        sampled = collect_cidr_ips(cidr_sources, per_segment)
        if not sampled:
            logger.warning("⚠️ 本轮采样为空，停止补采")
            break

        new_ips = [ip for ip in sampled if ip not in already_quick_tested]
        if not new_ips:
            logger.warning("⚠️ 本轮无新增IP（已全部测过），停止补采")
            break

        quick_results = quick_filter_ips_concurrently(new_ips)
        if not quick_results:
            logger.warning(f"⚠️ 第{round_idx}轮快速筛选无可用IP，继续下一轮")
            continue
        already_quick_tested.update(ip for ip, _ in quick_results)

        region_results = get_regions_concurrently([(ip, 0, 0) for ip, _ in quick_results])
        round_gain = {t: 0 for t in targets}
        for ip, region_code, _, _ in region_results:
            if region_code in targets:
                if ip not in candidates[region_code]:
                    candidates[region_code].add(ip)
                    round_gain[region_code] += 1

        for t in sorted(targets):
            logger.info(f"🎯 第{round_idx}轮后 {t}: 候选 {len(candidates[t])}/{candidate_goal}（本轮新增 {round_gain[t]}）")

    # 深度测试：TCP Ping + 带宽测试，按有效定义筛选
    valid_results = {}
    for region in sorted(targets):
        region_ips = sorted(candidates[region])
        if not region_ips:
            logger.warning(f"⚠️ 地区 {region} 没有候选IP，无法凑够有效节点")
            continue

        logger.info(f"🎯 ===== 深度测试地区 {region}（{len(region_ips)} 个候选） =====")
        ping_results = test_ips_concurrently(region_ips)
        if not ping_results:
            logger.warning(f"⚠️ 地区 {region} TCP Ping 全部失败")
            continue

        bw_results = test_bandwidth_concurrently(ping_results)
        valid = []
        for ip, min_delay, avg_delay, bandwidth, latency, score in bw_results:
            if min_delay < valid_max_delay and bandwidth > valid_min_bandwidth:
                valid.append((ip, min_delay, bandwidth, score))
        valid.sort(key=lambda x: x[2], reverse=True)  # 按带宽降序，速度快的在前

        logger.info(f"🎯 地区 {region}: 深度测试通过 {len(bw_results)} 个，达到有效标准 {len(valid)} 个")
        if valid:
            valid_results[region] = valid[:target_count]
            if len(valid) < target_count:
                logger.warning(f"⚠️ 地区 {region} 有效节点 {len(valid)} < 目标 {target_count}"
                               f"（可增大 region_scan_per_segment / region_max_rounds，或放宽有效定义）")

    return valid_results


def save_region_results(region_results: dict) -> None:
    """
    保存地区定向扫描结果

    - 每个地区一个纯IP文件：Region-{CODE}.txt（如 Region-HK.txt，方便直接使用）
    - 一个格式化汇总文件：Region-All.txt（按地区分组，含测速Mbps，速度快的排前面）

    Args:
        region_results (dict): scan_region_targets 的返回值
    """
    if not region_results:
        logger.warning("⚠️ 地区定向扫描无有效结果，跳过保存")
        return

    all_lines = []
    total = 0
    for region in sorted(region_results.keys()):
        items = region_results[region]
        country_name = get_country_name(region)

        single_file = f"{FILE_REGION_PREFIX}{region}.txt"
        with open(single_file, 'w', encoding='utf-8') as f:
            for ip, min_delay, bandwidth, score in items:
                f.write(f"{ip}\n")
        logger.info(f"📄 已保存地区 {region}（{country_name}）的 {len(items)} 个有效节点到 {single_file}")

        for idx, (ip, min_delay, bandwidth, score) in enumerate(items, 1):
            all_lines.append(f"{ip}#{region} {country_name}节点 | {bandwidth:.0f}Mbps | {idx:02d}")
        total += len(items)

    with open(FILE_REGION_ALL, 'w', encoding='utf-8') as f:
        f.write('\n'.join(all_lines))
    logger.info(f"📄 已保存地区汇总 {total} 个有效节点到 {FILE_REGION_ALL}")

# ===== 网络检测模块 =====
# 网络连接测试功能，包括TCP连接、延迟测试、带宽测试等

def is_valid_ipv4(ip: str) -> bool:
    """
    校验IPv4地址格式

    Args:
        ip (str): 待校验的IP地址

    Returns:
        bool: True为合法IPv4地址
    """
    try:
        parts = ip.split('.')
        return len(parts) == 4 and all(0 <= int(p) <= 255 for p in parts)
    except (ValueError, AttributeError):
        return False


def tcp_connect_test(ip: str, ports: list, timeout: float = 0.5) -> tuple:
    """
    TCP连接测试 - 遍历端口取最小延迟

    遍历给定的端口列表进行TCP连接测试，
    返回是否可用以及所有连通端口中的最小延迟。

    Args:
        ip (str): 要测试的IP地址
        ports (list): 测试端口列表
        timeout (float): 单次连接超时时间（秒）

    Returns:
        tuple: (是否可用, 最小延迟毫秒) - (bool, int)
    """
    if not is_valid_ipv4(ip):
        return (False, 0)

    if not ports or not isinstance(ports, list):
        logger.warning(f"⚠️ 测试端口配置无效，跳过IP {ip}")
        return (False, 0)

    min_delay = float('inf')
    success_count = 0

    for port in ports:
        # 验证端口号
        if not isinstance(port, int) or not (1 <= port <= 65535):
            logger.debug(f"⚠️ 无效端口号 {port}，跳过")
            continue

        try:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                s.settimeout(timeout)
                start_time = time.time()

                conn_result = s.connect_ex((ip, port))
                if conn_result == 0:
                    delay = round((time.time() - start_time) * 1000)
                    min_delay = min(min_delay, delay)
                    success_count += 1
                    logger.debug(f"✅ IP {ip} 端口 {port} 连接成功: {delay}ms")
                else:
                    logger.debug(f"IP {ip} 端口 {port} 连接失败（错误码 {conn_result}，{time.time() - start_time:.2f}s）")
        except (socket.timeout, socket.error, OSError) as e:
            logger.debug(f"IP {ip} 端口 {port} 连接失败: {str(e)[:30]}")
            continue
        except Exception as e:
            logger.debug(f"IP {ip} 端口 {port} 检测异常: {str(e)[:30]}")
            continue

    if success_count > 0:
        return (True, min_delay)

    return (False, 0)


def quick_filter_ip(ip: str) -> tuple:
    """
    快速筛选IP - 只测常用端口（443）

    快速筛选阶段只测试443端口，速度优先，
    用于快速剔除明显不可用的IP。

    Args:
        ip (str): 要测试的IP地址

    Returns:
        tuple: (是否可用, 延迟毫秒数) - (bool, int)
    """
    is_good, delay = tcp_connect_test(ip, CONFIG["quick_filter_ports"], timeout=0.5)
    if is_good:
        logger.debug(f"🔍 {ip} 快速筛选通过（延迟 {delay}ms）")
    else:
        logger.debug(f"🔍 {ip} 快速筛选失败（无可用端口）")
    return (is_good, delay)

# ===== 带宽测试 429 限流自动暂停 =====
# 测速服务器（speed.cloudflare.com / httpbin.org 等）在请求过密时会返回 429 Too Many Requests。
# 一旦检测到 429，全局暂停所有测速线程，等待 rate_limit_pause_seconds 秒后自动恢复，
# 避免持续触发限流导致全部测速失败。
_RATE_LIMIT_LOCK = threading.Lock()
_RATE_LIMIT_PAUSE_UNTIL = 0.0  # 时间戳：此时间之前所有测速请求均暂停
_RATE_LIMITED_IPS: set = set()  # 因 429 限流失败的 IP 集合（暂停结束后自动重试）


def _rate_limit_pause() -> None:
    """触发 429 全局暂停：所有测速线程暂停 rate_limit_pause_seconds 秒后自动恢复。"""
    global _RATE_LIMIT_PAUSE_UNTIL
    pause_seconds = float(CONFIG.get('rate_limit_pause_seconds', 60))
    with _RATE_LIMIT_LOCK:
        new_until = time.time() + pause_seconds
        if new_until > _RATE_LIMIT_PAUSE_UNTIL:
            _RATE_LIMIT_PAUSE_UNTIL = new_until
            logger.warning(f"🚦 检测到测速服务器限流（429），测速自动暂停 {pause_seconds:.0f} 秒后恢复")
        else:
            logger.debug("🚦 429 暂停已生效，不重复延长")


def _wait_while_rate_limited() -> None:
    """等待 429 暂停结束（阻塞，暂停时间不占用单 IP 测速总超时）。"""
    while True:
        with _RATE_LIMIT_LOCK:
            remaining = _RATE_LIMIT_PAUSE_UNTIL - time.time()
        if remaining <= 0:
            return
        logger.debug(f"🚦 429 暂停中，剩余 {remaining:.0f} 秒后自动恢复...")
        time.sleep(min(remaining, 5))


def _get_speed_test_urls() -> list:
    """获取配置的全部测速地址列表（支持字符串或数组，过滤空值）。"""
    raw = CONFIG.get('speed_test_url', 'https://speed.cloudflare.com/__down?bytes={size}')
    if isinstance(raw, str):
        raw = [raw]
    urls = [u.strip() for u in raw if isinstance(u, str) and u.strip()]
    return urls or ['https://speed.cloudflare.com/__down?bytes={size}']


def _download_speed_try_all(ip: str, size_bytes: int, connect_timeout: float = 3,
                            download_timeout: float = 5) -> tuple:
    """依次尝试配置的全部测速地址，返回 (速度Mbps, 延迟毫秒, 原因, 是否因429失败)。

    按配置顺序逐个尝试：第一个成功下载的地址即被采用；全部失败返回最后原因。
    任一地址返回 429 时标记 rate_limited=True，由并发层在暂停结束后自动重试该 IP。
    """
    urls = _get_speed_test_urls()
    last_reason = ''
    rate_limited = False
    for url in urls:
        try:
            speed, latency, reason = _download_speed_direct(
                ip, size_bytes, connect_timeout=connect_timeout,
                download_timeout=download_timeout, speed_url=url)
        except Exception as e:
            speed, latency, reason = 0, 0, f"连接失败: {str(e)[:40]}"
        if speed > 0:
            return speed, latency, reason, False
        last_reason = reason
        if '429' in reason or '限流' in reason:
            rate_limited = True
        logger.debug(f"⚡ {ip} 测速地址 {url} 失败（{reason}），尝试下一个...")
    return 0, 0, last_reason, rate_limited


def _parse_speed_test_url(url: str, size_bytes: int) -> tuple:
    """解析测速文件 URL 模板，返回 (scheme, host, port, path)。

    URL 中的 {size} / {test_size_bytes} 占位符会被替换为实际下载字节数
    （如 https://httpbin.org/bytes/{test_size_bytes}），也可配置不带占位符的
    固定地址（如 https://cf.xiu2.xyz/url）。SNI 与 Host 头取自 URL 域名，
    TCP 目标仍是被测 IP，从而测出该 IP 到本地的真实速度。
    """
    url = url.replace('{size}', str(int(size_bytes))).replace('{test_size_bytes}', str(int(size_bytes)))
    parsed = urllib.parse.urlparse(url)
    if parsed.scheme not in ('http', 'https') or not parsed.hostname:
        raise ValueError(f"无效的测速 URL: {url}")
    port = parsed.port or (443 if parsed.scheme == 'https' else 80)
    path = parsed.path or '/'
    if parsed.query:
        path += '?' + parsed.query
    return parsed.scheme, parsed.hostname, port, path


def _download_speed_direct(ip: str, size_bytes: int, connect_timeout: float = 3,
                           download_timeout: float = 5,
                           speed_url: str | None = None) -> tuple:
    """单个测速地址的 HTTPS 直连下载（自定义 SNI 指向测速服务器域名，绕过 CDN 对 SNI=IP 的拒绝）

    为什么不用 requests：requests/urllib3 的 SNI 取自 URL 的 host，
    直连 https://{ip} 时 SNI=IP 字面量会被 Cloudflare 边缘拒绝（SSLV3_ALERT_HANDSHAKE_FAILURE）。
    这里手写 socket+ssl，把 SNI 指向测速服务器域名（speed.cloudflare.com / httpbin.org 等），
    TCP 目标仍是被测 IP，从而真正测出该 IP 到本地的速度。

    Args:
        ip (str): 被测 IP
        size_bytes (int): 期望下载字节数
        connect_timeout (float): 连接超时（秒）
        download_timeout (float): 下载超时（秒）
        speed_url (str | None): 本次尝试的测速 URL（None = 使用配置的第一个地址）

    Returns:
        tuple: (速度Mbps, 延迟毫秒, 失败原因字符串) - 失败时速度/延迟为 0，原因用于 debug 诊断
    """
    try:
        if speed_url is None:
            urls = _get_speed_test_urls()
            speed_url = urls[0] if urls else 'https://speed.cloudflare.com/__down?bytes={size}'
        scheme, host, port, path = _parse_speed_test_url(speed_url, size_bytes)

        sock = socket.create_connection((ip, port), timeout=connect_timeout)
        if scheme == 'https':
            try:
                ctx = ssl.create_default_context()
                ctx.check_hostname = False
                ctx.verify_mode = ssl.CERT_NONE
                ssock = ctx.wrap_socket(sock, server_hostname=host)
            except Exception as e:
                try:
                    sock.close()
                except Exception:
                    pass
                return (0, 0, f"TLS握手失败: {str(e)[:40]}")
        else:
            ssock = sock

        start_total = time.time()
        req = (f"GET {path} HTTP/1.1\r\n"
               f"Host: {host}\r\n"
               f"User-Agent: Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36\r\n"
               f"Connection: close\r\n"
               f"Accept: */*\r\n\r\n")
        ssock.sendall(req.encode())

        # 读取响应头（限制大小防异常）
        buf = b''
        while b'\r\n\r\n' not in buf:
            chunk = ssock.recv(4096)
            if not chunk:
                break
            buf += chunk
            if len(buf) > 65536:
                break
        header, _, body = buf.partition(b'\r\n\r\n')
        status_line = header.split(b'\r\n')[0].decode(errors='replace')
        # 429 限流检测：触发全局暂停，本次测速失败，后续请求等待暂停结束自动恢复
        if b' 429 ' in header:
            try:
                ssock.close()
            except Exception:
                pass
            _rate_limit_pause()
            # 记录因 429 失败的 IP，暂停结束后由并发层自动重试
            with _RATE_LIMIT_LOCK:
                _RATE_LIMITED_IPS.add(ip)
            return (0, 0, f"HTTP {status_line}（已触发限流暂停）")
        if b' 200 ' not in header:
            try:
                ssock.close()
            except Exception:
                pass
            return (0, 0, f"HTTP {status_line}")

        # 首次字节耗时近似连接延迟
        latency = (time.time() - start_total) * 1000

        # 流式读取 body 计算速度
        ssock.settimeout(download_timeout)
        start_download = time.time()
        total = len(body)
        while total < size_bytes:
            if time.time() - start_download > download_timeout:
                break
            try:
                chunk = ssock.recv(8192)
            except socket.timeout:
                break
            if not chunk:
                break
            total += len(chunk)
        dur = time.time() - start_download
        try:
            ssock.close()
        except Exception:
            pass

        if dur > 0.05 and total > 0:
            mbps = (total * 8) / (dur * 1000000)
            return (mbps, latency, f"下载{total / 1024 / 1024:.1f}MB/{dur:.2f}s")
        return (0, 0, "无有效下载数据")
    except Exception as e:
        return (0, 0, f"连接失败: {str(e)[:40]}")


def test_ip_bandwidth_only(ip: str, current: int, total: int) -> tuple:
    """
    通过HTTPS直连下载测试IP带宽性能

    使用真实的HTTPS下载测试来测量IP的带宽性能：
    手写 socket+ssl 直连被测IP的端口（SNI 指向测速服务器域名），
    依次尝试配置的全部测速地址（speed_test_url 列表），
    通过下载指定大小的文件来评估网络速度。
    因 429 限流失败的 IP 会记录在案，暂停结束后由并发层自动重试。

    Args:
        ip (str): 要测试的IP地址
        current (int): 当前测试序号
        total (int): 总测试数量

    Returns:
        tuple: (是否成功, 带宽Mbps, 延迟毫秒) - (bool, float, float)
    """
    try:
        # 等待 429 限流暂停结束（暂停时间不占用单IP测速总超时）
        _wait_while_rate_limited()

        # 验证IP格式
        if not is_valid_ipv4(ip):
            return (False, 0, 0)

        # 总测试超时时间（秒）
        TOTAL_TIMEOUT = 15
        # 下载超时（秒）
        DOWNLOAD_TIMEOUT = 5

        start_total = time.time()
        test_size_bytes = CONFIG["bandwidth_test_size_mb"] * 1024 * 1024
        # HTTPS 直连被测IP测速（SNI 指向 speed.cloudflare.com），真正测出该IP到本地的速度
        # 不能用 http://{ip}：80端口明文HTTP在部分网络环境被阻断；不能 https://{ip}：SNI=IP 被 Cloudflare 拒绝

        best_speed = 0
        best_latency = 0
        test_count = CONFIG["bandwidth_test_count"]
        timed_out = False  # 总超时标记：超时视为失败，防止返回不可靠的部分数据速度
        last_reason = ''

        for test_attempt in range(test_count):
            # 检查总超时
            if time.time() - start_total > TOTAL_TIMEOUT:
                timed_out = True
                logger.debug(f"⏱️ IP {ip} 带宽测试总超时（>{TOTAL_TIMEOUT}s），放弃")
                break

            speed, latency, reason, was_rate_limited = _download_speed_try_all(
                ip, test_size_bytes, connect_timeout=3, download_timeout=DOWNLOAD_TIMEOUT)
            if speed <= 0:
                last_reason = reason
                if was_rate_limited:
                    # 429 限流失败由并发层在暂停结束后自动重试
                    logger.debug(f"🚦 [{current}/{total}] {ip} 第{test_attempt + 1}/{test_count}次测速因 429 失败，等待暂停结束后自动重试")
            if speed > best_speed:
                best_speed = speed
                if latency > 0:
                    best_latency = latency
            # 每次尝试输出明细（速度/失败原因），用于 DEBUG 诊断
            if speed > 0:
                logger.debug(f"⚡ [{current}/{total}] {ip} 第{test_attempt + 1}/{test_count}次测速成功: {speed:.2f}Mbps，延迟 {latency:.0f}ms（{reason}）")
            else:
                logger.debug(f"⚡ [{current}/{total}] {ip} 第{test_attempt + 1}/{test_count}次测速失败: {reason}")
            # 速度很好，提前结束测试
            if speed > 100:
                break

        if timed_out:
            # 总超时视为失败：部分下载数据计算出的速度不可靠，不能计入结果
            logger.debug(f"⏱️ [{current}/{total}] {ip} 带宽测试超时，标记失败")
            return (False, 0, 0)
        if best_speed > 0:
            # 测速成功：从 429 重试集合中移除（避免被重复重试）
            with _RATE_LIMIT_LOCK:
                _RATE_LIMITED_IPS.discard(ip)
            logger.debug(f"⚡ [{current}/{total}] {ip}（带宽综合速度：{best_speed:.2f}Mbps）")
            return (True, best_speed, best_latency)
        else:
            # 失败原因降为 debug，避免大量失败IP刷屏（汇总统计见并发完成日志）
            logger.debug(f"⚡ [{current}/{total}] {ip}（带宽测试失败: {last_reason or '未知原因'}）")
            return (False, 0, 0)
    except Exception as e:
        logger.error(f"IP {ip} 带宽测试异常: {str(e)[:50]}")
        return (False, 0, 0)

def test_bandwidth_concurrently(ips: list, max_workers: int | None = None) -> list:
    """
    并发带宽测试 - 使用线程池同时测试多个IP的带宽

    每个工作线程独立执行 HTTPS 直连测速（socket+ssl，无共享会话）。
    单IP内部仍有总超时/下载超时保护，防止卡住。

    Args:
        ips (list): IP列表，格式为[(ip, delay), ...]
        max_workers (int): 最大并发线程数，默认使用配置值

    Returns:
        list: 带宽测试结果，格式为[(ip, min_delay, avg_delay, bandwidth, latency, score), ...]
    """
    if max_workers is None:
        max_workers = CONFIG["bandwidth_workers"]

    logger.info(f"⚡ 开始并发带宽测试 {len(ips)} 个IP，使用 {max_workers} 个线程")
    logger.debug(f"⚡ 待测IP列表: {', '.join(ip for ip, _ in ips)}")
    bandwidth_results = []
    start_time = time.time()
    ip_to_delay = {ip: delay for ip, delay in ips}

    def _run_batch(batch_ips: list) -> list:
        """并发测速一批IP，返回通过测速的结果列表。"""
        results = []
        batch_total = len(batch_ips)

        def worker(item: tuple, idx: int) -> tuple | None:
            ip, delay = item
            is_fast, bandwidth, latency = test_ip_bandwidth_only(ip, idx + 1, batch_total)
            if is_fast:
                # 使用TCP Ping测试的延迟数据
                score = calculate_score(delay, delay, bandwidth, 100)
                return (ip, delay, delay, bandwidth, latency, score)
            return None

        try:
            with ThreadPoolExecutor(max_workers=max_workers) as executor:
                future_to_idx = {executor.submit(worker, item, idx): idx for idx, item in enumerate(batch_ips)}
                try:
                    # 批次级超时保护：单个IP最多耗时约15秒，这里给足余量避免误杀
                    for future in as_completed(future_to_idx, timeout=max(60, len(batch_ips) * 20)):
                        try:
                            result = future.result()
                            if result:
                                results.append(result)
                        except Exception as e:
                            logger.error(f"⚡ 带宽测试异常: {str(e)[:50]}")
                except TimeoutError:
                    logger.warning("⚠️ 带宽测试整体超过预期时间，跳过剩余任务")
                    for future in future_to_idx:
                        future.cancel()
        except Exception as e:
            logger.error(f"⚡ 并发带宽测试执行出错: {str(e)[:50]}")
        return results

    # 第一轮：测试全部IP
    bandwidth_results.extend(_run_batch(ips))

    # 429 限流重试：把因 429 失败的 IP 在暂停结束后自动重新测速
    retry_rounds = max(0, int(CONFIG.get('bandwidth_retry_rounds', 2)))
    for retry_round in range(retry_rounds):
        with _RATE_LIMIT_LOCK:
            pending_ips = sorted(_RATE_LIMITED_IPS)
        if not pending_ips:
            break
        _wait_while_rate_limited()  # 等待暂停结束再重试
        with _RATE_LIMIT_LOCK:
            # 等待期间可能有IP已通过其他尝试成功并移出集合，只重试仍被标记的
            pending_ips = sorted(_RATE_LIMITED_IPS)
            _RATE_LIMITED_IPS.clear()
        if not pending_ips:
            break
        logger.info(f"🚦 429 限流暂停结束，第 {retry_round + 1}/{retry_rounds} 轮重试 {len(pending_ips)} 个IP")
        retry_items = [(ip, ip_to_delay.get(ip, 0)) for ip in pending_ips]
        bandwidth_results.extend(_run_batch(retry_items))

    total_time = time.time() - start_time
    failed = len(ips) - len(bandwidth_results)
    logger.info(f"⚡ 并发带宽测试完成，{len(bandwidth_results)}/{len(ips)} 个IP通过"
                + (f"（失败 {failed} 个，可用 DEBUG 日志查看失败原因）" if failed else "")
                + f"，总耗时: {total_time:.1f}秒")
    return bandwidth_results


def calculate_score(min_delay: float, avg_delay: float, bandwidth: float, stability: float) -> float:
    """
    计算综合评分 - 结合延迟、带宽、稳定性
    
    根据IP的延迟、带宽和稳定性等指标计算综合评分，
    用于智能排序和推荐最佳IP。
    
    Args:
        min_delay (float): 最小延迟（毫秒）
        avg_delay (float): 平均延迟（毫秒）
        bandwidth (float): 带宽（Mbps）
        stability (float): 稳定性指标
    
    Returns:
        float: 综合评分（0-100分）
    """
    # 延迟评分 (0-40分) - 延迟越低分数越高
    if min_delay <= 50:
        delay_score = 40
    elif min_delay <= 100:
        delay_score = 35
    elif min_delay <= 200:
        delay_score = 30
    elif min_delay <= 300:
        delay_score = 25
    else:
        delay_score = max(0, 20 - (min_delay - 300) / 10)
    
    # 带宽评分 (0-30分) - 带宽越高分数越高
    if bandwidth >= 50:
        bandwidth_score = 30
    elif bandwidth >= 20:
        bandwidth_score = 25
    elif bandwidth >= 10:
        bandwidth_score = 20
    elif bandwidth >= 5:
        bandwidth_score = 15
    else:
        bandwidth_score = max(0, bandwidth * 3)
    
    # 稳定性评分 (0-30分) - 稳定性越高分数越高
    stability_score = min(30, stability * 0.3)
    
    # 综合评分
    total_score = delay_score + bandwidth_score + stability_score
    
    return round(total_score, 1)

def latency_filter_ips(ip_results: list, percentage: int = 30) -> list:
    """
    延迟排名筛选 - 取前N%的IP
    
    根据延迟性能对IP进行排名筛选，只保留延迟最低的前N%的IP，
    用于减少后续深度测试的工作量。
    
    Args:
        ip_results (list): IP测试结果列表，格式为[(ip, min_delay, avg_delay, stability), ...]
        percentage (int): 保留百分比，默认30%
    
    Returns:
        list: 筛选后的IP结果列表
    """
    if not ip_results:
        return []
    
    # 按延迟排序（min_delay，与展示字段保持一致）
    sorted_results = sorted(ip_results, key=lambda x: x[1])  # 按min_delay排序
    
    # 计算要保留的数量
    keep_count = max(1, int(len(sorted_results) * percentage / 100))
    
    # 显示筛选结果
    logger.info(f"🔍 延迟排名前{percentage}%筛选：从 {len(sorted_results)} 个IP中筛选出 {keep_count} 个IP")
    
    # 显示筛选结果（逐条排名降为 debug，避免刷屏）
    for i, (ip, min_delay, avg_delay, stability) in enumerate(sorted_results[:keep_count], 1):
        logger.debug(f"📊 {ip}（延迟排名第{i}位：{min_delay:.1f}ms）")
    
    return sorted_results[:keep_count]

def test_ip_availability(ip: str) -> tuple:
    """
    TCP Socket检测IP可用性 - 深度测试全部端口

    深度测试阶段遍历所有配置端口，取最小延迟作为综合延迟。

    Args:
        ip (str): 要测试的IP地址

    Returns:
        tuple: (是否可用, 延迟毫秒数) - (bool, int)
    """
    return tcp_connect_test(ip, CONFIG["test_ports"], timeout=1)


def quick_filter_ips_concurrently(ips: list, max_workers: int | None = None) -> list:
    """
    并发快速筛选IP - 使用线程池同时执行TCP连接测试。
    """
    if max_workers is None:
        max_workers = CONFIG["max_workers"]

    logger.info(f"🔍 开始并发快速筛选 {len(ips)} 个IP，使用 {max_workers} 个线程")
    filtered_results = []
    start_time = time.time()
    batch_size = max(1, CONFIG["batch_size"])

    for i in range(0, len(ips), batch_size):
        batch_ips = ips[i:i + batch_size]
        batch_num = i // batch_size + 1
        total_batches = (len(ips) - 1) // batch_size + 1

        logger.debug(f"🔍 处理快速筛选批次 {batch_num}/{total_batches}，包含 {len(batch_ips)} 个IP")

        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            future_to_ip = {executor.submit(quick_filter_ip, ip): ip for ip in batch_ips}
            for future in as_completed(future_to_ip):
                ip = future_to_ip[future]
                try:
                    is_good, delay = future.result()
                    if is_good:
                        filtered_results.append((ip, delay))
                except Exception as e:
                    logger.error(f"❌ 快速筛选异常 {ip}: {str(e)[:30]}")

        # 每个批次输出一条 INFO 汇总进度（逐IP明细为debug，避免刷屏）
        logger.info(f"🔍 批次 {batch_num}/{total_batches} 完成，累计可用 {len(filtered_results)} 个IP")

        if i + batch_size < len(ips):
            time.sleep(0.05)

    total_time = time.time() - start_time
    logger.info(f"🔍 并发快速筛选完成，保留 {len(filtered_results)} 个IP，总耗时: {total_time:.1f}秒")
    return filtered_results

# ===== 地区识别模块 =====
# IP地理位置识别功能，支持多API源和智能缓存

def get_ip_region(ip: str) -> str:
    """
    优化的IP地区识别（支持缓存TTL）
    
    通过多个API源查询IP的地理位置信息，支持智能缓存机制，
    避免重复查询，提升查询效率。
    
    Args:
        ip (str): 要查询的IP地址
    
    Returns:
        str: 国家代码（如'US', 'CN', 'JP'等）
    """
    # 检查缓存是否有效
    if ip in region_cache:
        cached_data = region_cache[ip]
        if isinstance(cached_data, dict) and 'timestamp' in cached_data:
            if is_cache_valid(cached_data['timestamp'], CONFIG["cache_ttl_hours"]):
                region = cached_data.get('region', '')
                # 校验：国家代码应为 2~3 位大写字母（如 US、SGP）；
                # 若存的是完整国家名（如 UNITED STATES），视为污染数据，跳过并重新查询
                if region.isalpha() and 2 <= len(region) <= 3:
                    logger.debug(f"📦 IP {ip} 地区信息从缓存获取: {region}")
                    return region
                logger.warning(f"⚠️ IP {ip} 缓存地区数据异常({region!r})，重新查询")
        else:
            # 兼容旧格式缓存（旧格式直接存地区代码字符串）
            if isinstance(cached_data, str) and 2 <= len(cached_data) <= 3 and cached_data.isalpha():
                logger.debug(f"📦 IP {ip} 地区信息从缓存获取（旧格式）: {cached_data}")
                return cached_data
            logger.warning(f"⚠️ IP {ip} 缓存地区数据异常({cached_data!r})，重新查询")
    
    # 尝试主要API（免费版本）
    logger.debug(f"🌐 IP {ip} 开始API查询（主要API: ipinfo.io lite）...")
    try:
        resp = session.get(f'https://api.ipinfo.io/lite/{ip}?token=2cb674df499388', timeout=CONFIG["api_timeout"])
        if resp.status_code == 200:
            data = resp.json()
            # ipinfo lite 返回 country_code（如 US），country 是完整国家名（如 United States）
            country_code = data.get('country_code', '').upper()
            if country_code and 2 <= len(country_code) <= 3:
                region_cache[ip] = {
                    'region': country_code,
                    'timestamp': datetime.now().isoformat()
                }
                logger.debug(f"✅ IP {ip} 主要API识别成功: {country_code}（来源：API查询）")
                return country_code
        else:
            logger.warning(f"⚠️ IP {ip} 主要API返回状态码: {resp.status_code}")
    except Exception as e:
        logger.error(f"❌ IP {ip} 主要API识别失败: {str(e)[:30]}")
        pass
    
    # 尝试备用API
    logger.debug(f"🌐 IP {ip} 尝试备用API（ip-api.com）...")
    try:
        resp = session.get(f'http://ip-api.com/json/{ip}?fields=status,countryCode', timeout=CONFIG["api_timeout"])
        data = resp.json()
        if resp.status_code == 200 and data.get('status') == 'success':
            country_code = data.get('countryCode', '').upper()
            if country_code:
                region_cache[ip] = {
                    'region': country_code,
                    'timestamp': datetime.now().isoformat()
                }
                logger.debug(f"✅ IP {ip} 备用API识别成功: {country_code}")
                return country_code
        else:
            logger.warning(f"⚠️ IP {ip} 备用API返回状态: {data.get('status', 'unknown')}")
    except Exception as e:
        logger.error(f"❌ IP {ip} 备用API识别失败: {str(e)[:30]}")
        pass
    
    # 失败返回Unknown
    logger.warning(f"❌ IP {ip} 所有API识别失败，标记为Unknown")
    region_cache[ip] = {
        'region': 'Unknown',
        'timestamp': datetime.now().isoformat()
    }
    return 'Unknown'

def get_country_name(code: str) -> str:
    """
    根据国家代码获取中文名称
    
    将ISO国家代码转换为中文名称，用于用户友好的显示。
    
    Args:
        code (str): 国家代码（如'US', 'CN'等）
    
    Returns:
        str: 中文国家名称
    """
    return COUNTRY_MAPPING.get(code, code)

# ===== 并发处理模块 =====
# 高并发网络测试功能，支持多线程并发处理

def test_ips_concurrently(ips: list, max_workers: int | None = None) -> list:
    """
    超快并发检测IP可用性（防卡住优化）
    
    使用ThreadPoolExecutor实现并发处理，大幅提升检测效率。
    支持批量处理和批次级超时保护，避免程序卡住。
    
    Args:
        ips (list): 要测试的IP地址列表
        max_workers (int): 最大并发线程数，默认使用配置值
    
    Returns:
        list: 可用IP列表，格式为[(ip, delay), ...]
    """
    if max_workers is None:
        max_workers = CONFIG["max_workers"]
    
    logger.info(f"📡 开始并发检测 {len(ips)} 个IP，使用 {max_workers} 个线程")
    available_ips = []
    
    # 使用更小的批次，避免卡住
    batch_size = CONFIG["batch_size"]  # 使用配置的批次大小
    start_time = time.time()
    
    for i in range(0, len(ips), batch_size):
        batch_ips = ips[i:i+batch_size]
        batch_num = i//batch_size + 1
        total_batches = (len(ips)-1)//batch_size + 1
        
        logger.debug(f"📡 处理批次 {batch_num}/{total_batches}，包含 {len(batch_ips)} 个IP")
        
        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            # 提交批次任务，添加超时保护
            future_to_ip = {executor.submit(test_ip_availability, ip): ip for ip in batch_ips}
            
            try:
                # 批次级超时保护：防止个别IP长时间阻塞整个流程
                batch_completed = 0  # 当前批次内已完成数（修复跨批次计数溢出）
                for future in as_completed(future_to_ip, timeout=30):
                    ip = future_to_ip[future]
                    batch_completed += 1
                    completed = i + batch_completed  # 批次起点 + 批次内已完成数
                    elapsed = time.time() - start_time
                    
                    try:
                        is_available, delay = future.result()
                        if is_available:
                            available_ips.append((ip, delay))
                            logger.debug(f"🎯 [{completed}/{len(ips)}] {ip}（TCP Ping 综合延迟：{delay:.1f}ms）")
                        else:
                            logger.debug(f"[{completed}/{len(ips)}] {ip} ❌ 不可用 - 总耗时: {elapsed:.1f}s")
                    except Exception as e:
                        logger.error(f"[{completed}/{len(ips)}] {ip} ❌ 检测出错: {str(e)[:30]} - 总耗时: {elapsed:.1f}s")
            except TimeoutError:
                logger.warning(f"⚠️ 批次 {batch_num}/{total_batches} 超过30秒未完成，跳过剩余任务")
                for future in future_to_ip:
                    future.cancel()
        
        # 每个批次输出一条 INFO 进度，避免长时间无反馈（逐IP明细为debug）
        logger.info(f"📡 批次 {batch_num}/{total_batches} 完成，累计可用 {len(available_ips)} 个IP")
        
        # 批次间短暂休息，避免过度占用资源
        if i + batch_size < len(ips):
            time.sleep(0.2)  # 减少休息时间
    
    total_time = time.time() - start_time
    logger.info(f"📡 并发检测完成，发现 {len(available_ips)} 个可用IP，总耗时: {total_time:.1f}秒")
    return available_ips

def get_regions_concurrently(ips: list, max_workers: int | None = None) -> list:
    """
    并发识别IP地理位置（优化版：O(n) 复杂度）

    使用多线程并发查询IP的地理位置信息。通过 as_completed 直接消费
    结果，避免 O(n²) 的嵌套查找，提升大列表场景下的效率。

    Args:
        ips (list): IP地址列表，格式为[(ip, min_delay, avg_delay), ...]
        max_workers (int): 最大并发线程数，默认使用配置值

    Returns:
        list: 地区识别结果，格式为[(ip, region_code, min_delay, avg_delay), ...]
    """
    if max_workers is None:
        max_workers = CONFIG["region_workers"]

    logger.info(f"🌍 开始并发地区识别 {len(ips)} 个IP，使用 {max_workers} 个线程")
    results = []
    start_time = time.time()

    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        # 提交所有任务，用 (ip, min_delay, avg_delay) 元组作为键，避免重复IP冲突
        future_to_key = {executor.submit(get_ip_region, ip): (ip, min_delay, avg_delay) for ip, min_delay, avg_delay in ips}

        # 通过 as_completed 直接消费结果，O(n) 复杂度
        completed = 0
        for future in as_completed(future_to_key):
            ip, min_delay, avg_delay = future_to_key[future]
            completed += 1
            try:
                region_code = future.result()
                results.append((ip, region_code, min_delay, avg_delay))
            except Exception as e:
                logger.warning(f"地区识别失败 {ip}: {str(e)[:50]}")
                results.append((ip, 'Unknown', min_delay, avg_delay))
            # 每完成 50 个输出一条 INFO 进度，避免长时间无反馈（逐IP明细为debug）
            if completed % 50 == 0 or completed == len(ips):
                logger.info(f"🌍 地区识别进度: {completed}/{len(ips)}")

    # 按原顺序输出（保持日志可读性）
    order = {tup: idx for idx, tup in enumerate(ips)}
    results.sort(key=lambda r: order.get((r[0], r[2], r[3]), 0))
    for i, (ip, region_code, min_delay, avg_delay) in enumerate(results, 1):
        logger.debug(f"📦 [{i}/{len(ips)}] {ip} -> {region_code}")

    total_time = time.time() - start_time
    logger.info(f"🌍 地区识别完成，处理了 {len(results)} 个IP，总耗时: {total_time:.1f}秒")
    return results

# ===== 主程序模块 =====
# 程序主流程控制，协调各个模块完成IP采集、检测、排序和输出

def main() -> None:
    """
    主程序入口
    
    执行完整的IP采集、检测、排序和输出流程：
    1. 采集IP地址
    2. 快速筛选
    3. 地区识别
    4. 深度测试（高级模式）
    5. 结果输出
    
    Returns:
        None: 程序执行完成后退出
    """
    start_time = time.time()
    
    # 1. 预处理：删除旧文件
    # 清理之前运行生成的结果文件，避免结果累积
    delete_file_if_exists(FILE_BASIC_IP)
    delete_file_if_exists(FILE_BASIC_REGION)
    if CONFIG["advanced_mode"]:
        delete_file_if_exists(FILE_PRO_IP)
        delete_file_if_exists(FILE_PRO_REGION)
        delete_file_if_exists(FILE_RANKING)
    # 地区定向扫描旧文件（Region-*.txt + 汇总），仅开启时清理
    if CONFIG.get('region_targets'):
        for old_file in glob.glob(f'{FILE_REGION_PREFIX}*.txt'):
            delete_file_if_exists(old_file)
        delete_file_if_exists(FILE_REGION_ALL)
    logger.info("🗑️ 预处理完成，旧文件已清理")

    # 2. 采集IP地址
    # 从多个API源并发采集IP地址，获取大量候选IP
    logger.info("📥 ===== 采集IP地址 =====")
    all_ips = []
    successful_sources = 0
    failed_sources = 0
    
    # 采集IP源
    for i, url in enumerate(CONFIG["ip_sources"]):
        try:
            logger.info(f"🔍 从 {url} 采集...")
            # 添加请求间隔，避免频率限制
            if i > 0:
                time.sleep(CONFIG["query_interval"])  # 使用配置的间隔时间
            if CONFIG.get("use_proxy_for_collection", True) and PROXY_ENABLED:
                logger.info("🔐 采集阶段使用代理")
            resp = collection_session.get(url, timeout=CONFIG["timeout"])  # 使用配置的超时时间
            logger.debug(f"🔍 {url} 响应状态 {resp.status_code}，内容 {len(resp.text)} 字节")
            if resp.status_code == 200:
                # 提取并验证IPv4地址
                ips = re.findall(r'\b(?:[0-9]{1,3}\.){3}[0-9]{1,3}\b', resp.text)
                valid_ips = [ip for ip in ips if is_valid_ipv4(ip)]
                
                # 调试信息：记录原始找到的IP数量
                if len(ips) > 0 and len(valid_ips) == 0:
                    logger.debug(f"从 {url} 找到 {len(ips)} 个IP，但验证后为0个")
                
                # 如果正则表达式没有找到IP，尝试按行分割查找
                if len(valid_ips) == 0:
                    lines = resp.text.strip().split('\n')
                    for line in lines:
                        line = line.strip()
                        # 检查是否是纯IP地址行
                        if re.match(r'^(?:[0-9]{1,3}\.){3}[0-9]{1,3}$', line) and is_valid_ipv4(line):
                            valid_ips.append(line)
                
                all_ips.extend(valid_ips)
                successful_sources += 1
                logger.info(f"✅ 成功采集 {len(valid_ips)} 个有效IP地址")
                if valid_ips:
                    preview = ', '.join(valid_ips[:10]) + (' ...' if len(valid_ips) > 10 else '')
                    logger.debug(f"🔍 {url} 提取到 {len(valid_ips)} 个IP: {preview}")
            elif resp.status_code == 403:
                failed_sources += 1
                logger.warning(f"⚠️ 被限制访问（状态码 403），跳过此源")
            else:
                failed_sources += 1
                logger.warning(f"❌ 失败（状态码 {resp.status_code}）")
        except Exception as e:
            failed_sources += 1
            error_msg = str(e)[:50]
            logger.error(f"❌ 出错: {error_msg}")
    
    logger.info(f"📊 采集统计: 成功 {successful_sources} 个源，失败 {failed_sources} 个源")

    # 2.5 IP段扫描（可选，由 cidr_scan_enabled 开关控制）：从 CIDR 段源采集网段并采样 IP
    # 与 ip_sources（具体IP列表）互补，可发现未被公开列表覆盖的 Cloudflare IP
    if CONFIG.get('cidr_scan_enabled', False):
        cidr_sources = CONFIG.get('ips_sources') or []
        if cidr_sources:
            logger.info("📥 ===== 采集IP段（CIDR扫描） =====")
            cidr_ips = collect_cidr_ips(cidr_sources, CONFIG.get('cidr_ips_per_segment', 10))
            logger.info(f"🔢 IP段扫描共采样 {len(cidr_ips)} 个IP地址")
            all_ips.extend(cidr_ips)
        else:
            logger.warning("⚠️ cidr_scan_enabled 已开启但未配置 ips_sources，跳过IP段扫描")
    else:
        logger.info("ℹ️ IP段扫描已关闭（cidr_scan_enabled=false），如需扫描 Cloudflare 官方网段请开启并配置 ips_sources")

    # 3. IP去重与排序
    # 对采集到的IP进行去重和排序，确保唯一性
    unique_ips = sorted(list(set(all_ips)), key=lambda x: [int(p) for p in x.split('.')])
    logger.info(f"🔢 去重后共 {len(unique_ips)} 个唯一IP地址")
    
    # 检查是否有IP需要检测
    if not unique_ips:
        logger.warning("⚠️ 没有采集到任何IP地址，程序结束")
        return

    # 4. 快速筛选
    # 使用TCP连接测试快速剔除明显不可用的IP，减少后续测试工作量
    logger.info("🔍 ===== 快速筛选 =====")
    quick_filter_results = quick_filter_ips_concurrently(unique_ips)
    filtered_ips = [ip for ip, _ in quick_filter_results]
    
    logger.info(f"🔍 快速筛选完成，保留 {len(filtered_ips)} 个IP")
    
    if not filtered_ips:
        logger.warning("⚠️ 快速筛选后无可用IP，程序结束")
        return

    # 5. 立即保存基础文件（快速筛选完成后）
    # 保存基础版IP列表，供用户快速使用
    logger.info("📄 ===== 保存基础文件 =====")
    with open(FILE_BASIC_IP, 'w', encoding='utf-8') as f:
        for ip in filtered_ips:
            f.write(f"{ip}\n")
    logger.info(f"📄 已保存 {len(filtered_ips)} 个可用IP到 {FILE_BASIC_IP}")
    preview = ', '.join(filtered_ips[:10]) + (' ...' if len(filtered_ips) > 10 else '')
    logger.debug(f"📄 {FILE_BASIC_IP} 内容预览: {preview}")
    
    # 6. 立即进行地区识别与结果格式化（提前保存Senflare.txt）
    # 对快速筛选的IP进行地区识别，生成格式化结果
    logger.info("🌍 ===== 并发地区识别与结果格式化 =====")
    # 使用快速筛选的IP进行地区识别
    ip_delay_data = [(ip, 0, 0) for ip in filtered_ips]  # 使用快速筛选的IP，延迟设为0
    
    region_results = get_regions_concurrently(ip_delay_data)
    
    # 按地区分组
    region_groups = defaultdict(list)
    for ip, region_code, min_delay, avg_delay in region_results:
        country_name = get_country_name(region_code)
        region_groups[country_name].append((ip, region_code, min_delay, avg_delay))
    
    logger.info(f"🌍 地区分组完成，共 {len(region_groups)} 个地区")
    
    # 生成并保存最终结果
    result = []
    for region in sorted(region_groups.keys()):
        # 同一地区内按延迟排序（更快的在前）
        sorted_ips = sorted(region_groups[region], key=lambda x: x[2])  # 按min_delay排序
        for idx, (ip, code, min_delay, avg_delay) in enumerate(sorted_ips, 1):
            result.append(f"{ip}#{code} {region}节点 | {idx:02d}")
        logger.debug(f"地区 {region} 格式化完成，包含 {len(sorted_ips)} 个IP")
    
    if result:
        # 立即保存基础文件
        with open(FILE_BASIC_REGION, 'w', encoding='utf-8') as f:
            f.write('\n'.join(result))
        logger.info(f"📄 已保存 {len(result)} 条格式化记录到 {FILE_BASIC_REGION}")
    else:
        logger.warning("⚠️ 无有效记录可保存")

    # ===== 高级模式：延迟筛选 + TCP Ping + 带宽测试 + 评分输出 =====
    if not CONFIG["advanced_mode"]:
        logger.info("ℹ️ 高级模式未启用，跳过深度测试（延迟筛选/TCP Ping/带宽/评分）")
    else:
        # 7. 延迟排名前N%筛选（基于快速筛选结果）
        # 根据延迟性能筛选出前N%的IP，用于后续深度测试
        logger.info(f"🔍 ===== 延迟排名前{CONFIG['latency_filter_percentage']}%筛选 =====")
        # 对快速筛选的IP进行延迟排名筛选，使用快速筛选的实际延迟数据
        latency_source = [
            (ip, delay, delay, 0)  # (ip, min_delay, avg_delay, stability)
            for ip, delay in quick_filter_results
        ]

        latency_filtered_ips = latency_filter_ips(latency_source, CONFIG["latency_filter_percentage"])
        logger.info(f"🔍 延迟筛选完成，保留 {len(latency_filtered_ips)} 个IP")

        # 8. TCP Ping测试（只测试延迟，不测试带宽）
        # 对筛选后的IP进行精确的TCP延迟测试
        logger.info("🔍 ===== TCP Ping测试 =====")
        tcp_ping_ips = test_ips_concurrently([ip for ip, _, _, _ in latency_filtered_ips])

        # 9. 带宽测试（只对筛选后的IP进行带宽测试）
        # 对通过延迟筛选的IP进行HTTP带宽测试，评估网络性能
        logger.info("🔍 ===== 带宽测试 =====")
        available_ips = test_bandwidth_concurrently(tcp_ping_ips)

        # 10. 保存高级文件（按评分排序）
        # 生成高级版IP列表和详细排名信息
        if available_ips:
            # 按评分排序
            available_ips.sort(key=lambda x: x[5], reverse=True)  # 按评分排序
            logger.info(f"📊 按综合评分排序完成")
            
            # 保存优选IP列表
            with open(FILE_PRO_IP, 'w', encoding='utf-8') as f:
                for ip, min_delay, avg_delay, bandwidth, latency, score in available_ips:
                    f.write(f"{ip}\n")
            logger.info(f"📄 已保存 {len(available_ips)} 个优选IP到 {FILE_PRO_IP}")
            
            # 保存详细排名信息
            with open(FILE_RANKING, 'w', encoding='utf-8') as f:
                for i, (ip, min_delay, avg_delay, bandwidth, latency, score) in enumerate(available_ips, 1):
                    f.write(f"📊 [{i}/{len(available_ips)}] {ip}（延迟 {min_delay}ms，带宽 {bandwidth:.2f}Mbps，评分 {score:.1f}）\n")
            logger.info(f"📄 已保存排名详情到 {FILE_RANKING}")
            
            # 保存高级格式化文件（使用优选IP重新生成）
            # 对优选IP进行地区识别，生成高级版格式化结果
            logger.info("🌍 ===== 高级地区识别与结果格式化 =====")
            # 构建 IP -> 带宽 映射（用于格式化时显示 Mbps 并按速度排序）
            bandwidth_map = {ip: bandwidth for ip, _, _, bandwidth, _, _ in available_ips}
            pro_ip_delay_data = [(ip, 0, 0) for ip, _, _, _, _, _ in available_ips]
            pro_region_results = get_regions_concurrently(pro_ip_delay_data)
            
            # 按地区分组（附带带宽信息）
            pro_region_groups = defaultdict(list)
            for ip, region_code, min_delay, avg_delay in pro_region_results:
                country_name = get_country_name(region_code)
                pro_region_groups[country_name].append((ip, region_code, min_delay, avg_delay, bandwidth_map.get(ip, 0)))
            
            logger.info(f"🌍 高级地区分组完成，共 {len(pro_region_groups)} 个地区")
            
            # 生成高级格式化结果：速度快的排在前面
            # 地区内按带宽降序，地区之间按组内最高带宽降序（整个文件从快到慢）
            pro_result = []
            for region, items in sorted(pro_region_groups.items(),
                                        key=lambda kv: max(x[4] for x in kv[1]),
                                        reverse=True):
                # 同一地区内按带宽降序（速度更快的在前）
                sorted_ips = sorted(items, key=lambda x: x[4], reverse=True)  # 按带宽排序
                for idx, (ip, code, min_delay, avg_delay, bandwidth) in enumerate(sorted_ips, 1):
                    pro_result.append(f"{ip}#{code} {region}节点 | {bandwidth:.0f}Mbps | {idx:02d}")
                logger.debug(f"高级地区 {region} 格式化完成，包含 {len(sorted_ips)} 个IP")
            
            if pro_result:
                with open(FILE_PRO_REGION, 'w', encoding='utf-8') as f:
                    f.write('\n'.join(pro_result))
                logger.info(f"📄 已保存 {len(pro_result)} 条高级格式化记录到 {FILE_PRO_REGION}")
            else:
                logger.warning("⚠️ 高级版无有效记录可保存")
        else:
            logger.warning("⚠️ 高级版无有效记录可保存")

    # 10.5 地区定向扫描（可选）：按国家/地区筛选，确保每个地区凑够指定数量的有效节点
    # 参考 CloudflareSpeedTest 的 IP段扫描+测速思路（其本身不支持按国家筛选，这里内置整合）
    region_targets = CONFIG.get('region_targets') or []
    if region_targets:
        region_results = scan_region_targets(
            region_targets,
            CONFIG.get('region_target_count', 10),
            CONFIG.get('region_valid_max_delay', 300),
            CONFIG.get('region_valid_min_bandwidth', 5),
            CONFIG.get('region_scan_per_segment', 50),
            CONFIG.get('region_max_rounds', 3),
            existing_ips=filtered_ips,
        )
        save_region_results(region_results)
    else:
        logger.info("ℹ️ 地区定向扫描未开启（region_targets 为空数组），如需按国家地区筛选请配置 region_targets")

    # 11. 显示统计信息
    # 显示运行统计信息（缓存由入口finally统一保存）
    run_time = round(time.time() - start_time, 2)
    logger.info(f"⏱️ 总耗时: {run_time}秒")
    logger.info(f"📊 缓存统计: 总计 {len(region_cache)} 个")
    logger.info("🏁 ===== 程序完成 =====")

# ===== 程序入口 =====
# 程序启动入口，初始化缓存并执行主程序

if __name__ == "__main__":
    """
    程序启动入口
    
    初始化缓存系统，执行主程序流程，处理异常情况。
    支持用户中断和异常处理。
    """
    # 程序启动日志
    logger.info("🚀 ===== 开始IP处理程序 =====")
    
    # 初始化缓存系统
    load_region_cache()
    
    # 清理过期缓存条目
    clean_expired_cache()
    
    # 执行主程序流程（finally确保缓存始终保存，防止中断时丢失新查询结果）
    try:
        main()
    except KeyboardInterrupt:
        logger.info("⏹️ 程序被用户中断")
    except Exception as e:
        logger.error(f"❌ 程序运行出错: {str(e)}")
    finally:
        # 兜底保存缓存：无论正常结束、中断还是异常，都保留已查询的地区信息
        try:
            save_region_cache()
        except Exception as e:
            logger.error(f"❌ 兜底保存缓存失败: {str(e)[:50]}")
