import time
import datetime
import logging
import requests
import json
import os
import re
import random
from playwright.sync_api import sync_playwright
import config

logger = logging.getLogger(__name__)

class UltraFastBot:
    def __init__(self):
        self.api_token = ""
        self.user_id = ""
        self.current_cookies = config.COOKIE
        self.is_warmed_up = False
        self.blacklist = set() # 记录有特殊限制的座位
        self.ua_list = [
            "Mozilla/5.0 (iPhone; CPU iPhone OS 16_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/16.0 Mobile/15E148 Safari/604.1",
            "Mozilla/5.0 (Linux; Android 13; Pixel 7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/116.0.0.0 Mobile Safari/537.36",
            "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Mobile/15E148 Safari/604.1"
        ]

    def notify(self, success, seat_name=""):
        if not config.SCKEY: return
        url = f"https://sctapi.ftqq.com/{config.SCKEY}.send"
        title = "🎉 HDU 抢座成功！" if success else "❌ HDU 抢座失败"
        content = f"座位：{seat_name}\n日期：{datetime.datetime.now().strftime('%Y-%m-%d')}" if success else "本次尝试未抢到目标座位。"
        try:
            requests.post(url, data={"title": title, "desp": content}, timeout=5)
        except: pass

    def refresh_credentials(self, username, password):
        logger.info(f"📡 正在为账号 {username} 同步凭证...")
        try:
            with sync_playwright() as p:
                iphone = p.devices['iPhone 14']
                browser = p.chromium.launch(headless=True)
                # 修复：不再传 user_agent，避免与 iphone 内置 UA 冲突
                context = browser.new_context(**iphone)
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
                    browser.close()
                    return False
                
                cookies = context.cookies()
                self.current_cookies = "; ".join([f"{c['name']}={c['value']}" for c in cookies])
                content = page.content()
                token_match = re.search(r'api-token["\']\s*:\s*["\']([^"\']+)["\']', content)
                uid_match = re.search(r'uid["\']\s*:\s*["\'](\d+)["\']', content)
                
                if token_match: self.api_token = token_match.group(1)
                if uid_match: self.user_id = uid_match.group(1)
                
                browser.close()
                self.is_warmed_up = True
                return True
        except Exception as e:
            logger.error(f"❌ 凭证同步异常: {e}")
            return False

    def snatch_action(self, task_params, skip_refresh=False):
        # 凭证兜底检查
        if (skip_refresh or not self.api_token) and not self.api_token:
            logger.warning("⚠️ 发现 Token 为空，强制重新登录同步...")
            if not self.refresh_credentials(task_params['username'], task_params['password']):
                return False

        logger.info("⏳ 正在进行触发前抖动...")
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
        success_flag = [False]
        success_name = [""]

        def try_book(item, is_retry=False):
            if success_flag[0]: return
            
            # 线程错峰：0.5s - 1.5s
            if not is_retry:
                time.sleep(random.uniform(0.5, 1.5))
            
            name, s_id = item
            if name in self.blacklist: return

            data = {"beginTime": start_ts, "duration": dur_sec, "seats[0]": s_id, "seatBookers[0]": self.user_id}
            try:
                resp = requests.post(url, data=data, headers=headers, timeout=10)
                res_json = resp.json()
                msg = res_json.get('msg') or res_json.get('message') or str(res_json)
                
                if "成功" in msg:
                    logger.info(f"🎊 【{name}号】预约成功！")
                    success_flag[0] = True
                    success_name[0] = name
                    return True
                elif "频繁" in msg and not is_retry:
                    logger.warning(f"⏳ {name}号请求过快，2秒后自动重试一次...")
                    time.sleep(2)
                    return try_book(item, is_retry=True)
                elif "必须在预约人列表" in msg:
                    logger.error(f"🚫 {name}号疑似为特权白名单座位，已加入本地黑名单。")
                    self.blacklist.add(name)
                else:
                    logger.warning(f"💡 {name}号: {msg}")
            except Exception as e:
                logger.error(f"⚠️ {name}号请求异常: {e}")
            return False

        with concurrent.futures.ThreadPoolExecutor(max_workers=4) as executor:
            executor.map(try_book, seat_list)
        
        if success_flag[0]:
            self.notify(True, success_name[0])
            return True
        return False
