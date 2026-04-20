
import logging
import os
import sys
from logging.handlers import RotatingFileHandler
import threading

# 使用 thread-local 存储 trace_id，实现全链路追踪
_context = threading.local()

def set_trace_id(trace_id):
    _context.trace_id = trace_id

def get_trace_id():
    return getattr(_context, 'trace_id', 'SYSTEM')

class TraceFormatter(logging.Formatter):
    """自定义格式化器，自动注入 [TraceID]"""
    def format(self, record):
        record.trace_id = get_trace_id()
        return super().format(record)

def setup_logging(level=logging.INFO):
    log_dir = "logs"
    if not os.path.exists(log_dir):
        os.makedirs(log_dir)

    # 移除重构逻辑，改用更兼容的日志输出
    log_format = "%(asctime)s [%(levelname)s] [%(trace_id)s] %(name)s: %(message)s"
    date_format = "%Y-%m-%d %H:%M:%S"
    
    formatter = TraceFormatter(log_format, date_format)

    # 根日志记录器
    root_logger = logging.getLogger()
    root_logger.setLevel(level)
    
    # 控制台输出 (如果还没有的话)
    if not any(isinstance(h, logging.StreamHandler) for h in root_logger.handlers):
        console_handler = logging.StreamHandler(sys.stdout)
        console_handler.setFormatter(formatter)
        root_logger.addHandler(console_handler)

    # 文件输出 (按大小滚动)
    file_path = os.path.join(log_dir, "seat.log")
    if not any(isinstance(h, RotatingFileHandler) and h.baseFilename.endswith("seat.log") for h in root_logger.handlers):
        file_handler = RotatingFileHandler(
            file_path,
            maxBytes=10*1024*1024, # 10MB
            backupCount=5,
            encoding="utf-8"
        )
        file_handler.setFormatter(formatter)
        root_logger.addHandler(file_handler)

    # 为所有已有的 handler 设置新的 Formatter (包括 GUI 的)
    for handler in root_logger.handlers:
        handler.setFormatter(formatter)

    # 也可以增加一个单独的错误日志文件
    error_handler = RotatingFileHandler(
        os.path.join(log_dir, "error.log"),
        maxBytes=5*1024*1024,
        backupCount=3,
        encoding="utf-8"
    )
    error_handler.setLevel(logging.ERROR)
    error_handler.setFormatter(formatter)
    root_logger.addHandler(error_handler)

    logging.info("Full-link logging system initialized.")
