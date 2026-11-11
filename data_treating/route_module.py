import json
import asyncio
from datetime import datetime
from pathlib import Path
from openai import OpenAI
from loguru import logger
# from llama_cpp import Llama

from character_simulation import llm_module

KEY = ""
NAME = ""
BOT_ID = 0
BOT_NICKNAME = ""
SYSTEM_CONTENT = ""

client = None
history = []
memory_compressed_path = None

def route_init():
    global KEY, NAME, BOT_ID, BOT_NICKNAME, SYSTEM_CONTENT, memory_compressed_path, client, history
    KEY = llm_module.get_apikey()
    NAME = llm_module.get_name()
    BOT_ID = llm_module.get_bot_id()
    BOT_NICKNAME = llm_module.get_bot_nickname()    
    SYSTEM_CONTENT = f"""
    你是一个用于辨别情况的路由引擎，被部署在一个QQ群中，同时还会有一个与你连接的聊天AI也被部署在一起，这个聊天AI的账号是{BOT_ID}，昵称是{BOT_NICKNAME}，因为聊天AI可能存在不止一个，所以账号与昵称是这个与你连接的聊天AI仅有的代号。
    你要处理的数据是QQ群中的聊天消息，每条输入的'role'为'user'的消息都是一条QQ聊天消息，你会收到的消息示例如下：
    [用户昵称|用户账号][消息id]:['[@123] 你好，用户123']
    你需要只输出合法的JSON，不能输出对话内容或Markdown代码，也不需要任何解释。你要输出的内容只有一个值为布尔值的键值对：
    "chat_to_bot": true 或 "chat_to_bot": false
    你需要判断一条消息中，用户是否在和与你连接的聊天AI{BOT_NICKNAME}交流，并以此决定"chat_to_bot"的结果，是则为True，否则为False。在QQ群聊中，用户们往往会互相交流，当一条消息没有明确指向{BOT_NICKNAME}的昵称或账号时，你要认为这不是在和{BOT_NICKNAME}交流，其中的所有人称指代（例如你、他、BOT、聊天AI）均不是指{BOT_NICKNAME}，此时默认为False，但同时，你要通过关联上下文判断这条消息是不是在和{BOT_NICKNAME}交流（例如{BOT_NICKNAME}发送了一条消息后，有用户根据{BOT_NICKNAME}的消息做了明确的回复），若有类似意图，则为True。若一条出现了@某人的操作，检查@的账号是否为{BOT_NICKNAME}的账号，若不是则可断定不是在和{BOT_NICKNAME}交流，则为False，若是则可断定是在和{BOT_NICKNAME}交流，则为True，若@了多个账号，只要其中包含有{BOT_NICKNAME}的账号，则断定为在和{BOT_NICKNAME}交流，则为True。若一条消息中出现了"回复了你的消息('具体内容')"字段，则是该消息回复了{BOT_NICKNAME}所发的某条消息，若该条消息同时@了{BOT_NICKNAME}或没有@任何人，则可断定为是在回复{BOT_NICKNAME}，则为True，若该条消息只@了{BOT_NICKNAME}以外的账号，则认为是引用式回复，而非在和{BOT_NICKNAME}交流，则为False。
    此外，{BOT_NICKNAME}发送的消息也会作为"role"为"user"的消息传入历史记录，这些消息没有消息id，且会被标明为"[{BOT_NICKNAME}的消息]"，你要将其作为关联上下文的一环。
    示例：
    [蓝花矢车菊|3218413440][mesid735977567]:['你为什么不理我'][2026-11-07 21:05:03]
    返回"chat_to_bot": false

    [蓝花矢车菊|3218413440][mesid735977567]:['回复我'][2026-11-07 21:05:03]
    返回"chat_to_bot": false

    [蓝花矢车菊|3218413440][mesid735977567]:['聊天AI，你好'][2026-11-07 21:05:03]
    返回"chat_to_bot": false

    [蓝花矢车菊|3218413440][mesid735977567]:['聊天AI，你好'][2026-11-07 21:05:03]
    返回"chat_to_bot": false

    [蓝花矢车菊|3218413440][mesid735977567]:['{BOT_ID}在不在'][2026-11-07 21:05:03]
    返回"chat_to_bot": true

    [蓝花矢车菊|3218413440][mesid735977567]:['[回复了你的消息('是的')][@2398367016] 真的吗'][2026-11-07 21:05:03]
    返回"chat_to_bot": false

    [蓝花矢车菊|3218413440][mesid735977567]:['[@{BOT_ID}] 你好'][2026-11-07 21:05:03]
    返回"chat_to_bot": true

    [蓝花矢车菊|3218413440][mesid735977567]:['[回复了你的消息('是的')]真的吗'][2026-11-07 21:05:03]
    返回"chat_to_bot": true

    [蓝花矢车菊|3218413440][mesid735977567]:['彩虹有几种颜色？'][2026-11-07 21:05:03]
    [{BOT_NICKNAME}|{BOT_ID}][{BOT_NICKNAME}的消息]:['七种'][2026-11-07 21:05:05]
    [蓝花矢车菊|3218413440][mesid214377567]:['你怎么不理我'][2026-11-07 21:05:12]
    返回"chat_to_bot": false

    [蓝花矢车菊|3218413440][mesid735977567]:['彩虹有几种颜色？'][2026-11-07 21:05:03]
    [{BOT_NICKNAME}|{BOT_ID}][{BOT_NICKNAME}的消息]:['七种'][2026-11-07 21:05:05]
    [蓝花矢车菊|3218413440][mesid214377567]:['回答我的问题'][2026-11-07 21:05:12]
    返回"chat_to_bot": false

    [蓝花矢车菊|3218413440][mesid735977567]:['彩虹有几种颜色？'][2026-11-07 21:05:03]
    [{BOT_NICKNAME}|{BOT_ID}][{BOT_NICKNAME}的消息]:['七种'][2026-11-07 21:05:05]
    [蓝花矢车菊|3218413440][mesid214377567]:['你在吗'][2026-11-07 21:05:12]
    返回"chat_to_bot": false

    [蓝花矢车菊|3218413440][mesid735977567]:['彩虹有几种颜色？'][2026-11-07 21:05:03]
    [{BOT_NICKNAME}|{BOT_ID}][{BOT_NICKNAME}的消息]:['七种'][2026-11-07 21:05:05]
    [蓝花矢车菊|3218413440][mesid214377567]:['说话'][2026-11-07 21:05:12]
    返回"chat_to_bot": false

    [蓝花矢车菊|3218413440][mesid735977567]:['彩虹有几种颜色？'][2026-11-07 21:05:03]
    [{BOT_NICKNAME}|{BOT_ID}][{BOT_NICKNAME}的消息]:['七种'][2026-11-07 21:05:05]
    [蓝花矢车菊|3218413440][mesid214377567]:['一加十等于几？'][2026-11-07 21:05:16]
    返回"chat_to_bot": false

    [蓝花矢车菊|3218413440][mesid735977567]:['彩虹有几种颜色？'][2026-11-07 21:05:03]
    [{BOT_NICKNAME}|{BOT_ID}][{BOT_NICKNAME}的消息]:['七种'][2026-11-07 21:05:05]
    [蓝花矢车菊|3218413440][mesid214377567]:['其中包括绿色吗？'][2026-11-07 21:05:09]
    返回"chat_to_bot": true
    """

    memory_compressed_path = Path(f"character_simulation/memories/{NAME}/{NAME}_compressed.txt")

    client = OpenAI(api_key=KEY, base_url="https://api.deepseek.com")
    if not memory_compressed_path.exists():
        history = [{
            "role": "system",
            "content": SYSTEM_CONTENT
        }]
    else:
        with open(memory_compressed_path, "r", encoding="utf-8") as m:
            memory_compressed = m.read()
        history = [{
            "role": "system",
            "content": SYSTEM_CONTENT + "\n\n---------\n以下为过往记忆，你要着重参考其中的用户信息：\n" + memory_compressed
        }]

