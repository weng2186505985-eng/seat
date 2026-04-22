
import requests
import logging
import threading
import config

logger = logging.getLogger(__name__)

class Notifier:
    """统一通知管理模块"""
    
    @staticmethod
    def send_bark(title, content, key):
        if not key:
            return
        # Bark URL 格式: https://api.day.app/{key}/{title}/{content}
        # 也可以使用 POST 方式支持更多参数
        url = f"https://api.day.app/{key}"
        try:
            resp = requests.post(url, data={
                "title": title,
                "body": content,
                "group": "HDU-Seat",
                "icon": "https://img.icons8.com/fluency/96/000000/bookmark.png"
            }, timeout=10)
            if resp.status_code != 200:
                logger.error(f"Bark notification failed with status {resp.status_code}: {resp.text}")
        except Exception as e:
            logger.error(f"Bark notification error: {e}")

    @staticmethod
    def send_serverchan(title, content, key):
        if not key:
            return
        url = f"https://sctapi.ftqq.com/{key}.send"
        try:
            resp = requests.post(url, data={"title": title, "desp": content}, timeout=10)
            if resp.status_code != 200:
                logger.error(f"ServerChan notification failed with status {resp.status_code}: {resp.text}")
        except Exception as e:
            logger.error(f"ServerChan notification error: {e}")

    @classmethod
    def notify(cls, title, content):
        """统一推送入口"""
        # 获取配置
        sckey = getattr(config, 'SCKEY', None)
        bark_key = getattr(config, 'BARK_KEY', None)
        
        if not sckey and not bark_key:
            logger.debug("No notification keys configured, skipping...")
            return

        def _worker():
            # 并发推送
            threads = []
            if sckey:
                t1 = threading.Thread(target=cls.send_serverchan, args=(title, content, sckey))
                threads.append(t1)
            if bark_key:
                t2 = threading.Thread(target=cls.send_bark, args=(title, content, bark_key))
                threads.append(t2)
            
            for t in threads:
                t.start()
            for t in threads:
                t.join()

        # 异步执行，不阻塞主流程
        threading.Thread(target=_worker, daemon=True).start()

def notify(title, content):
    """便捷函数"""
    Notifier.notify(title, content)
