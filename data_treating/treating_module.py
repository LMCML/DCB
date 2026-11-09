import copy
import asyncio
from collections import deque
import base64
from datetime import datetime
import requests

from data_treating import face_dictionary, route_module
from character_simulation import llm_module

BOT_ID = 0
TARGET_TYPE = ""
TARGET_ID = 0
STANDBY_COMMAND = [",", "，"]

def treating_init():
    global BOT_ID, TARGET_TYPE, TARGET_ID
    BOT_ID = llm_module.get_bot_id()
    TARGET_TYPE = llm_module.get_target_type()
    TARGET_ID = llm_module.get_target_id()

retry_times = 0
message_list = deque(maxlen=50)
message_list_cache = deque(maxlen=50)
sent_history = {}
treat_lock = asyncio.Lock()
request_lock = asyncio.Lock()

def treating_new_mes_function(mes):
    reply_soon = False
    if TARGET_TYPE == "group" and mes.get("group_id") == TARGET_ID:
        if mes.get("post_type") == "message":
            user_id = mes.get("sender", {}).get("user_id")
            nickname = mes.get("sender", {}).get("nickname")
            mesid = mes.get("message_id")

            user_message = ""
            for m in mes.get("message"):
                if m.get("type") == "text":
                    user_message = f"{user_message}{m.get('data', {}).get('text')}"
                elif m.get("type") == "image":
                    if m.get("data", {}).get("summary") == "[动画表情]":
                        user_message = f"{user_message}[动画表情]"
                    else:
                        user_message = f"{user_message}[图片]"
                elif m.get("type") == "face":
                    user_message = f"{user_message}[QQ表情{face_dictionary.get_face(m.get('data', {}).get('id'))}]"
                elif m.get("type") == "at":
                    user_message = f"{user_message}[@{m.get('data', {}).get('qq')}]"
                    if str(m.get('data', {}).get('qq')) == str(BOT_ID):
                        reply_soon = True
                elif m.get("type") == "reply":
                    if sent_history.get(str(m.get('data', {}).get('id'))):
                        user_message = f"[回复了你的消息('{sent_history.get(m.get('data', {}).get('id'))}')]{user_message}"
                        # reply_soon = True
                    else:
                        user_message = f"[回复mesid{m.get('data', {}).get('id')}]{user_message}"
            if not user_message:
                return
            message_combined = f"[{nickname}|{user_id}][mesid{mesid}]:['{user_message}'][{datetime.now().replace(microsecond=0)}]"
            route_result = route_module.communicatee_route(message_combined)
            if route_result:
                a_total_message = {
                    "type": "text",
                    "text": f"[与你交流]{message_combined}"
                }
                reply_soon = True
            else:
                a_total_message = {
                    "type": "text",
                    "text": message_combined
                }
            print(f"接收消息{a_total_message.get("text")}")
            message_list.append(a_total_message)

            for m in mes.get("message"):
                if m.get("type") == "image":
                    if m.get("data", {}).get("file"):
                        img_format = m.get("data", {}).get("file").split(".")[-1]
                    else:
                        img_format = "jpeg"
                    if img_format == "jpg":
                        img_format = "jpeg"
                    if int(m.get("data", {}).get("file_size")) > 24000000:
                        try:
                            with open("data_treating/placeholder/Imagetoolargedownloadrejected", "rb") as i:
                                img_b64 = base64.b64encode(i.read()).decode("utf-8")
                                a_total_message = {
                                    "type": "image_url",
                                    "image_url": {"url": f"data:image/jpeg;base64,{img_b64}"}
                                }
                            print("接收图片过大报错占位符")
                            message_list.append(a_total_message)
                            continue
                        except Exception as e:
                            print(f"接收图片过大报错占位符失败{e}")
                            continue
                    request_times = 0
                    while request_times <= 5:
                        try:
                            img = requests.get(m.get("data", {}).get("url"), timeout=10)
                        except Exception as e:
                            print(f"图片下载失败{e}")
                            request_times += 1
                            continue
                        if img.status_code == 200:
                            break
                        else:
                            request_times += 1
                    else:
                        try:
                            with open("data_treating/placeholder/imagedownloadfailed", "rb") as i:
                                img_b64 = base64.b64encode(i.read()).decode("utf-8")
                                a_total_message = {
                                    "type": "image_url",
                                    "image_url": {"url": f"data:image/jpeg;base64,{img_b64}"}
                                }
                            print("接收图片下载失败报错占位符")
                            message_list.append(a_total_message)
                            continue
                        except Exception as e:
                            print(f"接收图片下载失败报错占位符失败{e}")
                            continue
                    img_b64 = base64.b64encode(img.content).decode("utf-8")
                    a_total_message = {
                        "type": "image_url",
                        "image_url": {"url": f"data:image/{img_format};base64,{img_b64}"}
                    }
                    print(f"接收图片{img_format}")
                    message_list.append(a_total_message)
    return reply_soon
async def treating_new_mes(mes):
    async with treat_lock:
        result = await asyncio.to_thread(treating_new_mes_function, mes)
        return result
    
def pack_messages(m_list):
    message_packed = list(m_list)
    message_packed.append({
        "type": "text",
        "text": f"当前时间{datetime.now().replace(microsecond=0)}"
    })
    # print(message_packed)
    return message_packed

def local_image_to_base64_function(img_path):
    with open(img_path, "rb") as i:
        img_b64 = base64.b64encode(i.read()).decode("utf-8")
        return img_b64
async def local_image_to_base64(img_path):
    return await asyncio.to_thread(local_image_to_base64_function, img_path)

async def create_request_to_llm():
    global message_list_cache, retry_times
    async with request_lock:
        if message_list_cache:
            user_message = pack_messages(message_list_cache)
        else:
            if not message_list:
                print("无可对话消息")
                return
            message_list_cache = copy.deepcopy(message_list)
            user_message = pack_messages(message_list)
            message_list.clear()
            if not user_message:
                return

        print("对话请求创建")
        # print(user_message)
        user_message_without_image = copy.deepcopy(user_message)
        for i in user_message_without_image:
            if i.get("type") == "image_url":
                i["type"] = "text"
                del i["image_url"]
                i["text"] = "这里曾是一张图片"
        assistant_message = await llm_module.completions_created(user_message, user_message_without_image)

        if isinstance(assistant_message, str):
            if assistant_message == "chat_failed":
                if retry_times <= 5:
                    retry_times += 1
                    return "chat_failed"
                else:
                    print("对话失败次数过多，关闭连接")
                    return "close_connection"
        else:
            # print(assistant_message)  
            message_list_cache.clear()
            retry_times = 0
            if assistant_message.get("text").strip() in STANDBY_COMMAND:
                assistant_message["text"] = ""
                print("对话待机指令")
            assistant_message_text = assistant_message.get("text").replace("\n\n", "|||").split("|||")
            assistant_message_image = []
            for i in assistant_message.get("image"):
                img_b64 = await local_image_to_base64(i.get("path"))
                assistant_message_image.append([{
                    "type": "image",
                    "data": {"file": f"base64://{img_b64}"},
                    "name": i.get("name")
                }])
            result = {
                "params_message": [*assistant_message_image, *assistant_message_text],
                "params_id": TARGET_ID,
                "action_type": TARGET_TYPE
            }
            return result

def update_sent_history(mesid, mes):
    if len(sent_history) > 5000:
        for k in list(sent_history)[:500]:
            del sent_history[k]
    sent_history[str(mesid)] = mes