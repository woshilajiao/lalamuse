import streamlit as st
import json
import os
import uuid
import hashlib
from datetime import datetime
from openai import OpenAI
from supabase import create_client, Client

# ==========================================
# 1. 基础配置与样式
# ==========================================
st.set_page_config(
    page_title="灵感缪斯",
    page_icon="💡",
    layout="wide",
    initial_sidebar_state="expanded"
)

# 隐藏多余UI，打造 APP 沉浸感
hide_streamlit_style = """
<style>
    #MainMenu {visibility: hidden;}
    footer {visibility: hidden;}
    [data-testid="stToolbar"] {visibility: hidden;}
    .stDeployButton {display:none;}
    .block-container {padding-top: 1rem; padding-bottom: 0rem;}
</style>
"""
st.markdown(hide_streamlit_style, unsafe_allow_html=True)

# ==========================================
# 2. 全局常量与数据库初始化
# ==========================================
# 尝试从 Secrets 获取配置
SUPABASE_URL = st.secrets.get("SUPABASE_URL", "")
SUPABASE_KEY = st.secrets.get("SUPABASE_KEY", "")
DEFAULT_BASE_URL = "https://api.deepseek.com"
DEFAULT_MODEL = "deepseek-chat"

# 剧本与人设预设
SCRIPT_STYLE_GUIDE = "请输出标准剧本格式（场景头、动作、对白），贴近生活，避免翻译腔。"
DEFAULT_PERSONAS = {
    "默认-知心老友": "你是我无话不谈的创意搭档。请用自然、口语化、直率的语气...",
    "模式-严厉导师": "你是一位在好莱坞拥有30年经验的严厉编剧导师...",
    "模式-苏格拉底": "你是一个只会提问的哲学家...",
}

@st.cache_resource
def init_supabase():
    """初始化数据库连接"""
    if not SUPABASE_URL or not SUPABASE_KEY:
        return None
    return create_client(SUPABASE_URL, SUPABASE_KEY)

# ==========================================
# 3. 身份验证模块 (Auth)
# ==========================================
def hash_password(password):
    """简单的密码哈希处理"""
    return hashlib.sha256(password.encode()).hexdigest()

def register_user(username, password):
    """注册新用户"""
    supabase = init_supabase()
    if not supabase: return False, "数据库未配置"
    
    # 检查用户是否存在
    res = supabase.table("users").select("*").eq("username", username).execute()
    if res.data:
        return False, "用户名已存在"
    
    # 插入新用户
    try:
        supabase.table("users").insert({
            "username": username,
            "password": hash_password(password)
        }).execute()
        return True, "注册成功！请登录"
    except Exception as e:
        return False, f"注册失败: {str(e)}"

def login_user(username, password):
    """用户登录"""
    supabase = init_supabase()
    if not supabase: return False
    
    try:
        res = supabase.table("users").select("*").eq("username", username).eq("password", hash_password(password)).execute()
        if res.data:
            return True
        return False
    except:
        return False

# ==========================================
# 4. 数据存取模块 (带权限隔离)
# ==========================================
def load_user_data(username):
    """加载指定用户的数据"""
    supabase = init_supabase()
    if not supabase: return {}
    try:
        # 只筛选当前 user 的数据
        response = supabase.table("chat_history").select("*").eq("username", username).execute()
        data_map = {}
        for row in response.data:
            data_map[row['id']] = row['data']
        return data_map
    except Exception as e:
        st.error(f"数据加载失败: {e}")
        return {}

def save_session_db(session_id, session_data, username):
    """保存会话到数据库"""
    supabase = init_supabase()
    if not supabase: return
    try:
        supabase.table("chat_history").upsert({
            "id": session_id,
            "username": username, # 关键：标记数据归属
            "data": session_data
        }).execute()
    except Exception as e:
        print(f"Save error: {e}")

def delete_session_db(session_id):
    supabase = init_supabase()
    if supabase:
        supabase.table("chat_history").delete().eq("id", session_id).execute()

