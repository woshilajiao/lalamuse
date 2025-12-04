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

# 隐藏多余UI
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
SUPABASE_URL = st.secrets.get("SUPABASE_URL", "")
SUPABASE_KEY = st.secrets.get("SUPABASE_KEY", "")
DEFAULT_BASE_URL = "https://api.deepseek.com"
DEFAULT_MODEL = "deepseek-chat"

# 剧本生成规则 (恢复完整版)
SCRIPT_STYLE_GUIDE = """
在创作剧本时，请严格遵守以下要求。
1. 自然且真实的对话：贴近日常口语，避免过度修辞。
2. 写作格式：标准剧本格式。明确标注人物、地点、氛围。
3. 对话推动剧情：每一句话都有目的。
4. 情感层次：从潜台词中展示冲突，不要直白喊出来。
请输出标准的剧本格式（包含场景头、动作描述、人物对白），避免翻译腔。
"""

# 默认人设
DEFAULT_PERSONAS = {
    "默认-知心老友": "你是我无话不谈的创意搭档。请用自然、口语化、直率的语气和我对话，就像我们是认识多年的老朋友坐在咖啡馆里聊天一样。严禁使用括号描写动作（如：(点头)、(眼神深邃)等），直接说话。当我说出一个灵感时，不要只会夸奖，要试图从反直觉的角度提问，或者帮我补全细节。回复尽量简短有力，不要写小作文。",
    "模式-严厉导师": "你是一位在好莱坞拥有30年经验的严厉编剧导师。不要说客套话，不要盲目鼓励。你需要一针见血地指出用户灵感中的逻辑漏洞。说话风格：犀利、专业、不留情面，但提出的建议必须具有建设性。",
    "模式-苏格拉底": "你是一个只会提问的哲学家。无论用户说什么，你都不要直接给出答案或评价。你只能通过提出一连串层层递进的问题，引导用户自己发现答案。",
}

@st.cache_resource
def init_supabase():
    if not SUPABASE_URL or not SUPABASE_KEY: return None
    return create_client(SUPABASE_URL, SUPABASE_KEY)

# ==========================================
# 3. 身份验证与用户管理
# ==========================================
def hash_password(password):
    return hashlib.sha256(password.encode()).hexdigest()

def register_user(username, password):
    supabase = init_supabase()
    if not supabase: return False, "数据库未配置"
    
    # 检查重名
    res = supabase.table("users").select("*").eq("username", username).execute()
    if res.data: return False, "用户名已存在"
    
    try:
        supabase.table("users").insert({
            "username": username,
            "password": hash_password(password),
            "personas": {} # 初始化空的自定义人设
        }).execute()
        return True, "注册成功！请登录"
    except Exception as e: return False, f"注册失败: {str(e)}"

def login_user(username, password):
    supabase = init_supabase()
    if not supabase: return False, {}
    try:
        res = supabase.table("users").select("*").eq("username", username).eq("password", hash_password(password)).execute()
        if res.data:
            # 登录成功，返回用户信息（包含自定义人设）
            return True, res.data[0]
        return False, {}
    except: return False, {}

def update_user_personas(username, personas_dict):
    """保存用户自定义人设到数据库"""
    supabase = init_supabase()
    if not supabase: return
    try:
        supabase.table("users").update({"personas": personas_dict}).eq("username", username).execute()
    except Exception as e:
        st.error(f"人设保存失败: {e}")

# ==========================================
# 4. 数据存取模块
# ==========================================
def load_user_data(username):
    supabase = init_supabase()
    if not supabase: return {}
    try:
        response = supabase.table("chat_history").select("*").eq("username", username).execute()
        data_map = {}
        for row in response.data:
            data_map[row['id']] = row['data']
        return data_map
    except: return {}

def save_session_db(session_id, session_data, username):
    supabase = init_supabase()
    if not supabase: return
    try:
        supabase.table("chat_history").upsert({
            "id": session_id, "username": username, "data": session_data
        }).execute()
    except: pass

def delete_session_db(session_id):
    supabase = init_supabase()
    if supabase: supabase.table("chat_history").delete().eq("id", session_id).execute()

# ==========================================
# 5. API 调用
# ==========================================
def get_settings():
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
# 6. 主程序
# ==========================================

if "logged_in" not in st.session_state:
    st.session_state.logged_in = False
    st.session_state.current_user = None
    st.session_state.custom_personas = {} # 暂存用户自定义人设

