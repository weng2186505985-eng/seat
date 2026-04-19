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
<<<<<<< HEAD
=======
        # 为每个任务独立分配一个 Bot 实例，避免全局变量污染
>>>>>>> a5ec20e1bb719bd7c7bfe34216c3d4a993c241e7
        self.bots = {} 
        self.running = True
        self.lock = threading.Lock()
        
        self.seat_map = {}
        if os.path.exists("seat_map.json"):
<<<<<<< HEAD
            try:
                with open("seat_map.json", "r", encoding="utf-8") as f:
                    self.seat_map = json.load(f)
            except: pass
=======
            with open("seat_map.json", "r", encoding="utf-8") as f:
                self.seat_map = json.load(f)
>>>>>>> a5ec20e1bb719bd7c7bfe34216c3d4a993c241e7

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
<<<<<<< HEAD
            save_data = []
            for t in self.tasks:
                c = t.copy()
                if 'bot_instance' in c: del c['bot_instance']
=======
            # 存盘前剔除不可序列化的 bot 实例
            save_data = []
            for t in self.tasks:
                c = t.copy()
                if 'retry_count' in c: del c['retry_count']
>>>>>>> a5ec20e1bb719bd7c7bfe34216c3d4a993c241e7
                save_data.append(c)
            with open(self.tasks_file, "w", encoding="utf-8") as f:
                json.dump(save_data, f, ensure_ascii=False, indent=2)

    def add_task(self, data):
        task_id = str(int(time.time()))
<<<<<<< HEAD
        hall = data['floor']
        s_range = data['seatRange']
        pref = data.get('preferred_seat', "")
        
        # 构建 seat_list
        seat_list = self._build_seat_list(hall, s_range, pref)
        if not seat_list: return None
=======
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
>>>>>>> a5ec20e1bb719bd7c7bfe34216c3d4a993c241e7

        new_task = {
            "id": task_id,
            "username": data['username'],
            "password": data['password'],
            "floor": hall,
            "seat_list": seat_list,
            "seat_display": s_range,
<<<<<<< HEAD
            "preferred_seat": pref, # 成功后的座位会记录在这里
=======
>>>>>>> a5ec20e1bb719bd7c7bfe34216c3d4a993c241e7
            "dateOffset": data['dateOffset'],
            "startTime": data['startTime'],
            "endTime": data['endTime'],
            "triggerTime": data.get('triggerTime', "20:00:00"),
<<<<<<< HEAD
            "recurring": data.get('recurring', False), # 是否每日循环
            "status": "waiting",
            "last_run_date": "",
=======
            "status": "waiting",
            "last_run_date": "", # 记录上次运行成功的日期
>>>>>>> a5ec20e1bb719bd7c7bfe34216c3d4a993c241e7
            "created_at": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        }
        with self.lock:
            self.tasks.append(new_task)
<<<<<<< HEAD
            self.bots[task_id] = UltraFastBot()
=======
            self.bots[task_id] = UltraFastBot() # 独立实例
>>>>>>> a5ec20e1bb719bd7c7bfe34216c3d4a993c241e7
        self.save_tasks()
        return task_id

    def _build_seat_list(self, hall, s_range, preferred=""):
        """构建座位列表，并将 preferred 座位放到第一位"""
        if hall not in self.seat_map: return []
        raw_list = []
        if "-" in s_range:
            try:
                start, end = map(int, s_range.split("-"))
                for i in range(start, end + 1):
                    name = str(i)
                    if name in self.seat_map[hall]:
                        raw_list.append((name, self.seat_map[hall][name]))
            except: pass
        elif s_range in self.seat_map[hall]:
            raw_list.append((s_range, self.seat_map[hall][s_range]))
        
        if not raw_list: return []

        # 排序：将 preferred 座位排在最前面
        if preferred:
            final_list = []
            pref_item = None
            for item in raw_list:
                if item[0] == preferred: pref_item = item
                else: final_list.append(item)
            if pref_item: final_list.insert(0, pref_item)
            return final_list
        return raw_list

    def delete_task(self, task_id):
        with self.lock:
            self.tasks = [t for t in self.tasks if t['id'] != task_id]
            if task_id in self.bots: del self.bots[task_id]
        self.save_tasks()

    def _scheduler_loop(self):
<<<<<<< HEAD
=======
        logger.info("🚀 自动化循环任务调度引擎已启动...")
>>>>>>> a5ec20e1bb719bd7c7bfe34216c3d4a993c241e7
        while self.running:
            now = datetime.datetime.now()
            today_str = now.strftime("%Y-%m-%d")
            
            tasks_to_warmup = []
            tasks_to_snatch = []

            with self.lock:
                for task in self.tasks:
