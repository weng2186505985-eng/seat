
import uvicorn
from fastapi import FastAPI, BackgroundTasks
from fastapi.responses import HTMLResponse, FileResponse
from pydantic import BaseModel
import logging
import time
import threading
from task_manager import TaskManager
import config

app = FastAPI()

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

tm = TaskManager()

class TaskItem(BaseModel):
    username: str
    password: str
    floor: str
    seatRange: str
    startTime: str
    endTime: str
    dateOffset: int
    triggerTime: str # 新增字段

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
    return tm.tasks

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
    def _run():
        tm._apply_task_config(data.dict())
        tm.bot.snatch_action()
    threading.Thread(target=_run).start()
    return {"status": "processing"}

if __name__ == "__main__":
    print("\n🚀 HDU 任务制系统(测试增强版) 已启动！")
    uvicorn.run(app, host="127.0.0.1", port=8000, log_level="error")
