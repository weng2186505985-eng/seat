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
        # 移除 logger_config.setup_logging()，改由外部显式调用或在 gui_server 中处理
        self.tasks_file = "tasks.json"
        self.tasks = self.load_tasks()
        self.user_bots = {} 
        self.running = True
        self.lock = threading.RLock() # 使用递归锁防止死锁
        self.time_offset = 0 
        self.avg_rtt = 0.05 # 默认假设 50ms
        self.last_sync_time = 0
        self.firing_events = {} # {trigger_ts: threading.Event()} 🎯 统一发令枪池
        
        self._local = threading.local()
        self.last_blacklist_clear_date = "" # 记录上次清空黑名单的日期
        
        self.seat_map = {}
        if os.path.exists("seat_map.json"):
            try:
                with open("seat_map.json", "r", encoding="utf-8") as f:
                    self.seat_map = json.load(f)
            except Exception as e:
                logger.warning(f"⚠️ 加载 seat_map.json 失败: {e}")

        self.scheduler_thread = threading.Thread(target=self._scheduler_loop)
        self.scheduler_thread.daemon = True
        
        self.sync_thread = threading.Thread(target=self._time_sync_loop)
        self.sync_thread.daemon = True

    def start(self):
        """显式开启后台线程，避免在 __init__ 中导致死锁"""
        # 🎯 启动前清洗僵尸任务：上次崩溃可能遗留 snatching 状态的任务
        with self.lock:
            for task in self.tasks:
                if task['status'] == 'snatching':
                    logger.warning(f"🧹 [Startup] 清洗僵尸任务 {task['id']}：snatching → waiting")
                    task['status'] = 'waiting'
            self.save_tasks()
        
        if not self.scheduler_thread.is_alive():
            self.scheduler_thread.start()
        if not self.sync_thread.is_alive():
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
            
            # 🎯 改进同步策略：平时不折腾，临战前精准打击
            if needs_precision:
                sync_interval = 40    # 临战状态，每 40 秒校准一次
            elif is_near_task:
                sync_interval = 600   # 战前准备，每 10 分钟同步一次
            else:
                sync_interval = 43200 # 平时极简模式：每 12 小时同步一次

            # 只有当达到同步间隔，或者处于临战状态（需要高精度）时，才执行网络同步
            time_since_last = time.time() - self.last_sync_time
            if time_since_last >= sync_interval or (needs_precision and time_since_last > 40):
                self._sync_server_time(precision=needs_precision)
                
            time.sleep(30) 

    def _sync_server_time(self, precision=False):
        try:
            # 放弃秒级精度的 Header Date，尝试获取带有毫秒时间戳的业务 API
            url = "https://hdu.huitu.zhishulib.com/Seat/Index/searchSeats?LAB_JSON=1"
            start_local = time.time()
            resp = requests.get(url, timeout=5)
            t1 = time.time()
            rtt = t1 - start_local
            
            # 使用指数移动平均 (EMA) 平滑 RTT
            if self.avg_rtt == 0.05: self.avg_rtt = rtt
            else: self.avg_rtt = 0.7 * self.avg_rtt + 0.3 * rtt
            
            data = resp.json()
            # 1. 广谱字段扫描
            st = data.get('serverTime') or data.get('now') or data.get('time') or data.get('timestamp') or data.get('sysTime')
            
            # 2. 深度扫描（如果根目录没有，检查 data 内部）
            if not st and isinstance(data.get('data'), dict):
                st = data['data'].get('serverTime') or data['data'].get('now') or data['data'].get('time')

            if st:
                server_ts = float(st) / 1000.0 if float(st) > 2000000000 else float(st)
                adjusted_server_ts = server_ts + (self.avg_rtt / 2)
                self.time_offset = adjusted_server_ts - t1
                logger.info(f"[High Precision] Server time synced via JSON: offset {self.time_offset*1000:.1f}ms, RTT {rtt*1000:.1f}ms")
            else:
                date_str = resp.headers.get('Date')
                if date_str and precision:
                    # 🎯 战前突击模式：跳秒捕捉算法（在独立线程中执行，避免阻塞同步主循环 2.4s）
                    threading.Thread(
                        target=self._jump_second_capture, 
                        args=(url, date_str), 
                        daemon=True
                    ).start()
                elif date_str:
                    # 🕒 日常佛系模式：中值修正 (+0.5s)
                    server_ts = datetime.datetime.strptime(date_str, '%a, %d %b %Y %H:%M:%S GMT').replace(
                        tzinfo=datetime.timezone.utc).timestamp() + 0.5
                    adjusted_server_ts = server_ts + (self.avg_rtt / 2)
                    self.time_offset = adjusted_server_ts - t1
                    logger.info(f"[Mid Precision] Daily sync completed: offset {self.time_offset*1000:.1f}ms (Header estimate)")
                else:
                    self.time_offset = 0
                    logger.warning(f"❌ [低精度] 无法获取服务器参考时间")
            
            self.last_sync_time = time.time()
            return True
        except Exception as e:
            logger.warning(f"⚠️ 时钟同步异常: {e}")
            return False

    def _jump_second_capture(self, url, initial_date):
        """跳秒捕捉算法：独立线程执行，避免阻塞 _time_sync_loop 主循环"""
        last_date = initial_date
        # 步长 300ms，最多 8 次嗅探，跨度 2.4s
        for _ in range(8):
            time.sleep(0.3)
            try:
                r = requests.head(url, timeout=3)
                t_sniff = time.time()
                new_date = r.headers.get('Date')
                if new_date and new_date != last_date:
                    server_ts = datetime.datetime.strptime(new_date, '%a, %d %b %Y %H:%M:%S GMT').replace(
                        tzinfo=datetime.timezone.utc).timestamp()
                    rtt_sniff = r.elapsed.total_seconds()
                    self.time_offset = (server_ts + rtt_sniff / 2) - t_sniff
                    self.last_sync_time = time.time()
                    logger.info(f"🎯 [极致精度] 战时跳秒捕捉成功！偏差: {self.time_offset*1000:.1f}ms")
                    return
                last_date = new_date
            except Exception as e:
                logger.debug(f"跳秒嗅探异常: {e}")
        logger.info("⏱️ 跳秒捕捉未能在 8 次嗅探内检测到变秒，回退到中值估算")

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
        # 1. 锁内仅做快照，确保存储数据的一致性
        with self.lock:
            save_data = []
            for t in self.tasks:
                c = t.copy()
                if 'bot_instance' in c: del c['bot_instance']
                save_data.append(c)
        
        # 2. 锁外执行 IO 操作，防止磁盘 fsync 阻塞抢座时刻的 CPU 循环
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
        task_id = uuid.uuid4().hex[:12] # 使用更可靠的 UUID 防止冲突
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
                "status": "waiting", "last_run_date": "", "created_at": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                "fail_reason_stats": {"busy": 0, "occupied": 0, "other": 0}
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
            # 找到要删除的任务的用户名
            username = None
            for t in self.tasks:
                if t['id'] == task_id:
                    username = t['username']
                    break
            
            self.tasks = [t for t in self.tasks if t['id'] != task_id]
            
            # 🎯 修复 Bug #5: 如果该账号已无其他任务，清理 bot 实例释放内存
            if username and not any(t['username'] == username for t in self.tasks):
                if username in self.user_bots:
                    del self.user_bots[username]
                    logger.info(f"🧹 Cleaned up bot instance for {username} (no remaining tasks)")
        
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
                
                # 修复：每天全局只清空一次黑名单，而不是依赖 last_run_date
                if self.last_blacklist_clear_date != today_str:
                    logger.info(f"New day detected {today_str}, resetting global state...")
                    for bot_instance in self.user_bots.values():
                        bot_instance.clear_blacklist()
                    self.last_blacklist_clear_date = today_str

                for task in self.tasks:
                    # 跨天自动重置任务状态：仅针对已结束（完成或失败）的任务
                    if task.get('last_run_date') != today_str:
                        if task['status'] in ['completed', 'failed']:
                            old_status = task['status']
                            task['status'] = 'waiting'
                            logger.info(f"[Reset] Task {task['id']} reset to WAITING (was {old_status})")
                        
                            # 重新加载座位列表（防止黑名单变动）
                            bot = self._get_bot(task['username'])
                            task['seat_list'] = self._build_seat_list(task['floor'], task['seat_display'], task.get('preferred_seat', ""), bot)
                            # 强制保存一次，确保重启后状态正确
                            try:
                                self.save_tasks()
                            except Exception as e:
                                logger.error(f"Error saving tasks during reset: {e}")

                    # 跳过已完成且非循环的任务
                    if task['status'] in ['completed', 'failed'] and not task.get('recurring'): continue
                    
                    if task.get('last_run_date') == today_str: continue

                    # 🎯 性能优化：缓存字符串解析结果，减少持锁时间
                    if '_trigger_dt' not in task or task.get('_trigger_raw') != task['triggerTime']:
                        task['_trigger_dt'] = datetime.datetime.strptime(task['triggerTime'], "%H:%M:%S")
                        task['_trigger_raw'] = task['triggerTime']
                    
                    t_time = task['_trigger_dt'].replace(year=now.year, month=now.month, day=now.day)
                    
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
                        if not was_ready and task['status'] == "waiting":
                            logger.warning(f"⚠️ 任务 {task['id']} 跳过预热直接进入抢座，发包可能会延迟")
                        task['status'] = "snatching"
                        # 🎯 修复 Bug #3: 移除 .copy()，确保状态更新能写回 self.tasks 并被持久化
                        tasks_to_snatch.append((task, was_ready, trigger_ts))

            for task in tasks_to_warmup: self._run_task_warmup(task)
            for task, skip, t_ts in tasks_to_snatch: self._run_task_snatch(task, skip, t_ts)
            # 🎯 修复高危 Bug #1: 缩短轮询间隔，防止错过抢座的最佳触发时间
            time.sleep(0.05) 

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
                # 修复 Bug #3: 预热失败时，同账号所有 warming 任务均回退
                with self.lock:
                    for t in self.tasks:
                        if t['username'] == u and t['status'] == 'warming':
                            t['status'] = "waiting"
                logger.warning(f"⚠️ 账号 {u} 预热失败，已重置相关任务状态")
        threading.Thread(target=_worker, daemon=True).start()

    def _run_task_snatch(self, task, skip_refresh, t_ts):
        def _worker():
            bot = self._get_bot(task['username'])
            
            # 🎯 统一发令枪逻辑
            firing_event = None
            if t_ts:
                with self.lock:
                    if t_ts not in self.firing_events:
                        self.firing_events[t_ts] = threading.Event()
                        # 启动一个专门的计时线程负责发令
                        rtt = 0.05
                        target_local = t_ts - self.time_offset - (rtt / 2) + 0.02
                        threading.Thread(target=self._trigger_firing_event, args=(t_ts, target_local), daemon=True).start()
                    firing_event = self.firing_events[t_ts]

            params = {
                "username": task['username'], "password": task['password'],
                "floor": task['floor'], "seat_list": task['seat_list'],
                "seat_display": task['seat_display'],
                "date_offset": task['dateOffset'], "start_time": task['startTime'], "end_time": task['endTime'],
                "trigger_ts": t_ts, "time_offset": self.time_offset, "rtt": 0.05,
                "preferred_seat": task.get('preferred_seat', ""),
                "firing_event": firing_event,
                "synced_now": datetime.datetime.fromtimestamp(time.time() + self.time_offset)
            }
            
            # 发送冲击通知
            target_date = (datetime.datetime.now() + datetime.timedelta(days=task['dateOffset'])).strftime("%Y-%m-%d")
            bot.notify(False, custom_title="🚀 开始冲击", 
                       custom_msg=f"正在对 {task['seat_display']} 发起抢座\n目标日期：{target_date}")
            
            success = False
            for i in range(2):
                if i > 0:
                    time.sleep(1.5)
                    # 修复 Bug #6: 重试时刷新 seat_list，剔除第一轮中确认不可用的座位
                    with self.lock:
                        params['seat_list'] = self._build_seat_list(task['floor'], task['seat_display'], task.get('preferred_seat', ""), bot)
                    
                    # 发送重试通知
                    bot.notify(False, custom_title=f"⚠️ 第{i}次重试", 
                               custom_msg=f"第一轮未抢到，正在发起第 {i} 次重试...")
                
                res = bot.snatch_action(params, skip_refresh=(skip_refresh or i > 0 or bot.is_warmed_up))
                if isinstance(res, str): # 如果返回了具体座位号
                    success = res
                    break
                elif isinstance(res, dict): # 如果返回了失败统计
                    with self.lock:
                        for k in ["busy", "occupied", "other"]:
                            task["fail_reason_stats"][k] += res.get(k, 0)
            
            with self.lock:
                # 修复 Bug #7: 使用同步后的服务器时间作为运行标记，确保逻辑一致
                current_now = datetime.datetime.fromtimestamp(time.time() + self.time_offset)
                task['last_run_date'] = current_now.strftime("%Y-%m-%d")
                if success:
                    task['status'] = "completed"
                    # 智能锁定：记录真实抢到的座位号
                    task['preferred_seat'] = success
                else:
                    task['status'] = "failed"
                    bot.notify(False, custom_title="❌ 抢座失败",
                               custom_msg=f"连续 2 轮未中已停止\n场馆：{task['floor']}\n座位：{task['seat_display']}")
                
                # 记录结构化日志
                self._log_structured_event(task, success)
            self.save_tasks()

    def _trigger_firing_event(self, t_ts, target_local):
        """精准发令：由单一线程控制时间，触发该时刻的所有并发任务"""
        wait_time = target_local - time.time()
        if wait_time > 0:
            if wait_time > 0.05:
                time.sleep(wait_time - 0.02)
            # 最后 20ms 微秒级等待
            while time.time() < target_local:
                time.sleep(0.001)
        
        with self.lock:
            if t_ts in self.firing_events:
                self.firing_events[t_ts].set()
                # 1秒后清理，给所有线程留够反应时间
                threading.Timer(1.0, lambda: self.firing_events.pop(t_ts, None)).start()

    def get_shared_browser(self):
        """
        获取当前线程的浏览器实例
        修复 Bug #2: Playwright sync_api 不支持多线程跨线程调用 context/page
        通过 threading.local 确保每个线程拥有独立的 Playwright 实例和浏览器
        """
        from playwright.sync_api import sync_playwright
        if not hasattr(self._local, 'pw'):
            self._local.pw = sync_playwright().start()
            self._local.browser = self._local.pw.chromium.launch(headless=True)
        return self._local.browser

    def _log_structured_event(self, task, success):
        """记录结构化 JSON 日志用于后期统计（含大小限制自动轮转）"""
        log_dir = "logs"
        if not os.path.exists(log_dir): os.makedirs(log_dir)
        stats_file = os.path.join(log_dir, "stats.json")
        
        event = {
            "timestamp": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "task_id": task['id'],
            "username": task['username'],
            "floor": task['floor'],
            "target": task['seat_display'],
            "success": bool(success),
            "result_seat": success if success else None,
            "stats": task.get('fail_reason_stats', {})
        }
        
        try:
            # 🎯 大小限制：超过 10MB 自动轮转，防止无限增长
            max_size = 10 * 1024 * 1024  # 10MB
            if os.path.exists(stats_file) and os.path.getsize(stats_file) > max_size:
                archive_name = os.path.join(log_dir, f"stats_{datetime.datetime.now().strftime('%Y%m%d_%H%M%S')}.json")
                os.rename(stats_file, archive_name)
                logger.info(f"📦 stats.json 已轮转归档为 {archive_name}")
            
            with open(stats_file, "a", encoding="utf-8") as f:
                f.write(json.dumps(event, ensure_ascii=False) + "\n")
        except: pass