# ==========================================
# 5. API 调用模块
# ==========================================
def get_settings():
    # 优先读 Secrets
    return {
        "api_key": st.secrets.get("api_key", ""),
        "base_url": st.secrets.get("base_url", DEFAULT_BASE_URL),
        "model_name": st.secrets.get("model_name", DEFAULT_MODEL)
    }

def call_ai_chat(messages, settings):
    client = OpenAI(api_key=settings["api_key"], base_url=settings["base_url"])
    try:
        return client.chat.completions.create(model=settings["model_name"], messages=messages, stream=True, temperature=0.7)
    except Exception as e: return f"Error: {str(e)}"

def call_ai_gen(prompt, system, settings):
    client = OpenAI(api_key=settings["api_key"], base_url=settings["base_url"])
    try:
        res = client.chat.completions.create(
            model=settings["model_name"],
            messages=[{"role": "system", "content": system}, {"role": "user", "content": prompt}],
            temperature=1.0
        )
        return res.choices[0].message.content
    except Exception as e: return f"Error: {str(e)}"

# ==========================================
# 6. 主程序逻辑
# ==========================================

# 初始化 session state
if "logged_in" not in st.session_state:
    st.session_state.logged_in = False
    st.session_state.current_user = None

# --- 登录界面 (如果未登录) ---
if not st.session_state.logged_in:
    st.title("🔐 灵感缪斯 - 登录")
    
    tab1, tab2 = st.tabs(["登录", "注册新账号"])
    
    with tab1:
        with st.form("login_form"):
            user = st.text_input("用户名")
            pwd = st.text_input("密码", type="password")
            submitted = st.form_submit_button("登录")
            if submitted:
                if login_user(user, pwd):
                    st.session_state.logged_in = True
                    st.session_state.current_user = user
                    st.success("登录成功！")
                    st.rerun()
                else:
                    st.error("用户名或密码错误")

    with tab2:
        with st.form("reg_form"):
            new_user = st.text_input("设置用户名")
            new_pwd = st.text_input("设置密码", type="password")
            submitted_reg = st.form_submit_button("注册")
            if submitted_reg:
                if new_user and new_pwd:
                    success, msg = register_user(new_user, new_pwd)
                    if success: st.success(msg)
                    else: st.error(msg)
                else:
                    st.warning("请填写完整")
    
    st.stop() # 🛑 只有登录成功才会继续向下执行

# ==========================================
# --- 登录后的主 APP 界面 ---
# ==========================================

CURRENT_USER = st.session_state.current_user
SETTINGS = get_settings()

# 加载该用户的数据
if "history" not in st.session_state:
    with st.spinner(f"正在同步 {CURRENT_USER} 的灵感库..."):
        st.session_state.history = load_user_data(CURRENT_USER)
    # 补全字段
    for s in st.session_state.history.values():
        if "article_content" not in s: s["article_content"] = ""
        if "script_content" not in s: s["script_content"] = ""

if "personas" not in st.session_state:
    st.session_state.personas = DEFAULT_PERSONAS.copy()

# 初始化会话ID
if "current_session_id" not in st.session_state:
    if st.session_state.history:
        st.session_state.current_session_id = list(st.session_state.history.keys())[0]
    else:
        # 新用户没数据，创建第一个
        new_id = str(uuid.uuid4())
        new_data = {
            "title": "新灵感会话", "messages": [], "article_content": "", "script_content": "",
            "created_at": datetime.now().isoformat()
        }
        st.session_state.history[new_id] = new_data
        st.session_state.current_session_id = new_id
        save_session_db(new_id, new_data, CURRENT_USER)

