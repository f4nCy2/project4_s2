"""自然语言任务解析器

功能：
  - 内置室内场景点位坐标库（客厅、卧室、玄关、厨房）
  - 解析自然语言任务文本，提取起点/终点位置
  - 支持中文自然语言描述（如"去客厅拿杯子"、"前往卧室取物品"）
  - 返回结构化的 2D 坐标任务数据
"""
import re
from typing import Optional, Dict, Tuple
from dataclasses import dataclass, field


# ═══════════════════════════════════════════════════════════
# 室内场景点位坐标库（2D SLAM 地图，单位：米）
# ═══════════════════════════════════════════════════════════
LOCATION_DB: Dict[str, Dict[str, float]] = {
    "客厅":  {"x": 0.0,  "y": 0.0,  "description": "客厅中心"},
    "卧室":  {"x": 5.0,  "y": 3.0,  "description": "卧室入口"},
    "卧室1": {"x": 5.0,  "y": 3.0,  "description": "主卧室"},
    "玄关":  {"x": 1.5,  "y": -2.0, "description": "入户玄关"},
    "厨房":  {"x": -3.0, "y": 2.5,  "description": "厨房操作台"},
    "走廊":  {"x": 2.0,  "y": 0.0,  "description": "走廊中点"},
    "卫生间":{"x": 6.0,  "y": -1.5, "description": "卫生间"},
    "阳台":  {"x": -2.0, "y": 5.0,  "description": "阳台"},
}

# 别名映射（支持口语化表达）
LOCATION_ALIASES: Dict[str, str] = {
    "living room": "客厅",
    "livingroom": "客厅",
    "bedroom": "卧室",
    "bed room": "卧室",
    "entrance": "玄关",
    "hallway": "玄关",
    "kitchen": "厨房",
    "corridor": "走廊",
    "bathroom": "卫生间",
    "balcony": "阳台",
    "主卧": "卧室",
    "次卧": "卧室1",
    "大厅": "客厅",
    "起居室": "客厅",
    "饭厅": "厨房",
    "厕所": "卫生间",
    "洗手间": "卫生间",
}


@dataclass
class ParsedNLPTask:
    """解析后的 NLP 任务"""
    raw_text: str                           # 原始自然语言文本
    task_name: str                          # 提取的任务名称
    start_location: str                     # 起点位置名称
    target_location: str                    # 终点位置名称
    start_x: float                          # 起点 X 坐标 (m)
    start_y: float                          # 起点 Y 坐标 (m)
    target_x: float                         # 终点 X 坐标 (m)
    target_y: float                         # 终点 Y 坐标 (m)
    target_object: Optional[str] = None     # 目标物品（如"杯子"）
    action: str = "navigate"                # 动作类型