def communicatee_route(user_content):
    global history
    history_cache = list(history)
    history_cache.append({
        "role": "user",
        "content": user_content
    })
    try:
        answer = client.chat.completions.create(model="deepseek-flash", messages=history_cache, response_format={"type": "json_object"}, timeout=30)
        history_cache.append({
            "role": "assistant",
            "content": answer.choices[0].message.content.replace("True", "true").replace("False", "false")
        })
        history = list(history_cache)
    except Exception as e:
        logger.error(f"交流对象路由请求失败{e}")
        return False
    try:
        route_result = list(json.loads(answer.choices[0].message.content.replace("True", "true").replace("False", "false")).values())[0]
        return route_result
    except Exception as e:
        logger.error(f"交流对象路由结果无效{e}")
        return False

def sync_history(bot_content):
    history.append({
        "role": "user",
        "content": f"[{BOT_NICKNAME}|{BOT_ID}][{BOT_NICKNAME}的消息]:['{bot_content}'][{datetime.now().replace(microsecond=0)}]"
    })
    history.append({
        "role": "assistant",
        "content": "pass"
    })
    if len(history) > 100:
        del history[1:11]

# llm = Llama(
#     model_path="data_treating/models/AlphaRoute-0.8B-v1.0-Q4_K_M.gguf",
#     n_ctx=4096,
#     n_gpu_layers=0,
#     verbose=False
# )
