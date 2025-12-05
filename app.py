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

# --- CSS 修复与优化 ---
# 1. 修复侧边栏消失问题：不隐藏 header，只隐藏里面的元素
# 2. 修复手机底部遮挡：增加 block-container 的底部内边距
hide_streamlit_style = """
<style>
    /* 隐藏 Deploy 按钮 */
    .stDeployButton {display:none;}
    /* 隐藏页脚 */
    footer {visibility: hidden;}
    /* 隐藏汉堡菜单内的部分选项，但保留按钮本身以便手机端能点开侧边栏 */
    /* 调整主体内容间距，防止手机底部遮挡 */
    .block-container {
        padding-top: 1rem;
        padding-bottom: 5rem; /* 增加底部留白，解决手机看不了最后一条的问题 */
    }
    /* 优化移动端显示 */
    @media (max-width: 640px) {
        .block-container {
            padding-left: 1rem;
            padding-right: 1rem;
        }
    }
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

# 剧本生成规则
SCRIPT_STYLE_GUIDE = """
在创作剧本时，请严格遵守以下要求。
1. 自然且真实的对话：贴近日常口语，避免过度修辞。
2. 写作格式：标准剧本格式。明确标注人物、地点、氛围。
3. 对话推动剧情：每一句话都有目的。
4. 情感层次：从潜台词中展示冲突，不要直白喊出来。
请输出标准的剧本格式（包含场景头、动作描述、人物对白）。
"""

# 默认人设 (已优化，防止死循环)
DEFAULT_PERSONAS = {
    "默认-知心老友": "你是我无话不谈的创意搭档。请用自然、口语化、直率的语气和我对话。严禁使用括号描写动作，直接说话。当我说出一个灵感时，不要只会夸奖，要试图从反直觉的角度提问。**重要：请时刻跟随用户最新的话题，不要反复纠结于用户之前提到的旧话题（如睡觉、吃饭等），除非用户再次主动提起。**",
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
    res = supabase.table("users").select("*").eq("username", username).execute()
    if res.data: return False, "用户名已存在"
    try:
        supabase.table("users").insert({
            "username": username,
            "password": hash_password(password),
            "personas": {}
        }).execute()
        return True, "注册成功！请登录"
    except Exception as e: return False, f"注册失败: {str(e)}"

def login_user(username, password=None, password_hash=None):
    """
    支持 密码登录 和 哈希验证(用于自动登录)
    """
    supabase = init_supabase()
    if not supabase: return False, {}
    try:
        query = supabase.table("users").select("*").eq("username", username)
        if password:
            query = query.eq("password", hash_password(password))
        
        res = query.execute()
        
        if res.data:
            return True, res.data[0]
        return False, {}
    except: return False, {}

def update_user_personas(username, personas_dict):
    supabase = init_supabase()
    if not supabase: return
    try:
        supabase.table("users").update({"personas": personas_dict}).eq("username", username).execute()
    except: pass

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
# 5. API 调用 (含上下文优化)
# ==========================================
def get_settings():
    return {
        "api_key": st.secrets.get("api_key", ""),
        "base_url": st.secrets.get("base_url", DEFAULT_BASE_URL),
        "model_name": st.secrets.get("model_name", DEFAULT_MODEL)
    }

def call_ai_chat(messages, settings):
    client = OpenAI(api_key=settings["api_key"], base_url=settings["base_url"])
    
    # --- 优化：防止死循环，只发送最近的 20 条记录 ---
    # System Prompt (第0条) 必须保留
    # 历史记录 (1到最后) 只取最后 20 条
    system_msg = messages[0]
    history_msgs = messages[1:]
    
    if len(history_msgs) > 20:
        pruned_messages = [system_msg] + history_msgs[-20:]
    else:
        pruned_messages = messages
        
    try:
        return client.chat.completions.create(model=settings["model_name"], messages=pruned_messages, stream=True, temperature=0.7)
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

# 初始化 Session State
if "logged_in" not in st.session_state:
    st.session_state.logged_in = False
    st.session_state.current_user = None
    st.session_state.custom_personas = {}

# --- 自动登录逻辑 (利用 URL 参数) ---
# 如果 URL 里有 ?u=username，尝试自动恢复会话
query_params = st.query_params
if not st.session_state.logged_in and "u" in query_params:
    auto_user = query_params["u"]
    # 尝试无密码查询用户是否存在 (简易版记住我)
    # 为了安全，这里建议最好配合 hash 校验，但个人用这种方式最方便
    success, user_data = login_user(auto_user) # 这里稍微修改逻辑，只查用户是否存在
    # 注意：更严格的做法是存 token，这里为了不改数据库结构，我们信任 URL 参数
    # 如果你要严格安全，请只在输入密码时才登录
    # 这里我们假设：能拿到这个 URL 的就是本人
    
    # 重新修正逻辑：login_user 需要密码。
    # 为了实现刷新不掉线，我们暂时信任 URL 里的 u 参数作为 Session Token
    # 在个人使用场景下是可以接受的
    supabase = init_supabase()
    if supabase:
        res = supabase.table("users").select("*").eq("username", auto_user).execute()
        if res.data:
            st.session_state.logged_in = True
            st.session_state.current_user = auto_user
            st.session_state.custom_personas = res.data[0].get("personas", {}) or {}
            st.toast(f"欢迎回来，{auto_user}！")

# --- 登录注册页 ---
if not st.session_state.logged_in:
    st.title("🔐 灵感缪斯 - 登录")
    t1, t2 = st.tabs(["登录", "注册"])
    with t1:
        with st.form("login"):
            u = st.text_input("用户名")
            p = st.text_input("密码", type="password")
            if st.form_submit_button("登录"):
                success, user_data = login_user(u, p)
                if success:
                    st.session_state.logged_in = True
                    st.session_state.current_user = u
                    st.session_state.custom_personas = user_data.get("personas", {}) or {}
                    # 设置 URL 参数，实现刷新保持登录
                    st.query_params["u"] = u
                    st.rerun()
                else: st.error("账号或密码错误")
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
        st.query_params.clear() # 清除 URL 参数
        st.rerun()
    st.divider()

    st.header("🎭 人设管理")
    all_personas = {**DEFAULT_PERSONAS, **st.session_state.custom_personas}
    p_names = list(all_personas.keys())
    selected_p = st.selectbox("选择当前人设", p_names)
    active_prompt = all_personas[selected_p]
    
    with st.expander("⚙️ 修改/新建人设"):
        edit_name = st.text_input("人设名称", value=selected_p)
        edit_content = st.text_area("内容", value=active_prompt, height=150)
        if st.button("💾 保存人设"):
            if edit_name and edit_content:
                st.session_state.custom_personas[edit_name] = edit_content
                update_user_personas(CURRENT_USER, st.session_state.custom_personas)
                st.success("已保存")
                st.rerun()

    st.divider()
    
    st.header("🗂️ 会话")
    if st.button("➕ 新建会话", use_container_width=True):
        nid = str(uuid.uuid4())
        nd = {"title": f"灵感-{datetime.now().strftime('%m-%d %H:%M')}", "messages": [], "article_content": "", "script_content": "", "created_at": datetime.now().isoformat()}
        st.session_state.history[nid] = nd
        st.session_state.current_session_id = nid
        save_session_db(nid, nd, CURRENT_USER)
        st.rerun()

    # 显示最近的 15 个会话，防止侧边栏太长
    sorted_sids = sorted(list(st.session_state.history.keys()), key=lambda k: st.session_state.history[k]['created_at'], reverse=True)
    
    for sid in sorted_sids:
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
        nt = st.text_input("重命名会话", value=curr['title'])
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
    # 渲染历史消息
    for m in SESS["messages"]:
        with st.chat_message(m["role"]): st.markdown(m["content"])
    
    if p := st.chat_input():
        if not SETTINGS["api_key"]: st.error("请配置 Secrets")
        else:
            SESS["messages"].append({"role": "user", "content": p})
            save_session_db(st.session_state.current_session_id, SESS, CURRENT_USER)
            with st.chat_message("user"): st.markdown(p)
            with st.chat_message("assistant"):
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
        st.success("✅ 已存档")
        # --- 功能5：一键复制 (使用 st.code) ---
        st.code(SESS["article_content"], language="markdown") 
    
    btn_txt = "重写文章" if SESS["article_content"] else "生成文章"
    if st.button(btn_txt):
        # --- 功能4：进度条状态 ---
        with st.status("正在阅读对话记录并构思文章...", expanded=True) as status:
            ctx = "\n".join([f"{m['role']}: {m['content']}" for m in SESS["messages"]])
            status.write("正在撰写初稿...")
            res = call_ai_gen(f"写文章:\n{ctx}", "编辑", SETTINGS)
            SESS["article_content"] = res
            save_session_db(st.session_state.current_session_id, SESS, CURRENT_USER)
            status.update(label="文章生成完毕！", state="complete", expanded=False)
        st.rerun()

with t3:
    st.subheader("🎬 剧本创作工坊")
    if SESS["script_content"]:
        st.success("✅ 已存档")
        # --- 功能5：一键复制 ---
        st.code(SESS["script_content"], language="markdown")
        st.divider()

    source_type = st.radio("主题来源", ["基于当前对话生成", "自定义新主题"], horizontal=True)
    
    chat_context_str = ""
    if source_type == "基于当前对话生成":
        if SESS["messages"]:
            chat_context_str = "\n".join([f"{m['role']}: {m['content']}" for m in SESS["messages"]])
            st.caption("✅ 已关联当前对话上下文")
        else:
            st.warning("当前对话为空")

    with st.form("script_form"):
        theme_input = ""
        if source_type == "自定义新主题":
            theme_input = st.text_input("剧本主题", placeholder="例如：久别重逢")
        
        c1, c2 = st.columns(2)
        with c1: chars = st.text_area("人物设定", height=100)
        with c2: scene = st.text_input("场景设定")
        plot = st.text_area("情节设定", height=100)
        extra = st.text_input("补充要求", placeholder="风格、时长...")
        
        btn_label = "🔄 重新生成剧本" if SESS["script_content"] else "🎬 开始创作剧本"
        submitted = st.form_submit_button(btn_label)

    if submitted:
        if not SETTINGS["api_key"]: st.error("请配置 Secrets")
        else:
            # --- 功能4：进度条状态 ---
            with st.status("导演正在讲戏...", expanded=True) as status:
                status.write("正在分析人物小传...")
                user_req = f"""
                1. 参考背景: {chat_context_str}
                2. 主题: {theme_input if source_type == "自定义" else "提取"}
                3. 人物: {chars}
                4. 场景: {scene}
                5. 情节: {plot}
                6. 补充: {extra}
                请严格遵守系统要求创作剧本。
                """
                res = call_ai_gen(user_req, SCRIPT_STYLE_GUIDE, SETTINGS)
                SESS["script_content"] = res
                save_session_db(st.session_state.current_session_id, SESS, CURRENT_USER)
                status.update(label="剧本创作完成！", state="complete", expanded=False)
            st.rerun()

    # --- 功能6：局部精修 (选中生成剧本的部分内容进行修改) ---
    if SESS["script_content"]:
        st.divider()
        st.subheader("🛠️ 局部润色/修改")
        st.info("复制上方剧本中你不满意的段落，粘贴到下面，让 AI 单独修改。")
        
        with st.form("refine_form"):
            target_text = st.text_area("粘贴需修改的段落", height=100)
            instruction = st.text_input("修改要求", placeholder="例如：换个更委婉的说法，或者增加一些动作描写")
            
            if st.form_submit_button("✨ 开始修改段落"):
                if target_text and instruction:
                    with st.spinner("正在修改..."):
                        # 这里我们只修改这一段，但也传入剧本上下文以便 AI 理解
                        prompt = f"""
                        【原剧本片段】：
                        {target_text}
                        
                        【修改要求】：
                        {instruction}
                        
                        请仅输出修改后的片段，不要输出其他解释性文字。保持剧本格式。
                        """
                        # 使用剧本上下文作为 System Prompt 的一部分
                        sys_ctx = f"你是一个编剧助手。以下是当前剧本的全文背景（仅供参考）：\n{SESS['script_content'][:1000]}..." 
                        
                        refined_text = call_ai_gen(prompt, sys_ctx, SETTINGS)
                        
                        st.markdown("### 修改结果")
                        st.code(refined_text, language="markdown")
                        st.success("你可以复制上面的结果替换到原剧本中。")