# 侧边栏
with st.sidebar:
    st.write(f"👤 当前用户: **{CURRENT_USER}**")
    if st.button("退出登录"):
        st.session_state.logged_in = False
        st.session_state.current_user = None
        st.session_state.history = {} # 清空缓存
        st.rerun()
    st.divider()

    # 人设
    st.header("🎭 人设")
    p_names = list(st.session_state.personas.keys())
    sel_p = st.selectbox("选择", p_names, label_visibility="collapsed")
    curr_prompt = st.text_area("内容", value=st.session_state.personas[sel_p], height=100)
    
    # 会话列表
    st.header("🗂️ 会话")
    if st.button("➕ 新建", use_container_width=True):
        nid = str(uuid.uuid4())
        ndata = {
            "title": f"灵感-{datetime.now().strftime('%m-%d %H:%M')}", 
            "messages": [], "article_content": "", "script_content": "",
            "created_at": datetime.now().isoformat()
        }
        st.session_state.history[nid] = ndata
        st.session_state.current_session_id = nid
        save_session_db(nid, ndata, CURRENT_USER)
        st.rerun()

    for sid in reversed(list(st.session_state.history.keys())):
        sdata = st.session_state.history[sid]
        c1, c2 = st.columns([0.8, 0.2])
        with c1:
            lbl = f"🔵 {sdata['title']}" if sid == st.session_state.current_session_id else f"📄 {sdata['title']}"
            if st.button(lbl, key=f"b_{sid}", use_container_width=True):
                st.session_state.current_session_id = sid
                st.rerun()
        with c2:
            if st.button("x", key=f"d_{sid}"):
                del st.session_state.history[sid]
                delete_session_db(sid)
                if sid == st.session_state.current_session_id: st.session_state.current_session_id = None
                st.rerun()
    
    # 重命名
    if st.session_state.current_session_id in st.session_state.history:
        curr = st.session_state.history[st.session_state.current_session_id]
        nt = st.text_input("重命名", value=curr['title'])
        if nt != curr['title']:
            curr['title'] = nt
            save_session_db(st.session_state.current_session_id, curr, CURRENT_USER)
            st.rerun()

# 主界面
if not st.session_state.current_session_id or st.session_state.current_session_id not in st.session_state.history:
    st.info("点击左侧新建会话")
    st.stop()

SESS = st.session_state.history[st.session_state.current_session_id]
st.title(SESS['title'])

t1, t2, t3 = st.tabs(["💬 对话", "📝 文章", "🎬 剧本"])

with t1:
    for m in SESS["messages"]:
        with st.chat_message(m["role"]): st.markdown(m["content"])
    if p := st.chat_input():
        if not SETTINGS["api_key"]: st.error("请配置 Secrets")
        else:
            SESS["messages"].append({"role": "user", "content": p})
            save_session_db(st.session_state.current_session_id, SESS, CURRENT_USER)
            with st.chat_message("user"): st.markdown(p)
            with st.chat_message("assistant"):
                msgs = [{"role": "system", "content": curr_prompt}] + SESS["messages"]
                strm = call_ai_chat(msgs, SETTINGS)
                if isinstance(strm, str): st.error(strm)
                else:
                    ans = st.write_stream(strm)
                    SESS["messages"].append({"role": "assistant", "content": ans})
                    save_session_db(st.session_state.current_session_id, SESS, CURRENT_USER)

with t2:
    if SESS["article_content"]:
        st.success("已存档"); st.markdown(SESS["article_content"])
        if st.button("重写文章"):
            ctx = "\n".join([f"{m['role']}: {m['content']}" for m in SESS["messages"]])
            res = call_ai_gen(f"写文章:\n{ctx}", "编辑", SETTINGS)
            SESS["article_content"] = res
            save_session_db(st.session_state.current_session_id, SESS, CURRENT_USER)
            st.rerun()
    elif st.button("生成文章"):
        ctx = "\n".join([f"{m['role']}: {m['content']}" for m in SESS["messages"]])
        res = call_ai_gen(f"写文章:\n{ctx}", "编辑", SETTINGS)
        SESS["article_content"] = res
        save_session_db(st.session_state.current_session_id, SESS, CURRENT_USER)
        st.rerun()

with t3:
    if SESS["script_content"]:
        st.success("已存档"); st.markdown(SESS["script_content"])
    with st.form("script"):
        chars = st.text_area("人物"); scn = st.text_input("场景"); plt = st.text_area("情节")
        if st.form_submit_button("创作剧本"):
            req = f"人物:{chars}\n场景:{scn}\n情节:{plt}"
            res = call_ai_gen(req, SCRIPT_STYLE_GUIDE, SETTINGS)
            SESS["script_content"] = res
            save_session_db(st.session_state.current_session_id, SESS, CURRENT_USER)
            st.rerun()