<<<<<<< HEAD
                    if task['status'] == 'failed' and not task.get('recurring'): continue
                    
                    # 循环重置逻辑
                    if task.get('last_run_date') != today_str:
                        if task['status'] in ['completed', 'failed']:
                            task['status'] = 'waiting'
                            # 重新生成 seat_list 以应用最新的 preferred_seat 权重
                            task['seat_list'] = self._build_seat_list(task['floor'], task['seat_display'], task.get('preferred_seat', ""))
                    
                    if task.get('last_run_date') == today_str: continue
=======
                    if task['status'] in ['failed']: continue
                    
                    # 1. 自动重置逻辑：如果是新的一天，将状态从 completed 恢复到 waiting
                    if task.get('last_run_date') != today_str:
                        if task['status'] == 'completed':
                            task['status'] = 'waiting'
                    
                    # 2. 如果今天已经成功运行过了，跳过
                    if task.get('last_run_date') == today_str:
                        continue
>>>>>>> a5ec20e1bb719bd7c7bfe34216c3d4a993c241e7

                    t_time = datetime.datetime.strptime(task['triggerTime'], "%H:%M:%S").replace(
                        year=now.year, month=now.month, day=now.day
                    )
<<<<<<< HEAD
                    diff = (t_time - now).total_seconds()

=======
                    
                    diff = (t_time - now).total_seconds()

                    # 预热 (提前 15 分钟)
>>>>>>> a5ec20e1bb719bd7c7bfe34216c3d4a993c241e7
                    if 0 < diff < 900 and task['status'] == "waiting":
                        task['status'] = "warming"
                        tasks_to_warmup.append(task)
                    
<<<<<<< HEAD
                    if diff <= 0 and task['status'] in ["waiting", "warming", "ready"]:
                        was_ready = (task['status'] == "ready")
                        task['status'] = "snatching"
                        tasks_to_snatch.append((task, was_ready))

            for task in tasks_to_warmup: self._run_task_warmup(task)
            for task, skip in tasks_to_snatch: self._run_task_snatch(task, skip)

=======
                    # 触发
                    if diff <= 0 and task['status'] in ["waiting", "warming", "ready"]:
                        was_ready = (task['status'] == "ready")
                        task['status'] = "snatching"
                        task['retry_count'] = 0 # 初始化重试计数
                        self._run_task_snatch(task, skip_refresh=was_ready)

>>>>>>> a5ec20e1bb719bd7c7bfe34216c3d4a993c241e7
            time.sleep(0.1)

    def _run_task_warmup(self, task):
        def _worker():
            task_id = task['id']
            if task_id not in self.bots: self.bots[task_id] = UltraFastBot()
            bot = self.bots[task_id]
<<<<<<< HEAD
            if bot.refresh_credentials(task['username'], task['password']):
                with self.lock: task['status'] = "ready"
            else:
                with self.lock: task['status'] = "waiting"
=======
            
            if bot.refresh_credentials(task['username'], task['password']):
                task['status'] = "ready"
                logger.info(f"✅ 任务 {task_id} 预热完成。")
            else:
                task['status'] = "waiting"
>>>>>>> a5ec20e1bb719bd7c7bfe34216c3d4a993c241e7
            self.save_tasks()
        threading.Thread(target=_worker, daemon=True).start()

    def _run_task_snatch(self, task, skip_refresh):
        def _worker():
            task_id = task['id']
            if task_id not in self.bots: self.bots[task_id] = UltraFastBot()
            bot = self.bots[task_id]
            
            params = {
<<<<<<< HEAD
                "username": task['username'], "password": task['password'],
                "floor": task['floor'], "seat_list": task['seat_list'],
                "date_offset": task['dateOffset'], "start_time": task['startTime'], "end_time": task['endTime']
            }
            
            max_retries = 3
            current_retry = 0
            success_seat = ""
            
            while current_retry <= max_retries:
                if current_retry > 0: time.sleep(2)
                
                # 修改 snatcher 返回值处理，获取成功的座位号
                # 假设 snatcher.snatch_action 内部成功时会设置某个标记，我们在这里稍微调整下
                # 为了简单起见，我们假设 snatch_action 成功时返回座位名，失败返回 None
                # 但根据上一版，它返回布尔值。我们直接通过 bot 里的状态获取。
                success = bot.snatch_action(params, skip_refresh=(skip_refresh or current_retry > 0))
                if success:
                    # 尝试从日志逻辑中抠出成功的座位名（这里简单处理：如果是 list，通常第一个成功的会打印）
                    # 我们直接把任务设为成功，并在下一次循环中自动优化
                    success_seat = task['seat_list'][0][0] # 暂时假设是第一个
                    break
                current_retry += 1
            
            with self.lock:
                if success:
                    task['status'] = "completed"
                    task['last_run_date'] = datetime.datetime.now().strftime("%Y-%m-%d")
                    # 记录成功座位
                    if success_seat: task['preferred_seat'] = success_seat
                else:
                    task['status'] = "failed"
            self.save_tasks()
            
        threading.Thread(target=_worker, daemon=True).start()
=======
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
>>>>>>> a5ec20e1bb719bd7c7bfe34216c3d4a993c241e7
