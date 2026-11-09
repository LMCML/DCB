import json
import copy
import asyncio
import requests
from datetime import datetime
from pathlib import Path
from openai import AsyncOpenAI

from data_treating import route_module

KEY = ""
NAME = ""
ADMST = 0
BOT_ID = 0
BOT_NICKNAME = ""
TARGET_TYPE = ""
TARGET_ID = 0
SYSTEM_CONTENT = ""

client = None
def client_init():
    global client
    client = AsyncOpenAI(api_key=KEY, base_url="https://api.deepseek.com")

history = []
history_for_local = []
save_lock = asyncio.Lock()

def get_apikey():
    return KEY
def get_name():
    return NAME
def get_admst():
    return ADMST
def get_bot_id():
    return BOT_ID
def get_bot_nickname():
    return BOT_NICKNAME
def get_target_type():
    return TARGET_TYPE
def get_target_id():
    return TARGET_ID
def get_system_content():
    return SYSTEM_CONTENT
def set_apikey(key):
    global KEY
    KEY = key
def set_name(name):
    global NAME
    NAME = name
def set_admst(admst):
    global ADMST
    ADMST = admst
def set_bot_id(bot_id):
    global BOT_ID
    BOT_ID = bot_id
def set_bot_nickname(bot_nickname):
    global BOT_NICKNAME
    BOT_NICKNAME = bot_nickname
def set_target_type(target_type):
    global TARGET_TYPE
    TARGET_TYPE = target_type
def set_target_id(target_id):
    global TARGET_ID
    TARGET_ID = target_id
def set_system_content(system_content):
    global KEY, NAME, ADMST, BOT_ID, BOT_NICKNAME, TARGET_TYPE, TARGET_ID, SYSTEM_CONTENT
    SYSTEM_CONTENT = system_content.format(KEY=KEY, NAME=NAME, ADMST=ADMST, BOT_ID=BOT_ID, BOT_NICKNAME=BOT_NICKNAME, TARGET_TYPE=TARGET_TYPE, TARGET_ID=TARGET_ID)

def load_memory():
    global history, history_for_local
    memory_path = Path(f"character_simulation/memories/{NAME}.json")
    memory_path.parent.mkdir(parents=True, exist_ok=True)
    if memory_path.exists():
        try:
            with open(memory_path, "r", encoding="utf-8") as m:
                history = json.load(m)
                history_for_local = list(history)
                print("记忆文件读取成功")
                return "load_successful"
        except Exception as e:
            print(f"读取记忆文件失败{e}")
            return "load_failed"
    else:
        history = [{
            "role": "system",
            "content": SYSTEM_CONTENT
        }]
        history_for_local = list(history)
        try:
            with open(memory_path, "w", encoding="utf-8") as m:
                json.dump(history_for_local, m, ensure_ascii=False, indent=2)
                print("记忆文件创建成功")
                return "create_successful"
        except Exception as e:
            print(f"创建记忆文件失败{e}")
            return "create_failed"

def save_memory_function(history):
    memory_path = Path(f"character_simulation/memories/{NAME}.json")
    memory_path.parent.mkdir(parents=True, exist_ok=True)
    memory_backup_path = Path(f"character_simulation/memories/{NAME}_backup.json")
    if memory_path.exists():
        try:
            memory_path.copy(memory_backup_path)
        except Exception as e:
            print(f"复制备份失败{e}")
    try:
        content_saved = json.dumps(history, ensure_ascii=False, indent=2)
    except Exception as e:
        print(f"记忆内容序列化失败{e}")
        return
    try:
        memory_tmp = memory_path.with_name(f"{NAME}.tmp")
        memory_tmp.write_text(content_saved, encoding="utf-8")
        memory_tmp.replace(memory_path)
        print("已存档")
    except Exception as e:
        print(f"记忆内容保存失败{e}")
async def save_memory():
    history_cache = copy.deepcopy(history_for_local)
    async with save_lock:
        return await asyncio.to_thread(save_memory_function, history_cache)

def web_search_function(term):
    try:
        print("尝试创建搜索请求")
        ans = requests.post(
            "https://api.deepseek.com/v1/tools/search",
            headers={"Authorization": f"Bearer {KEY}"},
            json={
                "query": term,
                "count": 25
            },
            timeout=30
        )
    except Exception as e:
        print(f"搜索请求错误{e}")
        return "搜索请求错误"
    if ans.status_code == 200:
        print("完成搜索")
        result = ans.json()["results"]
        if not result:
            return "搜索无结果"
        return "\n\n".join(f"[{r.get('title')}]{r.get('summary')}" for r in result)
    else:
        print("搜索失败")
        return "搜索失败"
async def web_search(term):
    return await asyncio.to_thread(web_search_function, term)

