
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

# --- 1. 日志中转系统 (必须最先启动) ---
class LogQueueHandler(logging.Handler):
    def __init__(self):
        super().__init__()
        self.logs = []
        self.lock = threading.Lock()
        
    def emit(self, record):
        try:
            with self.lock:
                self.logs.append({
                    "time": time.strftime("%H:%M:%S"),
                    "msg": self.format(record),
                    "level": record.levelname.lower()
                })
                if len(self.logs) > 200: self.logs.pop(0)
        except: pass

log_handler = LogQueueHandler()
log_handler.setFormatter(logging.Formatter('%(message)s'))
logging.getLogger().addHandler(log_handler)
logging.getLogger().setLevel(logging.INFO)

# --- 2. 任务管理器 ---
tm = TaskManager()

# --- 3. Web 服务 ---
app = FastAPI()

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
    with log_handler.lock:
        total_len = len(log_handler.logs)
        if last_index > total_len: last_index = 0
        new_logs = log_handler.logs[last_index:total_len]
        return {"logs": new_logs, "last_index": total_len}

@app.get("/tasks")
async def list_tasks():
    # 直接返回，不再检查 None，因为 tm 是同步初始化的
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
    return {"status": "error", "message": "解析失败"}

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
    uvicorn.run(app, host="127.0.0.1", port=8000, log_level="info")
