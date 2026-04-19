import json
import os
import time
import threading
import logging
import datetime
from snatcher import UltraFastBot
import config

logger = logging.getLogger(__name__)

class TaskManager:
    def __init__(self):
        self.tasks_file = "tasks.json"
        self.tasks = self.load_tasks()
        self.bot = UltraFastBot()
        self.running = True
        self.lock = threading.Lock()
        
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
            with open(self.tasks_file, "w", encoding="utf-8") as f:
                json.dump(self.tasks, f, ensure_ascii=False, indent=2)

    def add_task(self, task_data):
        task_id = str(int(time.time()))
        new_task = {
            "id": task_id,
            "username": task_data['username'],
            "password": task_data['password'],
            "floor": task_data['floor'],
            "seats": task_data['seatRange'],
            "dateOffset": task_data['dateOffset'],
            "timeRange": f"{task_data['startTime']}-{task_data['endTime']}",
            "triggerTime": task_data.get('triggerTime', "20:00:00"), # 自定义触发时间
            "status": "waiting",
            "created_at": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        }
        with self.lock:
            self.tasks.append(new_task)
        self.save_tasks()
        return task_id

    def delete_task(self, task_id):
        with self.lock:
            self.tasks = [t for t in self.tasks if t['id'] != task_id]
        self.save_tasks()

    def _scheduler_loop(self):
        logger.info("🚀 任务调度引擎正在巡逻...")
        while self.running:
            now = datetime.datetime.now()
            
            with self.lock:
                for task in self.tasks:
                    if task['status'] in ['success', 'failed', 'completed']:
                        continue
                    
                    # 使用任务自带的触发时间
                    t_time = datetime.datetime.strptime(task['triggerTime'], "%H:%M:%S").replace(
                        year=now.year, month=now.month, day=now.day
                    )
                    
                    # 处理跨天逻辑
                    if now > t_time + datetime.timedelta(minutes=10):
                        t_time += datetime.timedelta(days=1)

                    diff = (t_time - now).total_seconds()

                    # 逻辑 1：预热 (前 15 分钟开始，且未预热过)
                    if 0 < diff < 900 and task['status'] == "waiting":
                        task['status'] = "warming"
                        self._run_task_warmup(task)
                    
                    # 逻辑 2：瞬时冲击 (偏差 100ms 内触发)
                    if diff <= 0 and task['status'] in ["waiting", "warming", "ready"]:
                        task['status'] = "snatching"
                        self._run_task_snatch(task)

            time.sleep(0.5)

    def _run_task_warmup(self, task):
        def _worker():
            logger.info(f"🛡️ 任务 {task['id']} 进入预热阶段...")
            self._apply_task_config(task)
            if self.bot.refresh_credentials():
                task['status'] = "ready"
                logger.info(f"✅ 任务 {task['id']} 预热完成，Token 已就绪。")
            else:
                task['status'] = "waiting" # 失败了回退，等待下次尝试
            self.save_tasks()
        threading.Thread(target=_worker).start()

    def _run_task_snatch(self, task):
        def _worker():
            logger.info(f"🚀 任务 {task['id']} 触发冲击时刻！")
            self._apply_task_config(task)
            self.bot.snatch_action(skip_refresh=(task['status'] == "ready"))
            task['status'] = "completed"
            self.save_tasks()
        threading.Thread(target=_worker).start()

    def _apply_task_config(self, task):
        config.USERNAME = task['username']
        config.PASSWORD = task['password']
        config.PREFERRED_FLOOR = task['floor']
        config.RESERVE_DAY_OFFSET = task['dateOffset']
        s_range = task['seats']
        if "-" in s_range:
            try:
                start, end = map(int, s_range.split("-"))
                config.PREFERRED_SEATS = [str(i) for i in range(start, end + 1)]
            except: config.PREFERRED_SEATS = [s_range]
        else:
            config.PREFERRED_SEATS = [s_range]
        times = task['timeRange'].split("-")
        config.RESERVE_START_TIME = times[0]
        config.RESERVE_END_TIME = times[1]
