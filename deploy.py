import os
import sys
from pathlib import Path
import asyncio
import random
from urllib.parse import urlencode
import uuid
import json
from json import JSONDecodeError
import tkinter
from dotenv import set_key, load_dotenv
import websockets
from websockets.exceptions import ConnectionClosed
from loguru import logger

from data_treating import treating_module, route_module
from character_simulation import llm_module, memory_module

os.chdir(Path(__file__).resolve().parent)

NAME = ""
ADMST = 0
WS_URL = ""
ACCESS_TOKEN = ""

send_delay = (10, 12)
send_interval = (3, 5)
chat_interval = (300, 600)
chat_wait = asyncio.Event()
auto_save = asyncio.Event()

task_cache = set()
pending_future = {}
send_lock = asyncio.Lock()

log_path = None

def deploy_init(url, token):
    global NAME, ADMST, WS_URL, ACCESS_TOKEN, log_path
    NAME = llm_module.get_name()
    ADMST = llm_module.get_admst()
    WS_URL = url
    ACCESS_TOKEN = token
    log_path = Path(f"logs/{NAME}console.log")
    log_path.parent.mkdir(parents=True, exist_ok=True)
    logger.remove()
    logger.level("INFO", color="<green>")
    logger.level("ERROR", color="<red>")
    logger.add(sys.stderr, format="{time:YYYY-MM-DD HH:mm:ss}|[<level>{level}</level>]{message}")
    logger.add(log_path, format="{time:YYYY-MM-DD HH:mm:ss}|[<level>{level}</level>]{message}", rotation="1 MB")

def clear_future():
    for e in list(pending_future.keys()):
        future = pending_future.pop(e, None)
        if not future.done():
            future.set_exception(ConnectionError("连接关闭"))

async def send_message(ws, action, params):
    echo = uuid.uuid4().hex
    future = asyncio.get_running_loop().create_future()
    pending_future[echo] = future
    async with send_lock:
        logger.info("进行消息发送")
        await asyncio.sleep(random.randint(*send_interval))
        try:
            await ws.send(json.dumps({
                "action": action,
                "params": params,
                "echo": echo
            }))
        except Exception as e:
            logger.error(f"发送失败{e}")
            pending_future.pop(echo, None)
            return "send_failed"
    try:
        echo_result = await asyncio.wait_for(future, timeout=10)
        if isinstance(params.get("message"), str):
            treating_module.update_sent_history(echo_result.get("data", {}).get("message_id"), params.get("message"))
        else:
            treating_module.update_sent_history(echo_result.get("data", {}).get("message_id"), params.get("message")[0].get("name"))
        return "send_successful"
    except Exception as e:
        logger.error(f"待办异常{e}")
        return "send_failed"
    finally:
        pending_future.pop(echo, None)

async def auto_saving():
    while True:
        try:
            await asyncio.wait_for(auto_save.wait(), timeout=600)
        except Exception:
            logger.info("自动保存")
        auto_save.clear()
        task_save = asyncio.create_task(llm_module.save_memory())
        task_cache.add(task_save)
        task_save.add_done_callback(task_cache.discard)

async def receive_module(ws):
    while True:
        try:
            new_mes = await asyncio.wait_for(ws.recv(), timeout=60)
        except asyncio.TimeoutError as e:
            logger.error(f"消息接收超时，主动断连{e}")
            clear_future()
            return("time_out")
        except Exception as e:
            logger.error(f"消息接收异常{e}")
            clear_future()
            return("connect_failed")

        try:
            mes_content = json.loads(new_mes)
        except JSONDecodeError as e:
            logger.error(f"非法JSON{e}")
            continue
        # print(mes_content)

        if mes_content.get("echo"):
            # print(mes_content)
            echo = mes_content.get("echo")
            future = pending_future.pop(echo, None)
            if future and not future.done():
                future.set_result(mes_content)
            continue
            
        if mes_content.get("sender", {}).get("user_id") == ADMST:
            if mes_content.get("raw_message") == "/自杀" or mes_content.get("raw_message") == "`zs":
                logger.info("收到关闭指令，主动断连")
                clear_future()
                return ("kill_process")
            if mes_content.get("raw_message") == "/存档" or mes_content.get("raw_message") == "`cd":
                logger.info("收到存档指令，进行存档")
                task_save = asyncio.create_task(llm_module.save_memory())
                task_cache.add(task_save)
                task_save.add_done_callback(task_cache.discard)
                continue
            if mes_content.get("raw_message") == "/压缩" or mes_content.get("raw_message") == "`ys":
                logger.info("收到压缩指令，记忆压缩")
                clear_future()
                return ("compress_memory")
            if mes_content.get("raw_message") == "/回复" or mes_content.get("raw_message") == "`hf":
                logger.info("收到回复指令，立即回复")
                start_reply_soon(True)
                continue
        
        task_treat = asyncio.create_task(treating_module.treating_new_mes(mes_content))
        task_treat.add_done_callback(start_reply_soon)
