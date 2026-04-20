
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

# --- 全局管理器引用 ---
tm: TaskManager = None

@asynccontextmanager
async def lifespan(app: FastAPI):
    global tm
    def _init():
        global tm
        tm = TaskManager()
    threading.Thread(target=_init, daemon=True).start()
    yield

app = FastAPI(lifespan=lifespan)

# --- 日志中转 ---
class LogQueueHandler(logging.Handler):
    def __init__(self):
        super().__init__()
        self.logs = []
        self.lock = threading.Lock() # 引入线程锁防止竞态
        
    def emit(self, record):
        try:
            with self.lock:
                self.logs.append({
                    "time": time.strftime("%H:%M:%S"),
                    "msg": self.format(record),
                    "level": record.levelname.lower()
                })
                # 保持最多 200 条，超出则截断前面的
                if len(self.logs) > 200: 
                    self.logs.pop(0)
        except: pass

log_handler = LogQueueHandler()
log_handler.setFormatter(logging.Formatter('%(message)s'))
logging.getLogger().addHandler(log_handler)
logging.getLogger().setLevel(logging.INFO)

# --- 任务管理器 (已移动到 lifespan) ---

class TaskItem(BaseModel):
    username: str
    password: str
    floor: str
    seatRange: str
    startTime: str
    endTime: str
    dateOffset: int
    triggerTime: str
    recurring: Optional[bool] = False # 循环字段

@app.get("/", response_class=HTMLResponse)
async def get_index():
    return FileResponse("index.html")

@app.get("/get_logs")
async def get_logs(last_index: int = 0):
    with log_handler.lock:
        total_len = len(log_handler.logs)
        # 如果前端游标超限（如后端重启），重置为 0
        if last_index > total_len:
            last_index = 0
            
        new_logs = log_handler.logs[last_index:total_len]
        return {"logs": new_logs, "last_index": total_len}

@app.get("/tasks")
async def list_tasks():
    clean_tasks = []
    for t in tm.tasks:
        c = t.copy()
        if 'bot_instance' in c: del c['bot_instance']
        clean_tasks.append(c)
    return clean_tasks

@app.post("/add_task")
async def add_task(data: TaskItem):
    task_id = tm.add_task(data.dict())
    if task_id: return {"status": "added", "id": task_id}
    return {"status": "error", "message": "座位解析失败"}

@app.post("/delete_task/{task_id}")
async def delete_task(task_id: str):
    tm.delete_task(task_id)
    return {"status": "deleted"}

@app.post("/book_now")
async def book_now(data: TaskItem):
    def _run():
        bot = UltraFastBot()
        seat_list = tm._build_seat_list(data.floor, data.seatRange)
        params = {
            "username": data.username, "password": data.password,
            "floor": data.floor, "seat_list": seat_list,
            "date_offset": data.dateOffset, "start_time": data.startTime, "end_time": data.endTime
        }
        bot.snatch_action(params)
    threading.Thread(target=_run).start()
    return {"status": "processing"}

if __name__ == "__main__":
    print("\n[Start] HDU Task System V4.2 Started!")
    uvicorn.run(app, host="0.0.0.0", port=8000, log_level="info")