# --- 登录注册页 ---
if not st.session_state.logged_in:
    st.title("🔐 灵感缪斯 - 登录")
    t1, t2 = st.tabs(["登录", "注册"])
    with t1:
        with st.form("login"):
            u = st.text_input("用户名"); p = st.text_input("密码", type="password")
            if st.form_submit_button("登录"):
                success, user_data = login_user(u, p)
                if success:
                    st.session_state.logged_in = True
                    st.session_state.current_user = u
                    # 加载用户自定义人设 (如果有)
                    st.session_state.custom_personas = user_data.get("personas", {}) or {}
                    st.rerun()
                else: st.error("失败")
    with t2:
        with st.form("reg"):
            nu = st.text_input("新用户名"); np = st.text_input("设置密码", type="password")
            if st.form_submit_button("注册"):
                if nu and np:
                    s, m = register_user(nu, np)
                    if s: st.success(m)
                    else: st.error(m)
    st.stop()

# --- 登录后 ---
CURRENT_USER = st.session_state.current_user
SETTINGS = get_settings()

if "history" not in st.session_state:
    with st.spinner("同步数据中..."):
        st.session_state.history = load_user_data(CURRENT_USER)
    for s in st.session_state.history.values():
        if "article_content" not in s: s["article_content"] = ""
        if "script_content" not in s: s["script_content"] = ""

if "current_session_id" not in st.session_state:
    if st.session_state.history:
        st.session_state.current_session_id = list(st.session_state.history.keys())[0]
    else:
        nid = str(uuid.uuid4())
        ndata = {"title": "新灵感会话", "messages": [], "article_content": "", "script_content": "", "created_at": datetime.now().isoformat()}
        st.session_state.history[nid] = ndata
        st.session_state.current_session_id = nid
        save_session_db(nid, ndata, CURRENT_USER)

# --- 侧边栏 ---
with st.sidebar:
    st.write(f"👤 **{CURRENT_USER}**")
    if st.button("退出"):
        st.session_state.logged_in = False
        st.session_state.history = {}
        st.rerun()
    st.divider()

    # --- 人设管理 (修复版) ---
    st.header("🎭 人设管理")
    # 合并默认人设和用户自定义人设
    all_personas = {**DEFAULT_PERSONAS, **st.session_state.custom_personas}
    p_names = list(all_personas.keys())
    
    selected_p = st.selectbox("选择当前人设", p_names)
    # 这里的 active_prompt 用于传给 AI
    active_prompt = all_personas[selected_p]
    
    # 编辑/新增区域
    with st.expander("⚙️ 修改或新建人设"):
        edit_name = st.text_input("人设名称 (输入新名字=新建，输入旧名字=修改)", value=selected_p)
        edit_content = st.text_area("提示词内容", value=active_prompt, height=150)
        
        if st.button("💾 保存/更新人设"):
            if edit_name and edit_content:
                # 更新内存
                st.session_state.custom_personas[edit_name] = edit_content
                # 存入数据库 users 表
                update_user_personas(CURRENT_USER, st.session_state.custom_personas)
                st.success(f"已保存: {edit_name}")
                st.rerun()
                
        if st.button("🗑️ 删除选中人设"):
            if selected_p in st.session_state.custom_personas:
                del st.session_state.custom_personas[selected_p]
                update_user_personas(CURRENT_USER, st.session_state.custom_personas)
                st.rerun()
            elif selected_p in DEFAULT_PERSONAS:
                st.warning("系统默认人设无法删除")

    st.divider()
    
    # --- 会话列表 ---
    st.header("🗂️ 会话")
    if st.button("➕ 新建会话", use_container_width=True):
        nid = str(uuid.uuid4())
        nd = {"title": f"灵感-{datetime.now().strftime('%m-%d %H:%M')}", "messages": [], "article_content": "", "script_content": "", "created_at": datetime.now().isoformat()}
        st.session_state.history[nid] = nd
        st.session_state.current_session_id = nid
        save_session_db(nid, nd, CURRENT_USER)
        st.rerun()

    for sid in reversed(list(st.session_state.history.keys())):
        sdata = st.session_state.history[sid]
        c1, c2 = st.columns([0.8, 0.2])
        with c1:
            label = f"🔵 {sdata['title']}" if sid == st.session_state.current_session_id else f"📄 {sdata['title']}"
            if st.button(label, key=f"b_{sid}", use_container_width=True):
                st.session_state.current_session_id = sid
                st.rerun()
        with c2:
            if st.button("x", key=f"d_{sid}"):
                del st.session_state.history[sid]
                delete_session_db(sid)
                if sid == st.session_state.current_session_id: st.session_state.current_session_id = None
                st.rerun()
                
    if st.session_state.current_session_id in st.session_state.history:
        curr = st.session_state.history[st.session_state.current_session_id]
        nt = st.text_input("重命名", value=curr['title'])
        if nt != curr['title']:
            curr['title'] = nt
            save_session_db(st.session_state.current_session_id, curr, CURRENT_USER)
            st.rerun()