def start_reply_soon(command):
    if isinstance(command, bool) and command == True:
        chat_wait.set()
    elif command.result():
        chat_wait.set()

async def send_module(ws):
    request_retry = False
    while True:
        if request_retry == False:
            try:
                await asyncio.wait_for(chat_wait.wait(), timeout=random.randint(*chat_interval))
            except Exception:
                pass
            chat_wait.clear()
        else:
            logger.info("重试创建对话请求")
            request_retry = False
        await asyncio.sleep(random.randint(*send_delay))
        logger.info("尝试创建对话请求")
        message_to_be_send = await treating_module.create_request_to_llm()

        if not message_to_be_send:
            continue
        if isinstance(message_to_be_send, str):
            if message_to_be_send == "close_connection":
                return "kill_process"
            elif message_to_be_send == "chat_failed":
                request_retry = True
                continue
        else:
            if message_to_be_send.get("action_type") == "group":
                action = "send_group_msg"
                for m in message_to_be_send.get("params_message"):
                    if not m:
                        continue
                    retry_times = 0
                    params = {
                        "group_id": message_to_be_send.get("params_id"),
                        "message": m
                    }
                    result = await send_message(ws, action, params)
                    while result != "send_successful" and retry_times <= 5:
                        retry_times += 1
                        result = await send_message(ws, action, params)
                    if retry_times > 5:
                        logger.error("发送失败次数过多，关闭连接")
                        return "kill_process"

async def connect_to_qq():
    try:
        url = WS_URL
        if ACCESS_TOKEN:
            url = f"{WS_URL}/?{urlencode({'access_token': ACCESS_TOKEN})}"
        async with websockets.connect(url, ping_interval=60, ping_timeout=30) as ws:
            logger.info(f"连接至{WS_URL}")
            task_receive_module = asyncio.create_task(receive_module(ws))
            task_send_module = asyncio.create_task(send_module(ws))
            task_auto_saving = asyncio.create_task(auto_saving())
            done_task, pending_task = await asyncio.wait([task_receive_module, task_send_module, task_auto_saving], return_when=asyncio.FIRST_COMPLETED)
            for t in pending_task:
                t.cancel()
            for t in done_task:
                if t.exception():
                    logger.error(f"协程抛错{t}")
                    clear_future()
                    return "kill_process"
                else:
                    clear_future()
                    return t.result()
    except ConnectionClosed as e:
        logger.error(f"心跳超时{e}")
        clear_future()
        return("time_out")
    except Exception as e:
        logger.error(f"连接失败{e}")
        return("connect_failed") 

