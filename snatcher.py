import time
import datetime
import logging
import requests
from requests.adapters import HTTPAdapter
import json
import os
import re
import random
import threading
import concurrent.futures
import queue
try:
    from curl_cffi import requests as cffi_requests
except ImportError:
    cffi_requests = None
from playwright.sync_api import sync_playwright
import config
import logger_config

logger = logging.getLogger(__name__)

class UltraFastBot:
    def __init__(self, browser_provider=None):
        self._use_cffi = bool(cffi_requests)
        if self._use_cffi:
            # 🎯 curl_cffi 的 Session 不是线程安全的，使用 threading.local 隔离
            self._thread_local = threading.local()
            self.session = cffi_requests.Session()  # 主线程 session（用于预热、登录等单线程操作）
        else:
            self.session = requests.Session()
        # 🎯 UA 抖动库：模拟不同版本的 iOS 设备
        self.ua_list = [
            "Mozilla/5.0 (iPhone; CPU iPhone OS 16_5 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/16.5 Mobile/15E148 Safari/604.1",
            "Mozilla/5.0 (iPhone; CPU iPhone OS 16_6 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/16.6 Mobile/15E148 Safari/604.1",
            "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Mobile/15E148 Safari/604.1",
            "Mozilla/5.0 (iPhone; CPU iPhone OS 17_1 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.1 Mobile/15E148 Safari/604.1"
        ]
        self.fixed_ua = random.choice(self.ua_list)
        
        # 🎯 curl_cffi 指纹伪装 (impersonate chrome/safari)
        if hasattr(self.session, "impersonate"):
            self.session.impersonate = "safari15_5" 
            
        self.api_token = ""
        self.user_id = ""
        self.current_cookies = ""
        self.blacklist = set()
        self.state_lock = threading.Lock()
        self.is_warmed_up = False
        self.browser_provider = browser_provider # 🎯 传入共享浏览器获取函数
        
        # 配置连接池：保持长连接，极大缩短 TLS 握手耗时
        adapter = HTTPAdapter(pool_connections=10, pool_maxsize=10)
        self.session.mount("https://", adapter)
        self.session.mount("http://", adapter)

    def _get_thread_session(self):
        """获取当前线程的 session（curl_cffi 模式下每线程独立，requests 模式下共享）"""
        if not self._use_cffi:
            return self.session  # requests.Session 连接池本身是线程安全的
        # curl_cffi: 每个线程独立 session
        if not hasattr(self._thread_local, 'session'):
            s = cffi_requests.Session()
            s.impersonate = "safari15_5"
            self._thread_local.session = s
        return self._thread_local.session

    # --- 浏览器资源管理 ---
    # 移除原本不符合线程安全规范的全局单例浏览器，改为在每个凭证刷新任务中独立启动
    # 虽然启动浏览器有一定开销，但在多账号并发场景下，这能彻底避免 greenlet 跨线程切换导致的崩溃问题。

    def clear_blacklist(self):
        """每日重置黑名单，确保循环任务不会逐日缩减可用池"""
        with self.state_lock:
            self.blacklist.clear()
            logger.info("Blacklist cleared for a new attempt.")

    def warmup_connection(self):
        """预热 TCP 连接，让连接池在触发时刻前已持有活跃连接 (省掉 50-150ms 握手耗时)"""
        url = "https://hdu.huitu.zhishulib.com/Seat/Index/bookSeats?LAB_JSON=1"
        try:
            # 使用 HEAD 请求，仅建立连接不下载内容
            self.session.head(url, timeout=3, headers={"User-Agent": self.fixed_ua})
            logger.info("📡 TCP/TLS connection pre-warmed and ready in pool.")
        except: pass

    def notify(self, success, seat_name="", custom_msg="", custom_title=""):
        """Server酱推送：异步发送，支持自定义标题 and 内容"""
        if not config.SCKEY: return
        
        def _send():
            url = f"https://sctapi.ftqq.com/{config.SCKEY}.send"
            # 优先级：自定义标题 > 默认成功/失败标题
            title = custom_title if custom_title else ("🎉 HDU 抢座成功！" if success else "❌ HDU 抢座最终失败")
            
            if custom_msg:
                content = custom_msg
            elif success:
                content = f"座位：{seat_name}\n日期：{datetime.datetime.now().strftime('%Y-%m-%d')}"
            else:
                content = "连续多次尝试均未抢到目标座位。"
            
            try:
                requests.post(url, data={"title": title, "desp": content}, timeout=5)
            except Exception as e:
                logger.error(f"⚠️ 推送失败: {e}")
        
        # 开启守护线程发送，绝不阻塞主业务逻辑
        threading.Thread(target=_send, daemon=True).start()

    def refresh_credentials(self, username, password):
        """
        同步登录凭证：使用独立浏览器进程获取 Cookie 和 Token
        修复了原有的 Playwright 多线程 greenlet 报错
        """
        logger.info(f"📡 正在为账号 {username} 同步凭证...")
        p_standalone = None
        is_shared = False
        try:
            # 🎯 资源优化：优先使用 TaskManager 提供的共享浏览器引擎
            browser = None
            if self.browser_provider:
                try:
                    browser = self.browser_provider()
                    is_shared = True
                except: pass
            
            # 如果没有共享引擎 (单机运行)，回退到独立启动
            if not browser:
                from playwright.sync_api import sync_playwright
                p_standalone = sync_playwright().start()
                browser = p_standalone.chromium.launch(headless=True)
                is_shared = False

            try:
                # 依然保持 context 隔离
                iphone = {"user_agent": self.fixed_ua, "viewport": {"width": 390, "height": 844}}
                with browser.new_context(**iphone) as context:
                    page = context.new_page()
                    page.goto("https://hdu.huitu.zhishulib.com/User/Index/hduCASLogin")
                    # 随机等待模拟真人
                    page.wait_for_timeout(random.randint(1000, 2000))
                    
                    # 自动填写登录
                    user_input = page.get_by_placeholder(re.compile(r"学工号|账号"))
                    pass_input = page.get_by_placeholder(re.compile(r"密码"))
                    user_input.fill(username)
                    page.wait_for_timeout(random.randint(500, 1200))
                    pass_input.fill(password)
                    page.wait_for_timeout(random.randint(500, 1200))
                    pass_input.press("Enter")
                    
                    # 等待跳转成功
                    page.wait_for_url("**/Category/list**", timeout=15000)
                    page.wait_for_timeout(2000) # 等待 localStorage 写入
                    
                    # --- 多方案凭证提取 ---
                    # 1. 深度扫描 localStorage
                    token_value, uid_value = page.evaluate("""() => {
                        let t = null, u = null;
                        const keys = Object.keys(localStorage);
                        
                        // 优先尝试已知的大对象 Key (针对 Parse 框架等)
                        const userObjKey = keys.find(k => k.includes('currentUser') || k.includes('user_info'));
                        if (userObjKey) {
                            try {
                                const data = JSON.parse(localStorage.getItem(userObjKey));
                                t = data.token || data.api_token || data.sessionToken || data.accessToken || data.apiToken;
                                u = data.uid || data.userId || data.id || data.user_id;
                            } catch(e) {}
                        }

                        // 如果还没找到，遍历所有 Key 查找
                        if (!t || !u) {
                            for (let k of keys) {
                                const val = localStorage.getItem(k);
                                if (!t && (k.toLowerCase().includes('token') || k.toLowerCase().includes('authorization'))) t = val;
                                if (!u && (k.toLowerCase().includes('uid') || k.toLowerCase().includes('userid'))) u = val;
                            }
                        }
                        return [t, u];
                    }""")
                    
                    if not token_value or not uid_value:
                        # 2. 尝试从页面源码正则提取
                        content = page.content()
                        t_match = re.search(r'(?:api-token|token|access_token|Authorization)["\']\s*[:=]\s*["\']([^"\']+)["\']', content, re.I)
                        u_match = re.search(r'(?:uid|userId|user_id|\"id\")["\']\s*[:=]\s*["\'](\d+)["\']', content, re.I)
                        token_value = token_value or (t_match.group(1) if t_match else None)
                        uid_value = uid_value or (u_match.group(1) if u_match else None)
                    
                    with self.state_lock:
                        if token_value: 
                            self.api_token = token_value
                            logger.info(f"✅ 账号 {username} api-token 获取成功")
                        if uid_value: 
                            self.user_id = uid_value
                            logger.info(f"✅ 账号 {username} uid 获取成功")
                        
                        cookies = context.cookies()
                        self.current_cookies = "; ".join([f"{c['name']}={c['value']}" for c in cookies])
                        
                        if token_value and uid_value:
                            self.is_warmed_up = True
                            threading.Thread(target=self.warmup_connection, daemon=True).start()
                            return True
                    return False
            except Exception as e:
                logger.error(f"❌ 账号 {username} 登录操作失败: {e}")
                return False
            finally:
                # 🎯 关键修复：只有在非共享模式下才关闭浏览器，防止干掉全局单例
                if not is_shared and browser:
                    browser.close()
                if p_standalone:
                    p_standalone.stop()
        except Exception as e:
            logger.error(f"❌ 凭证同步组件异常: {e}", exc_info=True)
            return False

    def snatch_action(self, task_params, skip_refresh=False):
        if not skip_refresh or not self.api_token:
            if not self.refresh_credentials(task_params['username'], task_params['password']):
                return False

        # 提前打靶逻辑
        trigger_ts = task_params.get('trigger_ts')
        if trigger_ts:
            rtt = task_params.get('rtt', 0.05)
            offset = task_params.get('time_offset', 0)
            # 目标本地时间 = 目标服务器时间 - 偏差 - (RTT/2) + 0.02s(安全余量)
            target_local = trigger_ts - offset - (rtt / 2) + 0.02
            
            wait_time = target_local - time.time()
            if wait_time > 0:
                logger.info(f"⏳ 正在精确等待打靶时刻... (预估等待 {wait_time:.3f}s, 已包含0.02s余量)")
                
                # 🎯 优先使用统一发令枪 Event，减少多线程抢占 CPU
                firing_event = task_params.get('firing_event')
                if firing_event:
                    firing_event.wait(timeout=wait_time + 1)
                else:
                    # 如果没有发令枪，回退到微睡眠模式
                    if wait_time > 0.05:
                        time.sleep(wait_time - 0.02)
                    while time.time() < target_local:
                        time.sleep(0.001) # 🎯 修复：改用微秒级休眠，保护 CPU
        else:
            time.sleep(random.uniform(0.1, 0.5))

        hall = task_params['floor']
        seat_list = task_params['seat_list']
        synced_now = task_params.get('synced_now', datetime.datetime.now())
        target_date = (synced_now + datetime.timedelta(days=task_params['date_offset'])).strftime("%Y-%m-%d")
        start_ts = int(datetime.datetime.strptime(f"{target_date} {task_params['start_time']}", "%Y-%m-%d %H:%M").timestamp())
        end_ts = int(datetime.datetime.strptime(f"{target_date} {task_params['end_time']}", "%Y-%m-%d %H:%M").timestamp())
        dur_sec = end_ts - start_ts
        
        url = "https://hdu.huitu.zhishulib.com/Seat/Index/bookSeats?LAB_JSON=1"
        
        pref = task_params.get('preferred_seat')
        # 智能随机化：如果明确有“首选座”，确保其排在第一，其余乱序
        if pref and any(item[0] == pref for item in seat_list):
            # 提取首选座项
            first = next(item for item in seat_list if item[0] == pref)
            # 提取非首选座项
            others = [item for item in seat_list if item[0] != pref]
            random.shuffle(others)
            seat_list = [first] + others
        else:
            random.shuffle(seat_list)

        success_event = threading.Event()
        success_name_q = queue.Queue(maxsize=1) # 使用线程安全队列存储成功的座位号
        fail_stats = {"busy": 0, "occupied": 0, "other": 0}
        stats_lock = threading.Lock()
        current_trace_id = logger_config.get_trace_id()

        # 预先快照状态，减少锁竞争
        with self.state_lock:
            snap_token = self.api_token
            snap_cookies = self.current_cookies
            snap_uid = self.user_id

        def try_book(item):
            logger_config.set_trace_id(current_trace_id)
            name, s_id = item
            
            try:
                for attempt in range(2): # 显式循环重试，替代递归
                    if success_event.is_set(): return
                    
                    start_time = time.time()
                    try:
                        # 动态生成请求头
                        current_headers = {
                            "api-token": snap_token,
                            "Cookie": snap_cookies,
                            "User-Agent": self.fixed_ua,
                            "Referer": "https://hdu.huitu.zhishulib.com/",
                            "X-Requested-With": "XMLHttpRequest"
                        }

                        if attempt == 0: 
                            time.sleep(random.uniform(0.01, 0.05))
                        
                        with self.state_lock:
                            if name in self.blacklist: return

                        data = {"beginTime": start_ts, "duration": dur_sec, "seats[0]": s_id, "seatBookers[0]": snap_uid}
                        
                        try:
                            sess = self._get_thread_session()
                            resp = sess.post(url, data=data, headers=current_headers, timeout=(2.0, 8.0))
                            res_json = resp.json()
                        except Exception as e:
                            logger.warning(f"⚠️ 【{name}号】网络请求或解析失败 (重试 {attempt}): {e}")
                            continue 

                        msg = res_json.get('msg') or res_json.get('message') or str(res_json)
                        
                        if "成功" in msg:
                            logger.info(f"🎉 SUCCESS: Seat {name} reserved! (Server: {msg})")
                            try: success_name_q.put_nowait(name)
                            except: pass
                            success_event.set()
                            return True
                        elif any(kw in msg for kw in ["已经预约", "已有预约", "已经有", "已有"]):
                            # 如果检测到“已有预约”，说明此账号已搞定（可能是本线程或并行的其他线程成功的）
                            # 只有在还没有其他线程宣布成功时，才将此座位记录为成功座位
                            if not success_event.is_set():
                                logger.info(f"✅ SUCCESS: Account already has a reservation. (Target: {name}, Server: {msg})")
                                try: success_name_q.put_nowait(name)
                                except: pass
                                success_event.set()
                            else:
                                logger.debug(f"ℹ️ Seat {name} reported 'already reserved', likely another thread won.")
                            return True
                        elif "频繁" in msg or "太快" in msg:
                            with stats_lock: fail_stats["busy"] += 1
                            if attempt == 0:
                                # 进一步缩短间隔，并增加随机扰动防止被风控识别
                                sleep_time = 0.5 + random.uniform(-0.1, 0.1)
                                logger.warning(f"⏳ 【{name}号】操作频繁，等待 {sleep_time:.2f}s 后重试...")
                                time.sleep(sleep_time)
                                continue
                        elif any(kw in msg for kw in ["必须在预约人列表", "已被预约", "已被占用", "该时间段不可预约"]):
                            with stats_lock: fail_stats["occupied"] += 1
                            if not success_event.is_set(): # 修复 Bug #2: 只有在没人成功时才拉黑
                                with self.state_lock:
                                    self.blacklist.add(name)
                                logger.info(f"📍 【{name}号】不可用: {msg}")
                            return False # 🎯 修复 Bug #8: 被占用了直接退出，不用再 retry 第二轮
                        else:
                            with stats_lock: fail_stats["other"] += 1
                            # 记录其他未定义的服务器响应
                            logger.info(f"📡 【{name}号】服务器响应: {msg}")
                    except Exception as e:
                        logger.debug(f"⚠️ 内部处理异常: {e}")
                    finally:
                        duration = (time.time() - start_time) * 1000
                        logger.debug(f"⏱️ 【{name}号】请求耗时: {duration:.2f}ms")
            except Exception as e:
                logger.error(f"⚠️ 线程执行异常: {e}")
            return False

        # 使用并发执行。使用 submit 替代 map 以实现真正的“熔断”提交
        # 使用并发执行。第一批只打最优先的 1-2 个座位，50ms 后再并发打剩余的
        with concurrent.futures.ThreadPoolExecutor(max_workers=5) as executor:
            futures = []
            for i, item in enumerate(seat_list):
                if success_event.is_set(): break 
                
                futures.append(executor.submit(try_book, item))
                
                # 激进策略：如果是首个（最高优）座位，给一个 50ms 的绝对保护期
                if i == 0:
                    time.sleep(0.05)
                # 之后的座位每隔 10ms 提交一个，平滑流量，防止被风控识别为瞬间突发请求
                else:
                    time.sleep(0.01)
                    
            concurrent.futures.wait(futures) 
            # 🎯 优化 Bug #4: 如果已经成功，尝试取消掉线程池中还没开始的任务
            if success_event.is_set():
                executor.shutdown(wait=False, cancel_futures=True)
        
        if success_event.is_set():
            # 修改通知描述，包含场馆和日期
            s_name = success_name_q.get_nowait() if not success_name_q.empty() else "未知"
            desc = f"座位：{s_name}\n场馆：{hall}\n日期：{target_date}"
            self.notify(True, seat_name=s_name, custom_msg=desc) 
            return s_name # 返回具体座位号
        return fail_stats # 失败则返回统计原因