def expression_send(ex_type):
    print("调用表情工具")
    match ex_type:
        case "angry":
            return {
                "name": "调用工具发送了表情包angry|||",
                "path": "character_simulation/expression_package/angry"
            }
        case "cry":
            return {
                "name": "调用工具发送了表情包cry|||",
                "path": "character_simulation/expression_package/cry"
            }
        case "cute":
            return {
                "name": "调用工具发送了表情包cute|||",
                "path": "character_simulation/expression_package/cute"
            }
        case "dissatisfied":
            return {
                "name": "调用工具发送了表情包dissatisfied|||",
                "path": "character_simulation/expression_package/dissatisfied"
            }
        case "laugh":
            return {
                "name": "调用工具发送了表情包laugh|||",
                "path": "character_simulation/expression_package/laugh"
            }
        case "speechless":
            return {
                "name": "调用工具发送了表情包speechless|||",
                "path": "character_simulation/expression_package/speechless"
            }
        case "none":
            print("取消表情工具调用")
            return "pass"
        case others:
            print(f"未知参数{others}")
            return "未知参数"

tools = [{
    "type": "function",
    "function": {
        "name": "web_search",
        "description": "当自有信息无法准确回答问题时，进行联网搜索信息。",
        "parameters": {
            "type": "object",
            "properties":{
                "term": {
                    "type": "string",
                    "description": "提取消息中的关键组成搜索词。"
                }
            },
            "required": ["term"]
        }
    }
},
{
    "type":"function",
    "function":{
        "name": "expression_send",
        "description": "当你想要以文字以外的方式表达情感时，或是遇到文字难以言说的情况，可以发送图像表情。图形表情可以连续多次发送以表达情绪的强烈。",
        "parameters": {
            "type": "object",
            "properties": {
                "ex_type": {
                    "type": "string",
                    "description": "根据情况选择表情类型。若发现情况不适合发送表情，或发现表情发送过于频繁，则返回'none'。",
                    "enum": ["angry", "cry", "cute", "dissatisfied", "laugh", "speechless", "none"]
                }
            },
            "required": ["ex_type"]
        }
    }
}]

async def completions_created(user_content, user_content_without_image):
    global history, history_for_local
    image_list = []
    history_cache = copy.deepcopy(history)
    history_cache.append({
        "role": "user",
        "content": user_content
    })
    try:
        answer = await asyncio.wait_for(client.chat.completions.create(model="deepseek-flash", messages=history_cache, tools=tools, timeout=30), timeout=60)
        if not answer.choices[0].message.tool_calls:
            history_cache.append({
                "role": "assistant",
                "content": answer.choices[0].message.content
            })
            history = copy.deepcopy(history_cache)
            history_for_local.append({
                "role": "user",
                "content": user_content_without_image
            })
            history_for_local.append({
                "role": "assistant",
                "content": answer.choices[0].message.content
            })
            route_module.sync_history(answer.choices[0].message.content)
            return {
                "text": answer.choices[0].message.content,
                "image": image_list
            }
        else:
            tool_call = answer.choices[0].message.model_dump()
            history_cache.append(tool_call)
            for t in tool_call.get("tool_calls"):
                if t.get("function", {}).get("name") == "web_search":
                    argument = json.loads(t.get("function", {}).get("arguments")).get("term")
                    tool_result = await web_search(argument)
                    history_cache.append({
                        "role": "tool",
                        "tool_call_id": t.get("id"),
                        "content": tool_result
                    })
                elif t.get("function", {}).get("name") == "expression_send":
                    argument = json.loads(t.get("function", {}).get("arguments")).get("ex_type")
                    tool_result = expression_send(argument)
                    if isinstance(tool_result, str):
                        if tool_result == "pass":
                            history_cache.append({
                            "role": "tool",
                            "tool_call_id": t.get("id"),
                            "content": "表情发送取消"
                        })
                        else:
                            history_cache.append({
                            "role": "tool",
                            "tool_call_id": t.get("id"),
                            "content": "参数无效"
                        })
                    else:
                        history_cache.append({
                        "role": "tool",
                        "tool_call_id": t.get("id"),
                        "content": f"已经发送了一个表情包{argument}"
                        })
                        image_list.append(tool_result)
                else:
                    history_cache.append({
                        "role": "tool",
                        "tool_call_id": t.get("id"),
                        "content": "unknown_tool"
                    })
                    print("出现未知方法调用")
        
        answer = await asyncio.wait_for(client.chat.completions.create(model="deepseek-flash", messages=history_cache, timeout=30), timeout=30)
        history_cache.append({
            "role": "assistant",
            "content": answer.choices[0].message.content
        })
        history = copy.deepcopy(history_cache)
        history_for_local.append({
            "role": "user",
            "content": user_content_without_image
        })
        history_for_local.append({
            "role": "assistant",
            "content": "".join(i.get("name", "") for i in image_list) + answer.choices[0].message.content
        })
        route_module.sync_history("".join(i.get("name", "") for i in image_list) + answer.choices[0].message.content)
        return {
            "text": answer.choices[0].message.content,
            "image": image_list
        }
    except Exception as e:
        print(f"创建对话失败{e}")
        return "chat_failed"