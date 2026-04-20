import json
import os
import time
import threading
import logging
import datetime
import requests
from snatcher import UltraFastBot
import logger_config
import uuid

logger = logging.getLogger(__name__)

class TaskManager:
    def __init__(self):
        logger_config.setup_logging()
        self.tasks_file = "tasks.json"
        self.tasks = self.load_tasks()
        self.user_bots = {} 
        self.running = True
        self.lock = threading.Lock()
        self.time_offset = 0 
        self.avg_rtt = 0.05 # 默认假设 50ms
        self.last_sync_time = 0
        
        self.seat_map = {}
        if os.path.exists("seat_map.json"):
            try:
                with open("seat_map.json", "r", encoding="utf-8") as f:
                    self.seat_map = json.load(f)
            except Exception as e:
                logger.warning(f"⚠️ 加载 seat_map.json 失败: {e}")

        self.scheduler_thread = threading.Thread(target=self._scheduler_loop)
        self.scheduler_thread.daemon = True
        self.scheduler_thread.start()

        self.sync_thread = threading.Thread(target=self._time_sync_loop)
        self.sync_thread.daemon = True
        self.sync_thread.start()

    def _time_sync_loop(self):
        """
        智能时间同步线程：
        1. 平时：每 4 小时同步一次，仅保持大概偏移。
        2. 战前 30 分钟：每 5 分钟同步一次。
        3. 战前 2 分钟：开启高精度毫秒同步。
        """
        while self.running:
            needs_precision = False
            is_near_task = False
            
            with self.lock:
                now_ts = time.time() + self.time_offset
                now = datetime.datetime.fromtimestamp(now_ts)
                for task in self.tasks:
                    if task['status'] in ['waiting', 'warming', 'ready']:
                        t_time = datetime.datetime.strptime(task['triggerTime'], "%H:%M:%S").replace(
                            year=now.year, month=now.month, day=now.day
                        )
                        if now > t_time + datetime.timedelta(minutes=2):
                            t_time += datetime.timedelta(days=1)
                        
                        diff = (t_time - now).total_seconds()
                        
                        if 0 < diff < 120: # 2分钟内
                            needs_precision = True
                            break
                        if 0 < diff < 1800: # 30分钟内
                            is_near_task = True
            
            self._sync_server_time(precision=needs_precision)
            
            if needs_precision:
                wait_sec = 60 # 临战状态，每分钟校准一次
            elif is_near_task:
                wait_sec = 300 # 战前准备，每 5 分钟校准一次
            else:
                wait_sec = 14400 # 平时佛系，4 小时校准一次
                
            time.sleep(wait_sec)

    def _sync_server_time(self, precision=False):
        try:
            # 放弃秒级精度的 Header Date，尝试获取带有毫秒时间戳的业务 API
            url = "https://hdu.huitu.zhishulib.com/Seat/Index/searchSeats?LAB_JSON=1"
            start_local = time.time()
            resp = requests.get(url, timeout=5)
            rtt = time.time() - start_local
            self.avg_rtt = rtt
            
            data = resp.json()
            # 1. 广谱字段扫描
            st = data.get('serverTime') or data.get('now') or data.get('time') or data.get('timestamp') or data.get('sysTime')
            
            # 2. 深度扫描（如果根目录没有，检查 data 内部）
            if not st and isinstance(data.get('data'), dict):
                st = data['data'].get('serverTime') or data['data'].get('now') or data['data'].get('time')

            if st:
                server_ts = float(st) / 1000.0 if float(st) > 2000000000 else float(st)
                adjusted_server_ts = server_ts + (rtt / 2)
                self.time_offset = adjusted_server_ts - time.time()
                logger.info(f"🚀 [高精度] 成功获取 JSON 毫秒时钟: 偏差 {self.time_offset*1000:.1f}ms, RTT {rtt*1000:.1f}ms")
            else:
                date_str = resp.headers.get('Date')
                if date_str and precision:
                    # 🎯 战前突击模式：跳秒捕捉算法
                    last_date = date_str
                    # 步长 150ms，最多 8 次嗅探，跨度 1.2s，足以抓到变秒点
                    for _ in range(8):
                        time.sleep(0.15)
                        r = requests.head(url, timeout=3)
                        new_date = r.headers.get('Date')
                        if new_date != last_date:
                            server_ts = datetime.datetime.strptime(new_date, '%a, %d %b %Y %H:%M:%S GMT').replace(
                                tzinfo=datetime.timezone.utc).timestamp()
                            rtt_sniff = r.elapsed.total_seconds()
                            self.time_offset = (server_ts + rtt_sniff / 2) - time.time()
                            logger.info(f"🎯 [极致精度] 战时跳秒捕捉成功！偏差: {self.time_offset*1000:.1f}ms")
                            break
                        last_date = new_date
                elif date_str:
                    # 🕒 日常佛系模式：中值修正 (+0.5s)
                    server_ts = datetime.datetime.strptime(date_str, '%a, %d %b %Y %H:%M:%S GMT').replace(
                        tzinfo=datetime.timezone.utc).timestamp() + 0.5
                    adjusted_server_ts = server_ts + (rtt / 2)
                    self.time_offset = adjusted_server_ts - time.time()
                    logger.info(f"⏰ [中精度] 日常对时完成: 偏差 {self.time_offset*1000:.1f}ms (Header 估算，可能存在 ±500ms 误差)")
                else:
                    self.time_offset = 0
                    logger.warning(f"❌ [低精度] 无法获取服务器参考时间")
            
            self.last_sync_time = time.time()
        except Exception as e:
            self.time_offset = 0
            logger.warning(f"⚠️ 时钟同步异常: {e}")

    def load_tasks(self):
        if os.path.exists(self.tasks_file):
            try:
                with open(self.tasks_file, "r", encoding="utf-8") as f:
                    return json.load(f)
            except Exception as e:
                logger.error(f"❌ 加载任务列表失败: {e}")
                return []
        return []

    def save_tasks(self):
        with self.lock:
            save_data = []
            for t in self.tasks:
                c = t.copy()
                if 'bot_instance' in c: del c['bot_instance']
                save_data.append(c)
            tmp_file = self.tasks_file + ".tmp"
            try:
                with open(tmp_file, "w", encoding="utf-8") as f:
                    json.dump(save_data, f, ensure_ascii=False, indent=2)
                    f.flush()
                    os.fsync(f.fileno())
                os.replace(tmp_file, self.tasks_file)
            except Exception as e:
                logger.error(f"❌ 保存任务列表失败: {e}")

    def _get_bot(self, username):
        if username not in self.user_bots:
            self.user_bots[username] = UltraFastBot()
        return self.user_bots[username]

    def add_task(self, data):
        task_id = str(int(time.time()))
        username = data['username']
        with self.lock:
            bot = self._get_bot(username)
            seat_list = self._build_seat_list(data['floor'], data['seatRange'], data.get('preferred_seat', ""), bot)
            if not seat_list: 
                logger.warning(f"⚠️ 无法为 {username} 构建座位列表: {data['floor']} {data['seatRange']}")
                return None

            new_task = {
                "id": task_id, "username": username, "password": data['password'],
                "floor": data['floor'], "seat_list": seat_list, "seat_display": data['seatRange'],
                "preferred_seat": data.get('preferred_seat', ""),
                "dateOffset": data['dateOffset'], "startTime": data['startTime'], "endTime": data['endTime'],
                "triggerTime": data.get('triggerTime', "20:00:00"), "recurring": data.get('recurring', False),
                "status": "waiting", "last_run_date": "", "created_at": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            }
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
            except Exception as e:
                logger.warning(f"⚠️ 解析座位号范围失败: {e}")
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
            now_ts = time.time() + self.time_offset
            now = datetime.datetime.fromtimestamp(now_ts)
            today_str = now.strftime("%Y-%m-%d")
            
            tasks_to_warmup = []
            tasks_to_snatch = []

            with self.lock:
                # 1. 预计算状态集合，将复杂度从 O(n^2) 降为 O(n)
                ready_users = {t['username'] for t in self.tasks if t['status'] == 'ready'}
                warming_users = {t['username'] for t in self.tasks if t['status'] == 'warming'}
                
                for task in self.tasks:
                    # 跳过已完成且非循环的任务
                    if task['status'] in ['completed', 'failed'] and not task.get('recurring'): continue
                    
                    # 跨天自动重置
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

                    # 状态机：waiting -> warming -> ready -> snatching
                    if 0 < diff < 900 and task['status'] == "waiting":
                        task['status'] = "warming"
                        if task['username'] not in ready_users and task['username'] not in warming_users:
                            tasks_to_warmup.append(task)
                            warming_users.add(task['username'])
                    
                    elif task['status'] == 'warming' and task['username'] in ready_users:
                        task['status'] = 'ready'

                    if diff <= 2 and task['status'] in ["waiting", "warming", "ready"]:
                        trigger_ts = t_time.timestamp()
                        was_ready = (task['status'] == "ready")
                        task['status'] = "snatching"
                        tasks_to_snatch.append((task, was_ready, trigger_ts))

            for task in tasks_to_warmup: self._run_task_warmup(task)
            for task, skip, t_ts in tasks_to_snatch: self._run_task_snatch(task, skip, t_ts)
            time.sleep(0.5)

    def _run_task_warmup(self, task):
        def _worker():
            trace_id = f"WARM-{uuid.uuid4().hex[:8]}"
            logger_config.set_trace_id(trace_id)
            u = task['username']
            with self.lock:
                bot = self._get_bot(u)
            if bot.refresh_credentials(u, task['password']):
                with self.lock:
                    for t in self.tasks:
                        if t['username'] == u and t['status'] == 'warming': t['status'] = "ready"
                # 发送预热成功通知
                bot.notify(True, custom_title="⚔️ 预热完成", 
                           custom_msg=f"账号 {u} Token 就绪，将在 {task['triggerTime']} 准时出击\n目标：{task['floor']} {task['seat_display']}")
            else:
                with self.lock: task['status'] = "waiting"
        threading.Thread(target=_worker, daemon=True).start()

    def _run_task_snatch(self, task, skip_refresh, trigger_ts):
        def _worker():
            trace_id = f"SNATCH-{uuid.uuid4().hex[:8]}"
            logger_config.set_trace_id(trace_id)
            u = task['username']
            with self.lock:
                bot = self._get_bot(u)
            params = {
                "username": u, "password": task['password'], "floor": task['floor'],
                "seat_list": task['seat_list'], "date_offset": task['dateOffset'],
                "start_time": task['startTime'], "end_time": task['endTime'],
                "trigger_ts": trigger_ts, "rtt": self.avg_rtt, "time_offset": self.time_offset,
                "preferred_seat": task.get('preferred_seat')
            }
            
            # 发送冲击通知
            target_date = (datetime.datetime.now() + datetime.timedelta(days=task['dateOffset'])).strftime("%Y-%m-%d")
            bot.notify(False, custom_title="🚀 开始冲击", 
                       custom_msg=f"正在对 {task['seat_display']} 发起抢座\n目标日期：{target_date}")
            
            success = False
            for i in range(2):
                if i > 0:
                    time.sleep(1.5)
                    # 发送重试通知
                    bot.notify(False, custom_title=f"⚠️ 第{i}次重试", 
                               custom_msg=f"第一轮未抢到，正在发起第 {i} 次重试...")
                
                res = bot.snatch_action(params, skip_refresh=(skip_refresh or i > 0 or bot.is_warmed_up))
                if isinstance(res, str): # 如果返回了具体座位号
                    success = res
                    break
            
            with self.lock:
                task['last_run_date'] = datetime.datetime.now().strftime("%Y-%m-%d")
                if success:
                    task['status'] = "completed"
                    # 智能锁定：记录真实抢到的座位号
                    task['preferred_seat'] = success
                else:
                    task['status'] = "failed"
                    bot.notify(False, custom_title="❌ 抢座失败",
                               custom_msg=f"连续 2 轮未中已停止\n场馆：{task['floor']}\n座位：{task['seat_display']}")
            self.save_tasks()
        threading.Thread(target=_worker, daemon=True).start()
