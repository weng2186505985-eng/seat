import time
import datetime
import logging
import requests
import json
import os
import re
from playwright.sync_api import sync_playwright
import config

logger = logging.getLogger(__name__)

class UltraFastBot:
    def __init__(self):
        self.api_token = ""
        self.user_id = ""
        self.current_cookies = config.COOKIE
        self.seat_map = {}
        self.is_warmed_up = False
        self.load_map()

    def load_map(self):
        if os.path.exists("seat_map.json"):
            with open("seat_map.json", "r", encoding="utf-8") as f:
                self.seat_map = json.load(f)

    def refresh_credentials(self):
        logger.info("📡 正在同步登录凭证 (移动端 + 回车提交模式)...")
        try:
            with sync_playwright() as p:
                iphone = p.devices['iPhone 14']
                browser = p.chromium.launch(headless=True)
                context = browser.new_context(**iphone)
                page = context.new_page()
                
                # 1. 访问登录入口
                page.goto("https://hdu.huitu.zhishulib.com/User/Index/hduCASLogin")
                time.sleep(2)
                
                # 2. 填写表单
                try:
                    user_input = page.get_by_placeholder(re.compile(r"学工号|账号"))
                    pass_input = page.get_by_placeholder(re.compile(r"密码"))
                    
                    user_input.fill(config.USERNAME)
                    pass_input.fill(config.PASSWORD)
                    
                    # 3. 直接按回车提交，避开按钮寻找难题
                    logger.info("⌨️ 正在模拟回车键提交表单...")
                    pass_input.press("Enter")
                    
                    # 4. 等待跳转 (容错逻辑：如果回车没跳，再点一下按钮)
                    try:
                        page.wait_for_url("**/Category/list**", timeout=8000)
                    except:
                        logger.info("⚠️ 回车似乎无效，尝试暴力点击登录按钮...")
                        # 尝试所有包含“登”字样的可点击元素
                        page.locator('button:has-text("登"), [role="button"]:has-text("登"), div:has-text("登录")').first.click()
                        page.wait_for_url("**/Category/list**", timeout=15000)
                    
                    logger.info("🔑 登录成功！")
                except Exception as e:
                    page.screenshot(path="login_final_error.png")
                    logger.error(f"❌ 登录最终尝试失败，详见 login_final_error.png: {e}")
                    browser.close()
                    return False
                
                # 5. 提取凭证
                cookies = context.cookies()
                self.current_cookies = "; ".join([f"{c['name']}={c['value']}" for c in cookies])
                content = page.content()
                token_match = re.search(r'api-token["\']\s*:\s*["\']([^"\']+)["\']', content)
                uid_match = re.search(r'uid["\']\s*:\s*["\'](\d+)["\']', content)
                
                if token_match: self.api_token = token_match.group(1)
                if uid_match: self.user_id = uid_match.group(1)
                
                browser.close()
                logger.info(f"✅ 凭证就绪 (UID: {self.user_id})")
                self.is_warmed_up = True
                return True
        except Exception as e:
            logger.error(f"❌ 凭证同步失败: {e}")
            return False

    def snatch_action(self, skip_refresh=False):
        if not skip_refresh:
            if not self.refresh_credentials(): return

        hall = config.PREFERRED_FLOOR
        if hall not in self.seat_map:
            logger.error(f"❌ 场馆【{hall}】未载入")
            return

        target_ids = []
        for name in config.PREFERRED_SEATS:
            if name in self.seat_map[hall]:
                target_ids.append((name, self.seat_map[hall][name]))
        
        target_date = (datetime.datetime.now() + datetime.timedelta(days=config.RESERVE_DAY_OFFSET)).strftime("%Y-%m-%d")
        start_ts = int(datetime.datetime.strptime(f"{target_date} {config.RESERVE_START_TIME}", "%Y-%m-%d %H:%M").timestamp())
        dur_sec = int((datetime.datetime.strptime(f"{target_date} {config.RESERVE_END_TIME}", "%Y-%m-%d %H:%M") - datetime.datetime.strptime(f"{target_date} {config.RESERVE_START_TIME}", "%Y-%m-%d %H:%M")).total_seconds())
        
        headers = {
            "api-token": self.api_token,
            "Cookie": self.current_cookies,
            "User-Agent": "Mozilla/5.0 (iPhone; CPU iPhone OS 16_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/16.0 Mobile/15E148 Safari/604.1",
            "Referer": "https://hdu.huitu.zhishulib.com/"
        }
        url = "https://hdu.huitu.zhishulib.com/Seat/Index/bookSeats?LAB_JSON=1"

        logger.info(f"🔥 零延迟发包启动！")
        
        import concurrent.futures
        def try_book(item):
            name, s_id = item
            data = {"beginTime": start_ts, "duration": dur_sec, "seats[0]": s_id, "seatBookers[0]": self.user_id}
            try:
                resp = requests.post(url, data=data, headers=headers, timeout=10)
                res_json = resp.json()
                msg = res_json.get('msg') or res_json.get('message') or str(res_json)
                if "成功" in msg:
                    logger.info(f"🎊 【{name}号】预约成功！")
                    return True
                else:
                    logger.warning(f"💡 {name}号: {msg}")
            except Exception as e:
                logger.error(f"⚠️ {name}号请求异常: {e}")
            return False

        with concurrent.futures.ThreadPoolExecutor(max_workers=8) as executor:
            executor.map(try_book, target_ids)

    def wait_for_schedule(self):
        target_time_str = config.SCHEDULE_TIME
        logger.info(f"⏰ 守候中: {target_time_str}...")
        self.is_warmed_up = False

        while True:
            now = datetime.datetime.now()
            target_time = datetime.datetime.strptime(target_time_str, "%H:%M:%S").replace(year=now.year, month=now.month, day=now.day)
            if target_time < now - datetime.timedelta(seconds=2): target_time += datetime.timedelta(days=1)
            
            diff = (target_time - now).total_seconds()
            if 0 < diff < 1200 and not self.is_warmed_up:
                logger.info("⚔️ 开启战前预热...")
                self.refresh_credentials()
            
            if diff <= 0:
                self.snatch_action(skip_refresh=self.is_warmed_up)
                break
            time.sleep(0.01 if diff < 1 else 0.5)
