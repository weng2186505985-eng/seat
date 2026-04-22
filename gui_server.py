
import uvicorn
from fastapi import FastAPI, BackgroundTasks
from fastapi.responses import HTMLResponse, FileResponse
from pydantic import BaseModel
from typing import Optional
import logging
import time
import threading
from task_manager import TaskManager
from snatcher import UltraFastBot
from contextlib import asynccontextmanager
import logger_config
from collections import deque

# --- 全局变量 ---
tm: TaskManager = None

# --- 日志中转系统 ---
class LogQueueHandler(logging.Handler):
    def __init__(self, maxlen=200):
        super().__init__()
        # 使用 deque 实现线程安全的列表操作
        self.logs = deque(maxlen=maxlen)
        
    def emit(self, record):
        if record.name.startswith("uvicorn"):
            return
        try:
            # deque.append 是线程安全的
            self.logs.append({
                "time": time.strftime("%H:%M:%S"),
                "msg": self.format(record),
                "level": record.levelname.lower()
            })
        except: pass

log_handler = LogQueueHandler()
log_handler.setFormatter(logging.Formatter('%(message)s'))

@asynccontextmanager
async def lifespan(app: FastAPI):
    global tm
    logger_config.setup_logging()
    logging.getLogger().addHandler(log_handler)
    
    tm = TaskManager()
    tm.start()
    yield

app = FastAPI(lifespan=lifespan)

class TaskItem(BaseModel):
    username: str
    password: str
    floor: str
    seatRange: str
    startTime: str
    endTime: str
    dateOffset: int
    triggerTime: str
    recurring: Optional[bool] = False

@app.get("/", response_class=HTMLResponse)
async def get_index():
    return FileResponse("index.html")

@app.get("/get_logs")
async def get_logs(last_index: int = 0):
    # 将 deque 转换为 list 返回
    all_logs = list(log_handler.logs)
    total_len = len(all_logs)
    if last_index > total_len: last_index = 0
    new_logs = all_logs[last_index:total_len]
    return {"logs": new_logs, "last_index": total_len}

@app.get("/history_logs")
async def get_history_logs(date: str):
    """获取指定日期的历史日志"""
    log_dir = "logs"
    # 今天的情况
    today = time.strftime("%Y-%m-%d")
    if date == today:
        file_path = os.path.join(log_dir, "seat.log")
    else:
        # TimedRotatingFileHandler 默认格式为 seat.log.YYYY-MM-DD
        file_path = os.path.join(log_dir, f"seat.log.{date}")
    
    if not os.path.exists(file_path):
        return {"logs": [], "error": "日志文件不存在"}
    
    try:
        with open(file_path, "r", encoding="utf-8") as f:
            lines = f.readlines()
            # 仅返回最后 1000 行以防文件过大
            lines = lines[-1000:]
            formatted_logs = []
            for line in lines:
                # 尝试解析格式: 2026-04-21 20:13:51 [INFO] [SYSTEM] ...
                parts = line.split(" ", 2)
                if len(parts) >= 3:
                    time_str = parts[1]
                    rest = parts[2]
                    level = "info"
                    if "[ERROR]" in rest: level = "error"
                    elif "[WARNING]" in rest: level = "warning"
                    elif "[INFO]" in rest: level = "info"
                    
                    # 简化显示消息内容
                    msg_parts = rest.split("] ", 2)
                    msg = msg_parts[-1] if len(msg_parts) > 1 else rest
                    
                    formatted_logs.append({
                        "time": time_str,
                        "msg": msg.strip(),
                        "level": level
                    })
            return {"logs": formatted_logs}
    except Exception as e:
        return {"logs": [], "error": str(e)}

@app.get("/tasks")
async def list_tasks():
    if not tm: return []
    try:
        clean_tasks = []
        # 直接遍历，不加锁，尽量减少阻塞
        for t in list(tm.tasks):
            c = t.copy()
            if 'bot_instance' in c: del c['bot_instance']
            clean_tasks.append(c)
        return clean_tasks
    except:
        return []

@app.post("/add_task")
async def add_task(data: TaskItem):
    if not tm: return {"status": "error", "message": "系统未就绪"}
    task_id = tm.add_task(data.dict())
    if task_id: return {"status": "added", "id": task_id}
    return {"status": "error", "message": "解析失败"}

@app.post("/delete_task/{task_id}")
async def delete_task(task_id: str):
    if tm: tm.delete_task(task_id)
    return {"status": "deleted"}

@app.post("/book_now")
async def book_now(data: TaskItem):
    def _run():
        try:
            bot = UltraFastBot()
            seat_list = tm._build_seat_list(data.floor, data.seatRange)
            params = {
                "username": data.username, "password": data.password,
                "floor": data.floor, "seat_list": seat_list,
                "date_offset": data.dateOffset, "start_time": data.startTime, "end_time": data.endTime
            }
            bot.snatch_action(params)
        except Exception as e:
            logging.error(f"立即抢座执行异常: {e}")
    threading.Thread(target=_run, daemon=True).start()
    return {"status": "processing"}

if __name__ == "__main__":
    print("\n[Start] HDU Task System V4.2 Started!")
    uvicorn.run(app, host="0.0.0.0", port=8000, log_level="info")
