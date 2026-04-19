import json
import os
import time
import threading
import logging
import datetime
from snatcher import UltraFastBot

logger = logging.getLogger(__name__)

class TaskManager:
    def __init__(self):
        self.tasks_file = "tasks.json"
        self.tasks = self.load_tasks()
        # 为每个任务独立分配一个 Bot 实例，避免全局变量污染
        self.bots = {} 
        self.running = True
        self.lock = threading.Lock()
        
        self.seat_map = {}
        if os.path.exists("seat_map.json"):
            with open("seat_map.json", "r", encoding="utf-8") as f:
                self.seat_map = json.load(f)

        self.scheduler_thread = threading.Thread(target=self._scheduler_loop)
        self.scheduler_thread.daemon = True
        self.scheduler_thread.start()

    def load_tasks(self):
        if os.path.exists(self.tasks_file):
            try:
                with open(self.tasks_file, "r", encoding="utf-8") as f:
                    return json.load(f)
            except: return []
        return []

    def save_tasks(self):
        with self.lock:
            # 存盘前剔除不可序列化的 bot 实例
            save_data = []
            for t in self.tasks:
                c = t.copy()
                if 'retry_count' in c: del c['retry_count']
                save_data.append(c)
            with open(self.tasks_file, "w", encoding="utf-8") as f:
                json.dump(save_data, f, ensure_ascii=False, indent=2)

    def add_task(self, data):
        task_id = str(int(time.time()))
        seat_list = []
        hall = data['floor']
        s_range = data['seatRange']
        
        if hall in self.seat_map:
            if "-" in s_range:
                try:
                    start, end = map(int, s_range.split("-"))
                    for i in range(start, end + 1):
                        name = str(i)
                        if name in self.seat_map[hall]:
                            seat_list.append((name, self.seat_map[hall][name]))
                except: pass
            elif s_range in self.seat_map[hall]:
                seat_list.append((s_range, self.seat_map[hall][s_range]))

        new_task = {
            "id": task_id,
            "username": data['username'],
            "password": data['password'],
            "floor": hall,
            "seat_list": seat_list,
            "seat_display": s_range,
            "dateOffset": data['dateOffset'],
            "startTime": data['startTime'],
            "endTime": data['endTime'],
            "triggerTime": data.get('triggerTime', "20:00:00"),
            "status": "waiting",
            "last_run_date": "", # 记录上次运行成功的日期
            "created_at": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        }
        with self.lock:
            self.tasks.append(new_task)
            self.bots[task_id] = UltraFastBot() # 独立实例
        self.save_tasks()
        return task_id

    def delete_task(self, task_id):
        with self.lock:
            self.tasks = [t for t in self.tasks if t['id'] != task_id]
            if task_id in self.bots: del self.bots[task_id]
        self.save_tasks()

    def _scheduler_loop(self):
        logger.info("🚀 自动化循环任务调度引擎已启动...")
        while self.running:
            now = datetime.datetime.now()
            today_str = now.strftime("%Y-%m-%d")
            
            with self.lock:
                for task in self.tasks:
                    if task['status'] in ['failed']: continue
                    
                    # 1. 自动重置逻辑：如果是新的一天，将状态从 completed 恢复到 waiting
                    if task.get('last_run_date') != today_str:
                        if task['status'] == 'completed':
                            task['status'] = 'waiting'
                    
                    # 2. 如果今天已经成功运行过了，跳过
                    if task.get('last_run_date') == today_str:
                        continue

                    t_time = datetime.datetime.strptime(task['triggerTime'], "%H:%M:%S").replace(
                        year=now.year, month=now.month, day=now.day
                    )
                    
                    diff = (t_time - now).total_seconds()

                    # 预热 (提前 15 分钟)
                    if 0 < diff < 900 and task['status'] == "waiting":
                        task['status'] = "warming"
                        self._run_task_warmup(task)
                    
                    # 触发
                    if diff <= 0 and task['status'] in ["waiting", "warming", "ready"]:
                        was_ready = (task['status'] == "ready")
                        task['status'] = "snatching"
                        task['retry_count'] = 0 # 初始化重试计数
                        self._run_task_snatch(task, skip_refresh=was_ready)

            time.sleep(0.1)

    def _run_task_warmup(self, task):
        def _worker():
            task_id = task['id']
            if task_id not in self.bots: self.bots[task_id] = UltraFastBot()
            bot = self.bots[task_id]
            
            if bot.refresh_credentials(task['username'], task['password']):
                task['status'] = "ready"
                logger.info(f"✅ 任务 {task_id} 预热完成。")
            else:
                task['status'] = "waiting"
            self.save_tasks()
        threading.Thread(target=_worker).start()

    def _run_task_snatch(self, task, skip_refresh):
        def _worker():
            task_id = task['id']
            if task_id not in self.bots: self.bots[task_id] = UltraFastBot()
            bot = self.bots[task_id]
            
            params = {
                "username": task['username'],
                "password": task['password'],
                "floor": task['floor'],
                "seat_list": task['seat_list'],
                "date_offset": task['dateOffset'],
                "start_time": task['startTime'],
                "end_time": task['endTime']
            }
            
            # 执行抢座并检查返回值
            success = bot.snatch_action(params, skip_refresh=skip_refresh)
            
            if success:
                task['status'] = "completed"
                task['last_run_date'] = datetime.datetime.now().strftime("%Y-%m-%d")
                logger.info(f"🎊 任务 {task_id} 今日抢座大成功，进入明日待机。")
            else:
                task['retry_count'] = task.get('retry_count', 0) + 1
                if task['retry_count'] <= 3:
                    logger.warning(f"⚠️ 任务 {task_id} 失败，正在发起第 {task['retry_count']} 次重试...")
                    time.sleep(1) # 稍微喘口气再试
                    task['status'] = "ready" # 退回 ready 状态，让调度器再次触发
                else:
                    task['status'] = "failed"
                    logger.error(f"❌ 任务 {task_id} 连续 3 次失败，停止任务。")
            
            self.save_tasks()
        threading.Thread(target=_worker).start()
