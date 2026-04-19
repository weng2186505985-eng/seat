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
        # 核心优化：将 Bot 实例与 username 绑定，确保同账号共享凭证
        self.user_bots = {} 
        self.running = True
        self.lock = threading.Lock()
        
        self.seat_map = {}
        if os.path.exists("seat_map.json"):
            try:
                with open("seat_map.json", "r", encoding="utf-8") as f:
                    self.seat_map = json.load(f)
            except: pass

        self.scheduler_thread = threading.Thread(target=self._scheduler_loop)
        self.scheduler_thread.daemon = True
        self.scheduler_thread.start()

    def _get_bot(self, username):
        """线程安全地获取或创建账号专属 Bot"""
        with self.lock:
            if username not in self.user_bots:
                self.user_bots[username] = UltraFastBot()
            return self.user_bots[username]

    def load_tasks(self):
        if os.path.exists(self.tasks_file):
            try:
                with open(self.tasks_file, "r", encoding="utf-8") as f:
                    return json.load(f)
            except: return []
        return []

    def save_tasks(self):
        with self.lock:
            save_data = []
            for t in self.tasks:
                c = t.copy()
                # 确保不保存运行时的 bot 引用
                if 'bot_instance' in c: del c['bot_instance']
                save_data.append(c)
            with open(self.tasks_file, "w", encoding="utf-8") as f:
                json.dump(save_data, f, ensure_ascii=False, indent=2)

    def add_task(self, data):
        task_id = str(int(time.time()))
        username = data['username']
        hall = data['floor']
        s_range = data['seatRange']
        pref = data.get('preferred_seat', "")
        
        # 获取账号专属 Bot 以进行黑名单过滤
        bot = self._get_bot(username)
        seat_list = self._build_seat_list(hall, s_range, pref, bot)
        if not seat_list: return None

        new_task = {
            "id": task_id,
            "username": username,
            "password": data['password'],
            "floor": hall,
            "seat_list": seat_list,
            "seat_display": s_range,
            "preferred_seat": pref,
            "dateOffset": data['dateOffset'],
            "startTime": data['startTime'],
            "endTime": data['endTime'],
            "triggerTime": data.get('triggerTime', "20:00:00"),
            "recurring": data.get('recurring', False),
            "status": "waiting",
            "last_run_date": "",
            "created_at": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        }
        with self.lock:
            self.tasks.append(new_task)
        self.save_tasks()
        return task_id

    def _build_seat_list(self, hall, s_range, preferred="", bot=None):
        if hall not in self.seat_map: return []
        raw_list = []
        if "-" in s_range:
            try:
                start, end = map(int, s_range.split("-"))
                for i in range(start, end + 1):
                    name = str(i)
                    if name in self.seat_map[hall]:
                        if bot and name in bot.blacklist: continue
                        raw_list.append((name, self.seat_map[hall][name]))
            except: pass
        elif s_range in self.seat_map[hall]:
            if not (bot and s_range in bot.blacklist):
                raw_list.append((s_range, self.seat_map[hall][s_range]))
        
        if not raw_list: return []

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
        self.save_tasks()

    def _scheduler_loop(self):
        while self.running:
            now = datetime.datetime.now()
            today_str = now.strftime("%Y-%m-%d")
            tasks_to_warmup = []
            tasks_to_snatch = []

            with self.lock:
                for task in self.tasks:
                    if task['status'] == 'failed' and not task.get('recurring'): continue
                    
                    if task.get('last_run_date') != today_str:
                        if task['status'] in ['completed', 'failed']:
                            task['status'] = 'waiting'
                            bot = self._get_bot(task['username'])
                            task['seat_list'] = self._build_seat_list(task['floor'], task['seat_display'], task.get('preferred_seat', ""), bot)
                    
                    if task.get('last_run_date') == today_str: continue

                    t_time = datetime.datetime.strptime(task['triggerTime'], "%H:%M:%S").replace(
                        year=now.year, month=now.month, day=now.day
                    )
                    if now > t_time + datetime.timedelta(minutes=2):
                        t_time += datetime.timedelta(days=1)
                    
                    diff = (t_time - now).total_seconds()

                    if 0 < diff < 900 and task['status'] == "waiting":
                        # 检查同账号是否有其他任务已在预热或已 ready
                        other_warming = any(t['username'] == task['username'] and t['status'] in ['warming', 'ready'] for t in self.tasks)
                        if other_warming:
                            # 如果已有其他任务在预热，当前任务直接同步状态，不需要重发请求
                            task['status'] = 'warming' # 稍后同步为 ready
                        else:
                            task['status'] = "warming"
                            tasks_to_warmup.append(task)
                    
                    # 状态同步：如果同账号有任务已经 ready，将 warming 的也设为 ready
                    if task['status'] == 'warming':
                        is_any_ready = any(t['username'] == task['username'] and t['status'] == 'ready' for t in self.tasks)
                        if is_any_ready: task['status'] = 'ready'

                    if diff <= 0 and task['status'] in ["waiting", "warming", "ready"]:
                        was_ready = (task['status'] == "ready")
                        task['status'] = "snatching"
                        tasks_to_snatch.append((task, was_ready))

            for task in tasks_to_warmup: self._run_task_warmup(task)
            for task, skip in tasks_to_snatch: self._run_task_snatch(task, skip)
            time.sleep(0.1)

    def _run_task_warmup(self, task):
        def _worker():
            username = task['username']
            bot = self._get_bot(username)
            if bot.refresh_credentials(username, task['password']):
                with self.lock:
                    # 将该账号下所有处于 warming 状态的任务同步为 ready
                    for t in self.tasks:
                        if t['username'] == username and t['status'] == 'warming':
                            t['status'] = "ready"
                logger.info(f"✅ 账号 {username} 凭证预热成功，已应用至相关任务。")
            else:
                with self.lock: task['status'] = "waiting"
            self.save_tasks()
        threading.Thread(target=_worker, daemon=True).start()

    def _run_task_snatch(self, task, skip_refresh):
        def _worker():
            username = task['username']
            bot = self._get_bot(username)
            
            params = {
                "username": username, "password": task['password'],
                "floor": task['floor'], "seat_list": task['seat_list'],
                "date_offset": task['dateOffset'], "start_time": task['startTime'], "end_time": task['endTime']
            }
            
            success = False
            for i in range(4):
                if i > 0: time.sleep(2)
                # 核心优化：重试时也会利用 bot 实例中共享的凭证
                success = bot.snatch_action(params, skip_refresh=(skip_refresh or i > 0 or bot.is_warmed_up))
                if success: break
            
            with self.lock:
                if success:
                    task['status'] = "completed"
                    task['last_run_date'] = datetime.datetime.now().strftime("%Y-%m-%d")
                    task['preferred_seat'] = task['seat_list'][0][0]
                else:
                    task['status'] = "failed"
                    logger.error(f"❌ 账号 {username} 任务最终失败。")
                    err_msg = f"账号：{username}\n场馆：{task['floor']}\n已连续尝试失败，请检查。"
                    bot.notify(False, custom_msg=err_msg)
            self.save_tasks()
        threading.Thread(target=_worker, daemon=True).start()
