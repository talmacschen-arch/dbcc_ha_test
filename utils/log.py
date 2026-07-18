"""日志 + 计时工具"""

import logging
import os
import time
from datetime import datetime
from config import LOG_DIR


def setup_logger(name="ha_test", scenario=None):
    """创建 logger，同时输出到控制台和文件"""
    logger = logging.getLogger(name)
    if logger.handlers:
        return logger

    logger.setLevel(logging.DEBUG)

    os.makedirs(LOG_DIR, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    suffix = f"_{scenario}" if scenario else ""
    log_file = os.path.join(LOG_DIR, f"ha_test{suffix}_{ts}.log")

    # 文件: DEBUG
    fh = logging.FileHandler(log_file, encoding="utf-8")
    fh.setLevel(logging.DEBUG)
    fh.setFormatter(logging.Formatter("[%(asctime)s] [%(levelname)s] %(message)s"))

    # 控制台: INFO
    ch = logging.StreamHandler()
    ch.setLevel(logging.INFO)
    ch.setFormatter(logging.Formatter("[%(asctime)s] [%(levelname)s] %(message)s", datefmt="%H:%M:%S"))

    logger.addHandler(fh)
    logger.addHandler(ch)
    return logger


class Timer:
    """计时上下文管理器"""

    def __init__(self, label=""):
        self.label = label
        self.elapsed = 0.0

    def __enter__(self):
        self._start = time.time()
        return self

    def __exit__(self, *args):
        self.elapsed = time.time() - self._start

    def __str__(self):
        return f"{self.label}: {self.elapsed:.1f}s"
