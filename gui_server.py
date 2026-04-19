
import uvicorn
from fastapi import FastAPI, BackgroundTasks
from fastapi.responses import HTMLResponse, FileResponse
from pydantic import BaseModel
import logging
import time
import threading
from task_manager import TaskManager
from snatcher import UltraFastBot

app = FastAPI()

# --- 日志中转 ---
class LogQueueHandler(logging.Handler):
    def __init__(self):
        super().__init__()
        self.logs = []
    def emit(self, record):
        try:
            self.logs.append({
                "time": time.strftime("%H:%M:%S"),
                "msg": self.format(record),
                "level": record.levelname.lower()
            })
            if len(self.logs) > 100: self.logs.pop(0)
        except: pass

log_handler = LogQueueHandler()
log_handler.setFormatter(logging.Formatter('%(message)s'))
logging.getLogger().addHandler(log_handler)
logging.getLogger().setLevel(logging.INFO)

# --- 任务管理器 ---
tm = TaskManager()

class TaskItem(BaseModel):
    username: str
    password: str
    floor: str
    seatRange: str
    startTime: str
    endTime: str
    dateOffset: int
    triggerTime: str

@app.get("/", response_class=HTMLResponse)
async def get_index():
    return FileResponse("index.html")

@app.get("/get_logs")
async def get_logs():
    logs = list(log_handler.logs)
    log_handler.logs = []
    return logs

@app.get("/tasks")
async def list_tasks():
    # 返回给前端前，过滤掉不可序列化的 bot 对象
    clean_tasks = []
    for t in tm.tasks:
        c = t.copy()
        if 'bot_instance' in c: del c['bot_instance']
        clean_tasks.append(c)
    return clean_tasks

@app.post("/add_task")
async def add_task(data: TaskItem):
    task_id = tm.add_task(data.dict())
    return {"status": "added", "id": task_id}

@app.post("/delete_task/{task_id}")
async def delete_task(task_id: str):
    tm.delete_task(task_id)
    return {"status": "deleted"}

@app.post("/book_now")
async def book_now(data: TaskItem):
    # 立即执行修复：使用参数化调用
    def _run():
        bot = UltraFastBot()
        # 实时从地图匹配 ID
        seat_list = []
        hall = data.floor
        s_range = data.seatRange
        if hall in tm.seat_map:
            if "-" in s_range:
                start, end = map(int, s_range.split("-"))
                for i in range(start, end + 1):
                    name = str(i)
                    if name in tm.seat_map[hall]:
                        seat_list.append((name, tm.seat_map[hall][name]))
            elif s_range in tm.seat_map[hall]:
                seat_list.append((s_range, tm.seat_map[hall][s_range]))
        
        params = {
            "username": data.username,
            "password": data.password,
            "floor": hall,
            "seat_list": seat_list,
            "date_offset": data.dateOffset,
            "start_time": data.startTime,
            "end_time": data.endTime
        }
        bot.snatch_action(params)
    
    threading.Thread(target=_run).start()
    return {"status": "processing"}

if __name__ == "__main__":
    print("\n🚀 HDU 任务制系统 (正式版 V4.1) 已启动！")
    uvicorn.run(app, host="127.0.0.1", port=8000, log_level="error")
