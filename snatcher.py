import time
import datetime
import logging
import requests
import json
import os
import re
import random
import threading
import concurrent.futures
from playwright.sync_api import sync_playwright
import config
import logger_config

logger = logging.getLogger(__name__)

class UltraFastBot:
    def __init__(self):
        self.api_token = ""
        self.user_id = ""
        self.current_cookies = config.COOKIE
        self.is_warmed_up = False
        self.blacklist = set() 
        self.ua_list = [
            "Mozilla/5.0 (iPhone; CPU iPhone OS 16_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/16.0 Mobile/15E148 Safari/604.1",
            "Mozilla/5.0 (Linux; Android 13; Pixel 7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/116.0.0.0 Mobile Safari/537.36",
            "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Mobile/15E148 Safari/604.1"
        ]
        self.session = requests.Session()
        # 配置连接池：保持长连接，极大缩短 TLS 握手耗时
        adapter = requests.adapters.HTTPAdapter(pool_connections=10, pool_maxsize=10)
        self.session.mount("https://", adapter)
        self.session.mount("http://", adapter)
        self.state_lock = threading.Lock()

    def notify(self, success, seat_name="", custom_msg="", custom_title=""):
        """Server酱推送：异步发送，支持自定义标题和内容"""
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
        logger.info(f"📡 正在为账号 {username} 同步凭证...")
        try:
            with sync_playwright() as p:
                iphone = p.devices['iPhone 14']
                with p.chromium.launch(headless=True) as browser:
                    with browser.new_context(**iphone) as context:
                        page = context.new_page()
                        page.goto("https://hdu.huitu.zhishulib.com/User/Index/hduCASLogin")
                        page.wait_for_timeout(random.randint(1000, 2000))
                        
                        try:
                            user_input = page.get_by_placeholder(re.compile(r"学工号|账号"))
                            pass_input = page.get_by_placeholder(re.compile(r"密码"))
                            user_input.fill(username)
                            page.wait_for_timeout(random.randint(500, 1200))
                            pass_input.fill(password)
                            page.wait_for_timeout(random.randint(500, 1200))
                            pass_input.press("Enter")
                            page.wait_for_url("**/Category/list**", timeout=15000)
                        except Exception as e:
                            page.screenshot(path=f"login_err_{username}.png")
                            logger.error(f"❌ 账号 {username} 登录失败: {e}")
                            return False
                        
                        with self.state_lock:
                            if token_match: self.api_token = token_match.group(1)
                            else: logger.error(f"❌ 账号 {username} 未能在页面中找到 api-token")
                            
                            if uid_match: self.user_id = uid_match.group(1)
                            else: logger.error(f"❌ 账号 {username} 未能在页面中找到 uid")
                            
                            cookies = context.cookies()
                            self.current_cookies = "; ".join([f"{c['name']}={c['value']}" for c in cookies])
                            
                            if token_match and uid_match:
                                self.is_warmed_up = True
                                return True
                            return False
        except Exception as e:
            logger.error(f"❌ 凭证同步异常 (Playwright): {e}", exc_info=True)
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
            # 目标本地时间 = 目标服务器时间 - 偏差 - (RTT/2)
            target_local = trigger_ts - offset - (rtt / 2)
            # 留一点点余量给后续的微小抖动
            wait_time = target_local - time.time() - 0.01 
            if wait_time > 0:
                logger.info(f"⏳ 正在精确等待打靶时刻... (预估等待 {wait_time:.3f}s)")
                time.sleep(wait_time)
        else:
            time.sleep(random.uniform(0.1, 0.5))

        hall = task_params['floor']
        seat_list = task_params['seat_list']
        target_date = (datetime.datetime.now() + datetime.timedelta(days=task_params['date_offset'])).strftime("%Y-%m-%d")
        start_ts = int(datetime.datetime.strptime(f"{target_date} {task_params['start_time']}", "%Y-%m-%d %H:%M").timestamp())
        end_ts = int(datetime.datetime.strptime(f"{target_date} {task_params['end_time']}", "%Y-%m-%d %H:%M").timestamp())
        dur_sec = end_ts - start_ts
        
        url = "https://hdu.huitu.zhishulib.com/Seat/Index/bookSeats?LAB_JSON=1"
        
        pref = task_params.get('preferred_seat')
        # 智能随机化：如果明确有“首选座”，则首选座保持第一，其余乱序；否则全列表随机
        if pref and len(seat_list) > 1 and seat_list[0][0] == pref:
            first = seat_list[0]
            others = seat_list[1:]
            random.shuffle(others)
            seat_list = [first] + others
        else:
            random.shuffle(seat_list)

        success_event = threading.Event()
        success_name = [""]
        current_trace_id = logger_config.get_trace_id()

        # 预先快照状态，减少锁竞争
        with self.state_lock:
            snap_token = self.api_token
            snap_cookies = self.current_cookies
            snap_uid = self.user_id

        def try_book(item):
            logger_config.set_trace_id(current_trace_id)
            name, s_id = item
            
            for attempt in range(2): # 显式循环重试，替代递归
                if success_event.is_set(): return
                
                start_time = time.time()
                try:
                    # 动态生成请求头
                    current_headers = {
                        "api-token": snap_token,
                        "Cookie": snap_cookies,
                        "User-Agent": random.choice(self.ua_list),
                        "Referer": "https://hdu.huitu.zhishulib.com/",
                        "X-Requested-With": "XMLHttpRequest"
                    }

                    if attempt == 0: 
                        time.sleep(random.uniform(0.01, 0.05))
                    
                    with self.state_lock:
                        if name in self.blacklist: return

                    data = {"beginTime": start_ts, "duration": dur_sec, "seats[0]": s_id, "seatBookers[0]": snap_uid}
                    
                    try:
                        resp = self.session.post(url, data=data, headers=current_headers, timeout=(2.0, 8.0))
                        res_json = resp.json()
                    except Exception as e:
                        logger.warning(f"⚠️ 【{name}号】网络请求或解析失败 (重试 {attempt}): {e}")
                        continue 

                    msg = res_json.get('msg') or res_json.get('message') or str(res_json)
                    
                    if any(kw in msg for kw in ["成功", "已经预约", "已有预约", "已经有", "已有"]):
                        logger.info(f"🎊 【{name}号】预约成功！(服务器返回: {msg})")
                        success_name[0] = name
                        success_event.set()
                        return True
                    elif "频繁" in msg or "太快" in msg:
                        if attempt == 0:
                            logger.warning(f"⏳ 【{name}号】操作频繁，等待重试...")
                            time.sleep(1.5)
                            continue
                    elif any(kw in msg for kw in ["必须在预约人列表", "已被预约", "已被占用", "该时间段不可预约"]):
                        with self.state_lock:
                            self.blacklist.add(name)
                        logger.info(f"📍 【{name}号】不可用: {msg}")
                    else:
                        # 记录其他未定义的服务器响应
                        logger.info(f"📡 【{name}号】服务器响应: {msg}")
                except Exception as e:
                    logger.debug(f"⚠️ 请求异常: {e}")
                
                duration = (time.time() - start_time) * 1000
                logger.debug(f"⏱️ 【{name}号】请求耗时: {duration:.2f}ms")
                return False
            except Exception as e:
                logger.error(f"⚠️ 线程执行异常: {e}")
                return False

        # 使用并发执行。对于大多数场馆，3个并发线程足以在毫秒级覆盖首选座及备选座
        with concurrent.futures.ThreadPoolExecutor(max_workers=3) as executor:
            executor.map(try_book, seat_list)
        
        if success_event.is_set():
            # 修改通知描述，包含场馆和日期
            s_name = success_name[0]
            desc = f"座位：{s_name}\n场馆：{hall}\n日期：{target_date}"
            self.notify(True, seat_name=s_name, custom_msg=desc) 
            return s_name # 返回具体座位号
        return False