class NLPParser:
    """自然语言任务解析器"""

    # 动作关键词模式
    ACTION_PATTERNS = [
        (re.compile(r"(?:去|前往|走到|移动到|导航到|到)(.+)"), "navigate"),
        (re.compile(r"(?:拿|取|抓取|拾取|捡|搬运)(.+)"), "pickup"),
        (re.compile(r"(?:放|放置|放下|送回)(.+)"), "place"),
        (re.compile(r"(?:回|返回|回到)(.+)"), "return"),
        (re.compile(r"(?:找|寻找|搜索)(.+)"), "search"),
    ]

    # 物品关键词模式（"拿杯子"中的"杯子"）
    OBJECT_PATTERN = re.compile(
        r"(?:拿|取|抓|拾|捡|搬运|放|放置|放下|送回|找|寻找)"
        r"([一-鿿\w]{1,6})"
    )

    # 位置提取模式（从动作目标中分离位置）
    LOCATION_PATTERNS = [
        re.compile(r"(.+)(?:拿|取|抓|拾|捡|搬运|放|放置|放下|找)([一-鿿\w]{1,6})"),
        re.compile(r"(.+?)(?:的|之)([一-鿿\w]{1,6})"),
    ]

    def __init__(self, location_db: Optional[Dict] = None):
        self.locations = location_db or LOCATION_DB

    def resolve_location(self, name: str) -> Optional[Tuple[str, float, float]]:
        """将位置名称解析为 (标准名称, x, y)"""
        name = name.strip()
        # 先查别名
        resolved = LOCATION_ALIASES.get(name.lower(), name)
        # 再查坐标库
        if resolved in self.locations:
            loc = self.locations[resolved]
            return resolved, loc["x"], loc["y"]
        # 模糊匹配
        for loc_name in self.locations:
            if loc_name in name or name in loc_name:
                loc = self.locations[loc_name]
                return loc_name, loc["x"], loc["y"]
        return None

    def extract_locations(self, text: str) -> list:
        """从文本中提取所有已知位置名称"""
        found = []
        # 按名称长度降序匹配，优先匹配长名称
        sorted_names = sorted(self.locations.keys(), key=len, reverse=True)
        remaining = text
        for name in sorted_names:
            if name in remaining:
                found.append(name)
                remaining = remaining.replace(name, "", 1)
        # 也检查别名
        for alias, canonical in sorted(LOCATION_ALIASES.items(), key=lambda x: len(x[0]), reverse=True):
            if alias.lower() in remaining.lower() and canonical in self.locations:
                if canonical not in found:
                    found.append(canonical)
        return found

    def extract_object(self, text: str) -> Optional[str]:
        """提取目标物品"""
        m = self.OBJECT_PATTERN.search(text)
        if m and m.group(1).strip():
            obj = m.group(1).strip()
            # 过滤掉位置词
            if obj not in self.locations and obj not in LOCATION_ALIASES:
                return obj
        return None

    def parse(self, text: str, current_location: str = "客厅") -> ParsedNLPTask:
        """解析自然语言文本，返回结构化任务数据

        Args:
            text: 自然语言任务描述，如"去客厅拿杯子"
            current_location: 机器人当前位置名称，默认"客厅"

        Returns:
            ParsedNLPTask: 解析后的任务数据
        """
        text = text.strip()

        # 1. 提取动作类型
        action = "navigate"
        target_phrase = text
        for pattern, act in self.ACTION_PATTERNS:
            m = pattern.match(text)
            if m:
                action = act
                target_phrase = m.group(1).strip()
                break

        # 2. 提取目标物品
        target_object = self.extract_object(text)

        # 3. 提取位置
        locations = self.extract_locations(text)

        # 4. 确定起止位置
        if len(locations) >= 2:
            start_location = locations[0]
            target_location = locations[1]
        elif len(locations) == 1:
            start_location = current_location
            target_location = locations[0]
        else:
            # 尝试从 target_phrase 中解析位置
            for loc_name in self.locations:
                if loc_name in target_phrase:
                    start_location = current_location
                    target_location = loc_name
                    break
            else:
                # 默认：当前位置 → 客厅
                start_location = current_location
                target_location = "客厅"

        # 5. 解析坐标
        start_info = self.resolve_location(start_location)
        target_info = self.resolve_location(target_location)

        if not start_info:
            # fallback to default
            start_info = ("客厅", 0.0, 0.0)
        if not target_info:
            target_info = ("客厅", 0.0, 0.0)

        _, start_x, start_y = start_info
        _, target_x, target_y = target_info

        # 6. 生成任务名
        if target_object:
            task_name = f"前往{target_location}拿{target_object}"
        elif action == "navigate":
            task_name = f"导航至{target_location}"
        elif action == "return":
            task_name = f"返回{target_location}"
        else:
            task_name = f"{action}: {target_location}"

        return ParsedNLPTask(
            raw_text=text,
            task_name=task_name,
            start_location=start_info[0],
            target_location=target_info[0],
            start_x=start_x,
            start_y=start_y,
            target_x=target_x,
            target_y=target_y,
            target_object=target_object,
            action=action,
        )

    def parse_to_navigation_packet(self, text: str,
                                   current_location: str = "客厅") -> dict:
        """解析并生成可直接下发到机器人模拟端的导航数据包

        Returns:
            dict: 包含 type="nav_task", start_x/y, target_x/y, yaw, task_name
        """
        parsed = self.parse(text, current_location)
        import math
        # 计算初始航向角（从起点指向终点）
        dx = parsed.target_x - parsed.start_x
        dy = parsed.target_y - parsed.start_y
        yaw = math.degrees(math.atan2(dy, dx)) % 360

        return {
            "type": "nav_task",
            "task_name": parsed.task_name,
            "raw_text": parsed.raw_text,
            "start_location": parsed.start_location,
            "target_location": parsed.target_location,
            "start_x": parsed.start_x,
            "start_y": parsed.start_y,
            "target_x": parsed.target_x,
            "target_y": parsed.target_y,
            "initial_yaw": round(yaw, 1),
            "target_object": parsed.target_object,
            "action": parsed.action,
        }


# 全局单例
_nlp_parser: Optional[NLPParser] = None


def get_nlp_parser() -> NLPParser:
    """获取 NLP 解析器单例"""
    global _nlp_parser
    if _nlp_parser is None:
        _nlp_parser = NLPParser()
    return _nlp_parser
