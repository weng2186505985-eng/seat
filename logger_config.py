
import logging
import os
import sys
from logging.handlers import TimedRotatingFileHandler
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

    # 文件输出：每日零点切割
    import datetime
    today = datetime.datetime.now().strftime("%Y-%m-%d")
    file_path = os.path.join(log_dir, f"seat_{today}.log")
    
    # 检查是否已经存在同名 handler，避免重复添加
    if not any(isinstance(h, TimedRotatingFileHandler) and f"seat_" in h.baseFilename for h in root_logger.handlers):
        # when="midnight" 表示每天零点切割
        file_handler = TimedRotatingFileHandler(
            file_path,
            when="midnight",
            interval=1,
            backupCount=30,
            encoding="utf-8"
        )
        # 设置旋转后的文件名格式为 seat_YYYY-MM-DD.log
        file_handler.suffix = "%Y-%m-%d"
        file_handler.setFormatter(formatter)
        root_logger.addHandler(file_handler)

    # 为所有已有的 handler 设置新的 Formatter (包括 GUI 的)
    for handler in root_logger.handlers:
        handler.setFormatter(formatter)

    # 错误日志也改为按天切割
    error_path = os.path.join(log_dir, "error.log")
    if not any(isinstance(h, TimedRotatingFileHandler) and h.baseFilename.endswith("error.log") for h in root_logger.handlers):
        error_handler = TimedRotatingFileHandler(
            error_path,
            when="midnight",
            interval=1,
            backupCount=30,
            encoding="utf-8"
        )
        error_handler.setLevel(logging.ERROR)
        error_handler.setFormatter(formatter)
        root_logger.addHandler(error_handler)

    logging.info("Full-link logging system initialized.")
