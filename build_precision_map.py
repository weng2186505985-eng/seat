import json

def build_precision_map():
    print("Extracting Gwu E-Hall and Songyun Yuntu maps...")
    try:
        with open("debug_seats.json", "r", encoding="utf-8") as f:
            data = json.load(f)
        
        target_map = {}
        
        # 定义你需要的核心场馆
        targets = ["格物E堂（二楼东）", "宋韵云图（四楼）"]
        
        def extract_recursive(obj):
            if isinstance(obj, dict):
                room_name = obj.get('roomName', '')
                # 如果这个节点是我们要找的场馆
                if room_name in targets:
                    pois = obj.get('seatMap', {}).get('POIs', [])
                    if pois:
                        target_map[room_name] = {}
                        for poi in pois:
                            if isinstance(poi, dict) and 'title' in poi and 'id' in poi:
                                target_map[room_name][poi['title']] = poi['id']
                        print(f"Captured Hall: {room_name}, with {len(target_map[room_name])} seats.")
                
                for v in obj.values():
                    extract_recursive(v)
            elif isinstance(obj, list):
                for item in obj:
                    extract_recursive(item)

        extract_recursive(data)
        
        if target_map:
            with open("seat_map.json", "w", encoding="utf-8") as f:
                json.dump(target_map, f, ensure_ascii=False, indent=2)
            print(f"精准地图已更新！包含场馆: {list(target_map.keys())}")
        else:
            print("❌ 错误：在数据中未找到指定的场馆名，请确认名称是否完全一致。")
            
    except Exception as e:
        print(f"解析出错: {e}")

if __name__ == "__main__":
    build_precision_map()
