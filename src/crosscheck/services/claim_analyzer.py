import re
from datetime import datetime
from zoneinfo import ZoneInfo

from crosscheck.domain.models import Claim


def analyze_claim(text: str) -> Claim:
    current_year = datetime.now(ZoneInfo("Asia/Shanghai")).year
    year = current_year
    if "明年" in text:
        year = current_year + 1
    elif "去年" in text:
        year = current_year - 1
    explicit_year = re.search(r"(\d{4})年", text)
    if explicit_year:
        year = int(explicit_year.group(1))
    month_match = re.search(r"(\d{1,2})月(?:份)?", text)
    if month_match:
        time_value = f"{year}年{int(month_match.group(1))}月"
    elif "今年" in text or "明年" in text or "去年" in text or explicit_year:
        time_value = f"{year}年"
    else:
        time_value = None
    if any(alias in text for alias in ("北理工", "北京理工", "北京理工大学")):
        subject = "北京理工大学"
    else:
        subject = None
    location_match = re.search(r"(北京|上海|广州|深圳|杭州|南京|全国|全市|某市)", text)
    location = location_match.group(1) if location_match else None
    if subject is None and location in {"北京", "上海", "广州", "深圳", "杭州", "南京"}:
        subject = f"{location}市"
    duration_match = re.search(r"(\d+)\s*天", text)
    if not duration_match:
        chinese_numbers = {"一": 1, "二": 2, "三": 3, "四": 4, "五": 5, "六": 6, "七": 7, "八": 8, "九": 9, "十": 10}
        chinese_duration = re.search(r"([一二三四五六七八九十]+)\s*天", text)
        if chinese_duration:
            duration_match = chinese_duration
            duration_value = chinese_numbers.get(chinese_duration.group(1))
        else:
            duration_value = None
    else:
        duration_value = int(duration_match.group(1))
    objects = ("无人机", "电动自行车", "电动车", "汽车", "产品", "航班", "列车")
    object_match = next((item for item in objects if item in text), None)
    action_map = {
        "禁飞": "禁飞", "禁止飞行": "禁飞", "禁行": "禁行", "禁止通行": "禁行",
        "放假": "放假", "免费": "免费", "取消": "取消", "停运": "停运",
        "封闭": "封闭", "开放": "开放", "上线": "上线", "下架": "下架",
    }
    action = next((value for key, value in action_map.items() if key in text), None)
    if action is None and re.search(r"禁止[^。；]{0,20}上路", text):
        action = "禁行"
    event = "国庆" if "国庆" in text else ("中秋" if "中秋" in text else None)
    scope = "全面" if any(word in text for word in ("所有", "全面", "全部", "一律")) else None
    if event is None and object_match and action:
        event = f"{object_match}{action}"
    return Claim(text=text, subject=subject, action=action, time=time_value, location=location, scope=scope, event=event, duration_days=duration_value, object=object_match)
