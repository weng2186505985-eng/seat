import time
import datetime
import logging
import requests
import json
import os
import re
import random
import threading
from playwright.sync_api import sync_playwright
import config

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

    def notify(self, success, seat_name="", custom_msg="", custom_title=""):
        """Server酱推送：支持自定义标题和内容"""
        if not config.SCKEY: return
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
                        
                        cookies = context.cookies()
                        self.current_cookies = "; ".join([f"{c['name']}={c['value']}" for c in cookies])
                        content = page.content()
                        token_match = re.search(r'api-token["\']\s*:\s*["\']([^"\']+)["\']', content)
                        uid_match = re.search(r'uid["\']\s*:\s*["\'](\d+)["\']', content)
                        
                        if token_match: self.api_token = token_match.group(1)
                        if uid_match: self.user_id = uid_match.group(1)
                        self.is_warmed_up = True
                        return True
        except Exception as e:
            logger.error(f"❌ 凭证同步异常 (Playwright): {e}")
            return False

    def snatch_action(self, task_params, skip_refresh=False):
        if not skip_refresh or not self.api_token:
            if not self.refresh_credentials(task_params['username'], task_params['password']):
                return False

        time.sleep(random.uniform(0.3, 2.0))

        hall = task_params['floor']
        seat_list = task_params['seat_list']
        target_date = (datetime.datetime.now() + datetime.timedelta(days=task_params['date_offset'])).strftime("%Y-%m-%d")
        start_ts = int(datetime.datetime.strptime(f"{target_date} {task_params['start_time']}", "%Y-%m-%d %H:%M").timestamp())
        end_ts = int(datetime.datetime.strptime(f"{target_date} {task_params['end_time']}", "%Y-%m-%d %H:%M").timestamp())
        dur_sec = end_ts - start_ts
        
        headers = {
            "api-token": self.api_token,
            "Cookie": self.current_cookies,
            "User-Agent": random.choice(self.ua_list),
            "Referer": "https://hdu.huitu.zhishulib.com/"
        }
        url = "https://hdu.huitu.zhishulib.com/Seat/Index/bookSeats?LAB_JSON=1"

        import concurrent.futures
        success_event = threading.Event()
        success_name = [""]

        def try_book(item, is_retry=False):
            if success_event.is_set(): return
            if not is_retry: time.sleep(random.uniform(0.5, 1.5))
            
            name, s_id = item
            if name in self.blacklist: return

            data = {"beginTime": start_ts, "duration": dur_sec, "seats[0]": s_id, "seatBookers[0]": self.user_id}
            try:
                resp = requests.post(url, data=data, headers=headers, timeout=10)
                try:
                    res_json = resp.json()
                except: return False

                msg = res_json.get('msg') or res_json.get('message') or str(res_json)
                
                if "成功" in msg:
                    logger.info(f"🎊 【{name}号】预约成功！")
                    success_name[0] = name
                    success_event.set()
                    return True
                elif ("频繁" in msg or "太快" in msg) and not is_retry:
                    time.sleep(2)
                    return try_book(item, is_retry=True)
                elif "必须在预约人列表" in msg:
                    self.blacklist.add(name)
            except: pass
            return False

        with concurrent.futures.ThreadPoolExecutor(max_workers=4) as executor:
            executor.map(try_book, seat_list)
        
        if success_event.is_set():
            # 修改通知描述，包含场馆和日期
            s_name = success_name[0]
            desc = f"座位：{s_name}\n场馆：{hall}\n日期：{target_date}"
            self.notify(True, seat_name=s_name, custom_msg=desc) 
            return s_name # 返回具体座位号
        return False