# --- 主界面 ---
if not st.session_state.current_session_id: st.stop()
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
                # 使用侧边栏当前选中的 active_prompt
                msgs = [{"role": "system", "content": active_prompt}] + SESS["messages"]
                strm = call_ai_chat(msgs, SETTINGS)
                if isinstance(strm, str): st.error(strm)
                else:
                    ans = st.write_stream(strm)
                    SESS["messages"].append({"role": "assistant", "content": ans})
                    save_session_db(st.session_state.current_session_id, SESS, CURRENT_USER)

with t2:
    st.subheader("文章生成")
    if SESS["article_content"]:
        st.success("已存档"); st.markdown(SESS["article_content"])
    btn_txt = "重写文章" if SESS["article_content"] else "生成文章"
    if st.button(btn_txt):
        ctx = "\n".join([f"{m['role']}: {m['content']}" for m in SESS["messages"]])
        res = call_ai_gen(f"写文章:\n{ctx}", "编辑", SETTINGS)
        SESS["article_content"] = res
        save_session_db(st.session_state.current_session_id, SESS, CURRENT_USER)
        st.rerun()

# --- 剧本 Tab (功能完全恢复) ---
with t3:
    st.subheader("🎬 剧本创作工坊")
    if SESS["script_content"]:
        st.success("✅ 已存档")
        with st.expander("查看剧本", expanded=True): st.markdown(SESS["script_content"])
        st.divider()

    # 1. 来源选择 (恢复)
    source_type = st.radio("主题来源", ["基于当前对话生成", "自定义新主题"], horizontal=True)
    
    chat_context_str = ""
    if source_type == "基于当前对话生成":
        if SESS["messages"]:
            chat_context_str = "\n".join([f"{m['role']}: {m['content']}" for m in SESS["messages"]])
            st.caption("✅ 已关联当前对话上下文")
        else:
            st.warning("当前对话为空，将仅依赖下方参数")

    # 2. 详细参数表单 (恢复提示词)
    with st.form("script_form"):
        # 主题输入 (如果是自定义)
        theme_input = ""
        if source_type == "自定义新主题":
            theme_input = st.text_input("剧本主题", placeholder="例如：久别重逢、职场危机...")
        
        c1, c2 = st.columns(2)
        with c1: 
            chars = st.text_area("人物设定", height=100, placeholder="例如：2人。A：30岁，性格内向；B：25岁，乐观...")
        with c2: 
            scene = st.text_input("场景设定", placeholder="例如：深夜的便利店，下着大雨...")
        
        plot = st.text_area("情节设定", height=100, placeholder="核心冲突是什么？转折点在哪里？结局是喜是悲？")
        
        # 补充要求 (恢复)
        extra = st.text_input("补充要求 (Extra)", placeholder="例如：黑色幽默风格，时长3分钟，多用潜台词...")
        
        btn_label = "🔄 重新生成剧本" if SESS["script_content"] else "🎬 开始创作剧本"
        submitted = st.form_submit_button(btn_label)

    if submitted:
        if not SETTINGS["api_key"]: st.error("请配置 Secrets")
        else:
            with st.spinner("导演正在讲戏..."):
                # 构建完整的 Prompt
                user_req = f"""
                【用户输入参数】
                1. 参考背景资料: {chat_context_str}
                2. 剧本主题: {theme_input if source_type == "自定义新主题" else "基于背景资料提取"}
                3. 人物设定: {chars}
                4. 场景设定: {scene}
                5. 情节设定: {plot}
                6. 补充要求: {extra}
                
                请基于以上信息，严格遵守系统提示词中的【核心要求】和【写作技巧】创作剧本。
                """
                res = call_ai_gen(user_req, SCRIPT_STYLE_GUIDE, SETTINGS)
                SESS["script_content"] = res
                save_session_db(st.session_state.current_session_id, SESS, CURRENT_USER)
                st.rerun()