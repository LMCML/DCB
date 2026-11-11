import json
from pathlib import Path
from datetime import datetime
import requests
from openai import OpenAI
from loguru import logger

from character_simulation import llm_module

KEY = ""
NAME = ""
ADMST = 0
BOT_NICKNAME = ""
TARGET_ID = 0
SYSTEM_CONTENT = ""

client = None
processing = []
memory_path = None
memory_compressed_path = None

def memory_init():
    global KEY, NAME, ADMST, BOT_NICKNAME, TARGET_ID, SYSTEM_CONTENT, memory_path, memory_compressed_path, client, processing
    KEY = llm_module.get_apikey()
    NAME = llm_module.get_name()
    ADMST = llm_module.get_admst()
    BOT_NICKNAME = llm_module.get_bot_nickname()
    TARGET_ID = llm_module.get_target_id()
    SYSTEM_CONTENT = f"""
    你要进行对历史记录信息的记忆提炼摘要工作，你需要只输出对历史记录信息提炼摘要后的内容。对于历史记录信息中的内容，你要仔细辨别，对不确定的事情、疑似不完整的信息不要猜测，可以调用联网搜索工具，可以用“疑似”描述，或直接剔除这部分消息，主要对可做出准确结论的消息进行处理。
    你将会收到一组来自QQ群的聊天消息记录，该组消息记录来源的群聊的群聊号为{TARGET_ID}，该群聊中有一个名为{BOT_NICKNAME}的聊天AI，该聊天AI的管理员的账号是{ADMST}（仅是聊天AI{BOT_NICKNAME}的管理员，不一定是群聊管理员），'role'为'assistant'的消息由{BOT_NICKNAME}发送，'role'为'user'的消息由其他用户发送。
    你需要提炼的内容包括：
    用户（以用户账号为准）的画像，包括用户的信息（简称、别名、职业、学历、住址、文化等），用户的喜好厌恶；
    群聊（以群聊号为准）中发生过的主要事件，及其涉及的实体（人、物、地点、时间等）；
    过滤掉无意义的消息。
    你将会收到的聊天消息记录中，'user'消息的'content'格式示例如下：
    ["type": 'text', 'text': '[用户昵称1|用户帐号1][消息id1]:['用户消息1[图片]'][消息发送时间]', "type": 'text', 'text': '这里曾是一张图片', 'type": 'text', 'text': '[用户昵称2|用户帐号2][消息id2]:['用户消息2'][消息发送时间]', 'type': 'text', 'text': '该组消息时间']
    这是一个包含三条消息和一个时间记录的列表，其中的第一条消息为用户1发送的一条带有图片的消息，第二条消息为用户1发送的图片记录，第三条消息为用户2发送的一条纯文本消息，最后为该组消息的时间记录。
    你将会收到的聊天消息记录中，若'system'消息的'content'中包含以下示例内容：
    “
    ---------
    以下为过往记忆：
    过往记忆
    ”
    需将其中的过往记忆内容作为基础，在此之上将新提炼的摘要加入，将已有的有关用户画像的信息合并，信息最少的用户信息可以剔除，而群聊事件内容中，如果有旧事件的新进展，则将其追加到旧事件后，最久远（以最新后续为准）、最无聊、最无关紧要的部分内容可以剔除，以避免摘要总长度过长。
    对于琐事，你要减少描述的长度，或直接跳过，可以概述但不能影响原义。
    你将会收到的聊天消息记录中，'assistant'消息的'content'是{BOT_NICKNAME}实际表述过的话语，'reasoning_content'只是思考过程。
    你需要输出的内容输出格式示例如下：
    用户信息
    -用户账号1|[称呼1、称呼2、称呼3]|用户简述
    -用户账号2|[称呼1、称呼2、称呼3]|用户简述
    群聊事件
    -群聊号1
    --事件时间（详细至分钟）|事件1--事件时间|事件1后续进展
    --事件时间|事件2
    -群聊号2
    --事件时间|事件1
    整篇摘要的期望长度为2500字，极限长度为5000字。
    """

    memory_path = Path(f"character_simulation/memories/{NAME}/{NAME}.json")
    memory_compressed_path = Path(f"character_simulation/memories/{NAME}/{NAME}_compressed.txt")

    client = OpenAI(api_key=KEY, base_url="https://api.deepseek.com")
    processing = [{
        "role": "system",
        "content": SYSTEM_CONTENT
    }]

def web_search(term):
    try:
        logger.info("尝试创建搜索请求")
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
        logger.error(f"搜索请求错误{e}")
        return "搜索请求错误"
    if ans.status_code == 200:
        result = ans.json()["results"]
        if not result:
            return "搜索无结果"
        return "\n\n".join(f"[{r.get('title')}]{r.get('summary')}" for r in result)
    else:
        logger.error("搜索失败")
        return "搜索失败"

tools = [{
    "type": "function",
    "function": {
        "name": "web_search",
        "description": "当自有信息无法准确处理信息时，进行联网搜索信息。",
        "parameters": {
            "type": "object",
            "properites": {
                "term": {
                    "type": "string",
                    "description": "将有疑问的部分组成搜索词。"
                }
            },
            "required": ["term"]
        }
    }
}]

def memory_compress():
    while True:
        logger.info("开始记忆压缩")
        try:
            with open(memory_path, "r", encoding="utf-8") as m:
                history_memory = json.load(m)
            processing.append({
                "role": "user",
                "content": str(history_memory)
            })
            anwser = client.chat.completions.create(model="deepseek-v4-pro", messages=processing, tools=tools, timeout=600)
            if not anwser.choices[0].message.tool_calls:
                try:
                    with open(memory_compressed_path, "w", encoding="utf-8") as m:
                        m.write(anwser.choices[0].message.content)
                    logger.info("压缩后记忆已写入")
                    try:
                        memory_archived_path = Path(f"character_simulation/memories/{NAME}/{NAME}_archived/{NAME}_archived_{datetime.now().strftime("%Y-%m-%d_%H.%M.%S")}.json")
                        memory_archived_path.parent.mkdir(parents=True, exist_ok=True)
                        memory_path.replace(memory_archived_path)
                        logger.info("记忆归档")
                        return
                    except Exception as e:
                        logger.error(f"记忆归档失败{e}")
                        return
                except Exception as e:
                    logger.error(f"压缩后记忆写入失败{e}")
                    return
            else:
                tool_call = anwser.choices[0].message.model_dump()
                processing.append(tool_call)
                for t in tool_call.get("tool_calls"):
                    argument = json.loads(t.get("function", {}).get("arguments")).get("term")
                    tool_result = web_search(argument)
                    processing.append({
                        "role": "tool",
                        "tool_call_id": t.get("id"),
                        "content": tool_result
                    })
        except Exception as e:
            logger.error(f"压缩记忆失败{e}")
            return