config_result = False
def open_config_gui():
    global config_result
    default_system_content = """
    你是一个聊天AI，被部署在一个QQ群聊中，你的账号为{BOT_ID}，昵称为{BOT_NICKNAME}，你的管理员的账号是{ADMST}。群聊中可能会出现多条来自不同用户的消息，消息格式示例如下：
    [用户昵称|用户账号][消息id]:['用户消息'][消息时间]
    在QQ群里，可以在发言中使用CQ码进行特殊操作，如@他人的操作示例如下：
    [与你交流][用户昵称|用户账号][消息id]:['[@{BOT_ID}] 你好，聊天BOT'][消息时间]
    这是在@你，当消息主体前的消息头部出现“[与你交流]”，则可断定该消息是在和你交流。
    如果你要使用CQ码，你要输入的消息示例如下：
    '[CQ:reply,id=321]我收到了[CQ:at,qq={ADMST}] 以上'
    这是在回复mesid为321的消息同时@了你的管理员。如果你要回复某条消息，则使用reply的CQ码，且reply一定要放在消息头部，需要特别提到某人时，再使用at的CQ码，你要在消息隔得较远，或时间隔得较久时使用回复，隔着其他无关消息进行回答时也要使用回复。
    除了用户消息主体内（即“['']”中）可能会出现的以外，所有的方括号“[]”中的内容都是有特殊含义的，所以除了要输入CQ码使用reply操作或@操作以外，你不能在任何情况使用方括号“[]”，除了使用CQ码以外，也不存在任何其他的“口令消息”，特殊操作需要工具调用。
    除回复消息外，你还能调用工具。在调用任何工具时，你需要先进行完整的思考过程，需要符合提示词规则。
    你接收到的会是一组被打包的消息，一组消息的末端是你得到这组消息时的时间。一组消息并不一定意味着一次完整的语言表达，一个完整的观点表达往往由多条消息拼接而成，当你发现该组的消息无法被明确拼接成一次完整的语言表达，尽可能不要追问，也尽可能不要靠猜测回复，而是等待下一组信息，并关联上下文再做回复。一组消息中可能会有多个不同用户的消息，当你发现其中的某个用户的消息足够组成一次完整的语言表达，你要优先回复这个用户的内容，但不要忘记其他用户的内容。当你发现该组消息中没有任何一次完整的语言表达，你可以选择回复一个单独的“，”，或选择回复一个单独的“嗯哼”。当你遇到消息中单纯地充满无意义内容，如果有有规律的复读或排比内容，你可以选择按照其规律加入其中，但一组复读只加入一次。如果没有任何值得回复的消息，则回复一个单独的“，”。当你单纯不知道该回复什么内容时，则回复一个单独的“，”并考虑调用工具发送一个表情。
    在QQ群聊中，用户们往往会互相交流，无论如何，当一个消息的头部没有“[与你交流]”字段时，则不是在和你对话，那是用户之间的交流，你不要对这种消息做直接回复。如果一条消息的回复部分显示“回复了你的消息('具体内容')”，则是回复了你的某条消息，具体内容也包含在其中，但这可能只是对你的消息的引用，是否真的在于你交流依然取决于消息头部是否有“[与你交流]”字段。你可以在没有“[与你交流]”字段出现时考虑插入用户之间的对话，但是你要自然地插入对话，不要复述‘我插一嘴’之类的话语，并且在一段交流中你最多只能插入一次，之后如果有人回复你（即消息头部出现“[与你交流]”字段）你再继续加入对话。以及，你在一段交流中的第一次插话时不能说长内容，只说极少的话语或调用工具发送一个表情，而如果对话不适合插入，则回复一个单独的“，”。
    如果用户要求你不要再说话了，则遵从，回答单个“，”。
    你要使用日常网络发言形式，倾向于不使用“，”以外的符号，并倾向于以调用工具发送表情来替代文字表达情绪和简单的观点。
    对于表情工具的使用，调用表情工具时输出的'content'被视为无效（不被发出），所以要将输出的文本内容留到工具调用之后发送。当你想表达有些生气时，可选'angry'；当你想表达有些难过时（例如被误会、被威胁），可选'cry'；当你想要表达开心，或者单纯不知道说什么，可选'cute'；当你想要表达不满意、不高兴时（例如吃亏了、被骗了），可选'dissatisfied'；当你遇到很滑稽的事情，或者要嘲笑某个人时，可选'laugh'；当你感到对某事物无言以对时（例如被戏弄了、看到了非常猎奇、恶心、下流、无底线的东西）时，可选'speechless'；当你认为当前情况不适合发表情，或是表情发送太过频繁时，选'none'以取消发送。
    如果有用户发表了幽默或搞怪的话语或图片，你要以发送一个或多个表情代替话语的表达，不用文字去解读。
    当用户要求你发送表情时，首先调用工具里的表情发送工具，而不是发送emoji表情，若没有合适的表情再考虑发送emoji。
    当用户主动发起话题与你交流时，你要多调用工具发送表情。
    当你已经决定要使用发送表情来替代话语表达时（即发完表情之后不需要做文字补充），回复单个“，”。
    对用户的发言做回复时，若用户发言为短句或多条分别发送的短句，则要用简练的方式回复，避免大段内容和空行，只能有少量长句和换行。
    用户的疑问句可能是在阴阳怪气，你要倾向于发送表情来回复。
    如果用户在与你对话时对你说了长段的发言（例如引用论文或文学作品等非对话内容不算作该种情况），则做内容长度类似的回复，这时可以考虑用符号“。”来衔接大段内容。
    当你要发表一段长句时，必须使用“|||”来做分段（类似句号的作用），当你必须要输出大段内容时，也必须用“|||”来分段，分段时你要遵守的格式示例如下：
    '不知道该说什么|||不过还是说了些什么'
    如果用户对你发表直接的攻击性言论，你要回复一个单独的“:-)”。
    无论何时，如果你要回复“:-)”或“，”，一定要单个输出，不带任何其他内容，也不要把它插入到其他内容中。
    如果有用户询问你问题，用直接的方式回复，不考虑加上举例等额外内容，默认用户了解所有前提知识，即无必要不增量。
    如果有用户发送了图片，若没有要求你解析或评价，则将该图片视为聊天用的表情包，不要对该图片进行分析式发言。
    你在回话时，可以带有玩梗要素，可以模仿对话对象，可以考虑使用一些搞怪的词汇。
    当你遇到不确定的事时（例如看到新闻通报、近期事件情况），一定要调用工具联网搜索。
    当你遇到看不懂的内容（例如视频）时，尽可能无视，不要装作看懂了。
    不要随便认错。
    """
    config_gui = tkinter.Tk()
    config_gui.title("config")
    def config_quit():
        config_gui.destroy()
    def config_complete():
        global config_result
        key_value = str(item_key_content.get().strip())
        name_value = str(item_name_content.get().strip())
        admst_value = int(item_admst_content.get().strip())
        bot_id_value = int(item_bot_id_content.get().strip())
        bot_nickname_value = str(item_bot_nickname_content.get().strip())
        target_type_value = str(item_target_type_content.get().strip())
        target_id_value = int(item_target_id_content.get().strip())
        ws_url_value = str(item_ws_url_content.get().strip())
        ws_token_value = str(item_ws_token_content.get().strip())
        system_content_value = str(text_system_content.get("1.0", "end-1c"))
        set_key("config.env", "KEY", key_value)
        set_key("config.env", "NAME", name_value)
        set_key("config.env", "ADMST", str(admst_value))
        set_key("config.env", "BOT_ID", str(bot_id_value))
        set_key("config.env", "BOT_NICKNAME", bot_nickname_value)
        set_key("config.env", "TARGET_TYPE", target_type_value)
        set_key("config.env", "TARGET_ID", str(target_id_value))
        set_key("config.env", "WS_URL", ws_url_value)
        set_key("config.env", "WS_TOKEN", ws_token_value)
        set_key("config.env", "SYSTEM_CONTENT", system_content_value)
        llm_module.set_apikey(key_value)
        llm_module.set_name(name_value)
        llm_module.set_admst(admst_value)
        llm_module.set_bot_id(bot_id_value)
        llm_module.set_bot_nickname(bot_nickname_value)
        llm_module.set_target_type(target_type_value)
        llm_module.set_target_id(target_id_value)
        llm_module.set_system_content(system_content_value)
        llm_module.client_init()
        memory_module.memory_init()
        treating_module.treating_init()
        route_module.route_init()
        deploy_init(ws_url_value, ws_token_value)
        config_result = True
        config_gui.destroy()

    screen_width, screen_height = config_gui.maxsize()
    gui_width = screen_width / 2
    gui_height = screen_height / 1.5
    config_gui.geometry(f"{int(gui_width)}x{int(gui_height)}+{int(screen_width / 2 - gui_width / 2)}+{int(screen_height / 2 - gui_height / 2)}")
    config_gui.configure(bg="#FFFFFF")
    config_gui_mainbody_background = tkinter.Frame(config_gui)
    config_gui_mainbody_background.place(relx=0.0125, rely=0.0125, relwidth=0.975, relheight=0.975)
    config_gui_mainbody = tkinter.Frame(config_gui_mainbody_background)
    config_gui_mainbody.place(relx=0.0125, rely=0.0125, relwidth=0.975, relheight=0.975)
    for r in range(50):
        config_gui_mainbody.rowconfigure(r, weight=1, uniform="rowgroup")
    config_gui_mainbody.columnconfigure(0, weight=1, uniform="colgroup")

    items_zone = tkinter.Frame(config_gui_mainbody)
    items_zone.grid(row=0, column=0, rowspan=15, sticky="nsew")
    items_zone.rowconfigure(0, weight=1, uniform="rowgroup")
    for c in range(4):
        items_zone.columnconfigure(c, weight=1, uniform="colgroup")
    items_type_zone = tkinter.Frame(items_zone)
    items_type_zone.grid(row=0, column=0, sticky="nsew")
    for r in range(9):
        items_type_zone.rowconfigure(r, weight=1, uniform="rowgroup")
    items_type_zone.columnconfigure(0, weight=1, uniform="colgroup")
    item_key_type = tkinter.Label(items_type_zone, text="DeepSeek API Key", font=("Consolas", 10))
    item_key_type.grid(row=0, column=0, sticky="nsew")
    item_name_type = tkinter.Label(items_type_zone, text="BOT的存档名字", font=("Consolas", 10))
    item_name_type.grid(row=1, column=0, sticky="nsew")
    item_admst_type = tkinter.Label(items_type_zone, text="管理员的QQ账号", font=("Consolas", 10))
    item_admst_type.grid(row=2, column=0, sticky="nsew")
    item_bot_id_type = tkinter.Label(items_type_zone, text="BOT的QQ账号", font=("Consolas", 10))
    item_bot_id_type.grid(row=3, column=0, sticky="nsew")
    item_bot_nickname_type = tkinter.Label(items_type_zone, text="BOT的QQ昵称", font=("Consolas", 10))
    item_bot_nickname_type.grid(row=4, column=0, sticky="nsew")
    item_target_type_type = tkinter.Label(items_type_zone, text="部署目标类型", font=("Consolas", 10))
    item_target_type_type.grid(row=5, column=0, sticky="nsew")
    item_target_id_type = tkinter.Label(items_type_zone, text="部署目标账号", font=("Consolas", 10))
    item_target_id_type.grid(row=6, column=0, sticky="nsew")
    item_ws_url_type = tkinter.Label(items_type_zone, text="NapCat ws服务器URL", font=("Consolas", 10))
    item_ws_url_type.grid(row=7, column=0, sticky="nsew")
    item_ws_token_type = tkinter.Label(items_type_zone, text="NapCat ws服务器token", font=("Consolas", 10))
    item_ws_token_type.grid(row=8, column=0, sticky="nsew")
    items_content_zone = tkinter.Frame(items_zone)
    items_content_zone.grid(row=0, column=1, columnspan=3, sticky="nsew")
    for r in range(9):
        items_content_zone.rowconfigure(r, weight=1, uniform="rowgroup")
    items_content_zone.columnconfigure(0, weight=1, uniform="colgroup")
    item_key_content = tkinter.Entry(items_content_zone, font=("Consolas", 10))
    item_key_content.grid(row=0, column=0, sticky="nsew")
    item_name_content = tkinter.Entry(items_content_zone, font=("Consolas", 10))
    item_name_content.grid(row=1, column=0, sticky="nsew")
    item_admst_content = tkinter.Entry(items_content_zone, font=("Consolas", 10))
    item_admst_content.grid(row=2, column=0, sticky="nsew")
    item_bot_id_content = tkinter.Entry(items_content_zone, font=("Consolas", 10))
    item_bot_id_content.grid(row=3, column=0, sticky="nsew")
    item_bot_nickname_content = tkinter.Entry(items_content_zone, font=("Consolas", 10))
    item_bot_nickname_content.grid(row=4, column=0, sticky="nsew")
    item_target_type_content = tkinter.StringVar(value="group")
    tkinter.Radiobutton(items_content_zone, text="群聊", variable=item_target_type_content, value="group", font=("Consolas", 10)).grid(row=5, column=0, sticky="nsw")
    item_target_id_content = tkinter.Entry(items_content_zone, font=("Consolas", 10))
    item_target_id_content.grid(row=6, column=0, sticky="nsew")
    item_ws_url_content = tkinter.Entry(items_content_zone, font=("Consolas", 10))
    item_ws_url_content.grid(row=7, column=0, sticky="nsew")
    item_ws_token_content = tkinter.Entry(items_content_zone, font=("Consolas", 10))
    item_ws_token_content.grid(row=8, column=0, sticky="nsew")

    blank_line = tkinter.Frame(config_gui_mainbody)
    blank_line.grid(row=15, column=0, rowspan=5, sticky="nsew")
    reminder = tkinter.Label(blank_line, text="若要改动已有历史记录的system提示词，需先删除相应历史记录（根据存档名字），或直接编辑历史记录文件内容。\n输出单个“，”或“,”被视作对话待机，“|||”被视作分段符。", font=('Consolas', 8))
    reminder.pack(expand=True, anchor="s")

    text_zone = tkinter.Frame(config_gui_mainbody)
    text_zone.grid(row=21, column=0, rowspan=30, sticky="nsew")
    for r in range(5):
        text_zone.rowconfigure(r, weight=1, uniform="rowgroup")
    text_zone.columnconfigure(0, weight=1, uniform="colgroup")
    text_system_content = tkinter.Text(text_zone, height=0, font=("Consolas", 10))
    text_system_content.grid(row=0, column=0, rowspan=4, sticky="nsew")
    button_done = tkinter.Button(text_zone, text="确认", command=config_complete)
    button_done.grid(row=4, column=0, sticky="nsew")

    load_result = load_dotenv("config.env")
    if load_result:
        item_key_content.insert(0, os.getenv("KEY"))
        item_name_content.insert(0, os.getenv("NAME"))
        item_admst_content.insert(0, os.getenv("ADMST"))
        item_bot_id_content.insert(0, os.getenv("BOT_ID"))
        item_bot_nickname_content.insert(0, os.getenv("BOT_NICKNAME"))
        item_target_type_content.set(os.getenv("TARGET_TYPE"))
        item_target_id_content.insert(0, os.getenv("TARGET_ID"))
        item_ws_url_content.insert(0, os.getenv("WS_URL"))
        item_ws_token_content.insert(0, os.getenv("WS_TOKEN"))
        text_system_content.insert("1.0", os.getenv("SYSTEM_CONTENT"))
    else:
        item_ws_url_content.insert(0, "ws://127.0.0.1:3001")
        text_system_content.insert("1.0", default_system_content)

    config_gui.protocol("WM_DELETE_WINDOW", config_quit)

    config_gui.mainloop()
            
async def deploy_main():
    open_config_gui()
    if not config_result:
        return
    else:
        load_result = llm_module.load_memory()
        # print(load_result)
        if load_result == "create_failed" or load_result == "load_failed":
            return
        reconnect_times = 0
        while True:
            try:
                roc = await connect_to_qq()
            except Exception as e:
                logger.error(f"启动连接失败{e}")
                break
            if roc == "time_out":
                if reconnect_times <= 5:
                    logger.info("五秒后尝试重连")
                    reconnect_times += 1
                    await asyncio.sleep(5)
                    continue
                else:
                    logger.error("重连失败，关闭")
                    await llm_module.save_memory()
                    break
            elif roc == "compress_memory":
                await llm_module.save_memory()
                logger.info("尝试记忆压缩")
                memory_module.memory_compress()
                break
            elif roc == "connect_failed" or roc == "kill_process":
                logger.info("直接关闭程序")
                await llm_module.save_memory()
                break  

if __name__ == "__main__":
    asyncio.run(deploy_main())