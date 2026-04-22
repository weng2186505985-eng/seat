
import uvicorn
from fastapi import FastAPI, BackgroundTasks
from fastapi.responses import HTMLResponse, FileResponse, StreamingResponse
from pydantic import BaseModel
from typing import Optional
import logging
import asyncio
import json
import hashlib
import time
import os
import threading
from task_manager import TaskManager
from snatcher import UltraFastBot
from contextlib import asynccontextmanager
import logger_config
import notifier
from collections import deque

# --- 全局变量 ---
tm: TaskManager = None

# --- 日志中转系统 ---
class LogQueueHandler(logging.Handler):
    def __init__(self, maxlen=200):
        super().__init__()
        # 使用 deque 实现线程安全的列表操作
        self.logs = deque(maxlen=maxlen)
        self.skip_trace_formatter = True # 🎯 修复 Bug #9: 标记跳过全局格式化
        
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
    preferred_seat: Optional[str] = ""
    recurring: Optional[bool] = False

@app.get("/", response_class=HTMLResponse)
async def get_index():
    return FileResponse("index.html")

@app.get("/get_halls")
async def get_halls():
    """动态返回 seat_map.json 中所有场馆名，作为前端下拉列表的单一数据源"""
    if not tm or not tm.seat_map:
        return {"halls": []}
    return {"halls": list(tm.seat_map.keys())}

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
    # 🎯 修复：文件名格式与 logger_config.py 保持一致 → seat_YYYY-MM-DD.log
    file_path = os.path.join(log_dir, f"seat_{date}.log")
    
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
        for t in list(tm.tasks):
            c = {k: v for k, v in t.items() if not k.startswith('_')}
            if 'bot_instance' in c: del c['bot_instance']
            clean_tasks.append(c)
        return clean_tasks
    except:
        return []

@app.get("/events")
async def sse_endpoint():
    async def event_generator():
        last_log_idx = 0
        last_task_hash = ""
        while True:
            # 1. Check for logs
            all_logs = list(log_handler.logs)
            if len(all_logs) > last_log_idx:
                new_logs = all_logs[last_log_idx:]
                last_log_idx = len(all_logs)
                yield f"event: logs\ndata: {json.dumps({'logs': new_logs})}\n\n"
            
            # 2. Check for tasks
            try:
                tasks = []
                for t in list(tm.tasks):
                    # 只序列化非内部字段（不以 _ 开头），避免 datetime 等不可序列化对象
                    c = {k: v for k, v in t.items() if not k.startswith('_')}
                    if 'bot_instance' in c: del c['bot_instance']
                    tasks.append(c)
                
                task_json = json.dumps(tasks, sort_keys=True)
                task_hash = hashlib.md5(task_json.encode()).hexdigest()
                
                if task_hash != last_task_hash:
                    last_task_hash = task_hash
                    yield f"event: tasks\ndata: {task_json}\n\n"
            except Exception as e:
                logging.error(f"SSE task gathering error: {e}")
            
            # 3. Heartbeat / Keep-alive
            yield ": heartbeat\n\n"
            await asyncio.sleep(0.8)
            
    return StreamingResponse(event_generator(), media_type="text/event-stream")

@app.post("/test_notification")
async def test_notification():
    notifier.notify("🔔 HDU 抢座助手测试", "看到这条消息说明通知配置成功！")
    return {"status": "sent"}

@app.post("/update_config")
async def update_config(data: dict):
    # 动态更新 config 中的 Key
    if "bark_key" in data:
        import config
        config.BARK_KEY = data["bark_key"]
    if "sckey" in data:
        import config
        config.SCKEY = data["sckey"]
    return {"status": "updated"}

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
    import datetime
    if not tm: return {"status": "error", "message": "系统未就绪"}
    
    def _run():
        try:
            # 🎯 修复 Bug #5: 复用 TaskManager 的 Bot 池，避免独立实例化和进程泄露
            with tm.lock:
                bot = tm._get_bot(data.username)
            
            seat_list = tm._build_seat_list(data.floor, data.seatRange, data.preferred_seat, bot)
            params = {
                "username": data.username, "password": data.password,
                "floor": data.floor, "seat_list": seat_list,
                "seat_display": data.seatRange,
                "date_offset": data.dateOffset, "start_time": data.startTime, "end_time": data.endTime,
                "synced_now": datetime.datetime.fromtimestamp(time.time() + tm.time_offset),
                "time_offset": tm.time_offset,
                "rtt": 0.05
            }
            # 直接触发抢座逻辑
            bot.snatch_action(params)
        except Exception as e:
            logging.error(f"立即抢座执行异常: {e}")
            
    threading.Thread(target=_run, daemon=True).start()
    return {"status": "processing"}

if __name__ == "__main__":
    print("\n[Start] HDU Task System V4.2 Started!")
    uvicorn.run(app, host="0.0.0.0", port=8000, log_level="info")
