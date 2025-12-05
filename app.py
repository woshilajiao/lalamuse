import streamlit as st
import json
import os
import uuid
import hashlib
import io
from datetime import datetime
from openai import OpenAI
from supabase import create_client, Client
from docx import Document
from docx.shared import Pt, Inches
from docx.enum.text import WD_ALIGN_PARAGRAPH

# ==========================================
# 1. 基础配置与样式
# ==========================================
st.set_page_config(
    page_title="灵感缪斯 Pro",
    page_icon="🎬",
    layout="wide",
    initial_sidebar_state="expanded"
)

hide_streamlit_style = """
<style>
    .stDeployButton {display:none;}
    footer {visibility: hidden;}
    .block-container {padding-top: 1rem; padding-bottom: 5rem;}
    /* 优化剧本显示字体，更有编剧感 */
    code {font-family: 'Courier New', Courier, monospace !important;}
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

# 剧本生成规则 (更新为支持大纲的 Prompt)
SCRIPT_STYLE_GUIDE = """
你是一名好莱坞专业编剧。请严格按照行业标准格式创作剧本。
格式要求：
1. 场景标题 (SCENE HEADING)：使用 "INT." 或 "EXT." 开头，全大写。
2. 动作描述 (ACTION)：现在的时态，描述画面。
3. 人物名 (CHARACTER)：全大写，居中。
4. 对白 (DIALOGUE)：居中。
5. 括号 (PARENTHETICAL)：用于表达语气，居中。
"""

DEFAULT_PERSONAS = {
    "默认-知心老友": "你是我无话不谈的创意搭档。请用自然、口语化、直率的语气和我对话。严禁使用括号描写动作，直接说话。**时刻跟随用户最新的话题**。",
    "模式-严厉导师": "你是一位在好莱坞拥有30年经验的严厉编剧导师。一针见血地指出逻辑漏洞。风格犀利、专业。",
    "模式-苏格拉底": "你是一个只会提问的哲学家。通过层层递进的问题引导用户发现答案。",
}

@st.cache_resource
def init_supabase():
    if not SUPABASE_URL or not SUPABASE_KEY: return None
    return create_client(SUPABASE_URL, SUPABASE_KEY)

# ==========================================
# 3. 工具函数：Word 导出与流式处理
# ==========================================

def create_docx(script_content):
    """
    生成行业标准格式的 Word 剧本 (.docx)
    """
    doc = Document()
    
    # 设置默认字体为 Courier New (剧本标准)
    style = doc.styles['Normal']
    font = style.font
    font.name = 'Courier New'
    font.size = Pt(12)

    doc.add_heading('剧本初稿', 0)

    # 简单的剧本格式解析器
    lines = script_content.split('\n')
    for line in lines:
        line = line.strip()
        if not line:
            continue
            
        p = doc.add_paragraph()
        runner = p.add_run(line)
        runner.font.name = 'Courier New'
        
        # 简单的规则判断格式
        if line.startswith("INT.") or line.startswith("EXT.") or line.startswith("内.") or line.startswith("外."):
            # 场景标题：加粗
            runner.bold = True
            p.paragraph_format.space_before = Pt(12)
            p.paragraph_format.space_after = Pt(12)
        
        elif line.isupper() and len(line) < 20 and not line.startswith("("):
            # 人物名 (假设全大写且短)：居中
            p.alignment = WD_ALIGN_PARAGRAPH.CENTER
            p.paragraph_format.space_before = Pt(12)
            p.paragraph_format.left_indent = Inches(2.0) # 视觉居中
        
        elif line.startswith("(") and line.endswith(")"):
             # 括号舞台指示：居中
            p.alignment = WD_ALIGN_PARAGRAPH.CENTER
            p.paragraph_format.left_indent = Inches(1.5)
            
        else:
            # 动作描述或对白
            # 这里简单处理，默认左对齐，实际剧本软件对白需要缩进
            # 为了兼容性，这里不做过度复杂的缩进判断，保持左对齐清晰可读
            p.paragraph_format.space_after = Pt(6)

    # 保存到内存流
    buffer = io.BytesIO()
    doc.save(buffer)
    buffer.seek(0)
    return buffer

def stream_parser(stream):
    """解析流式响应并返回生成器"""
    for chunk in stream:
        if chunk.choices[0].delta.content is not None:
            yield chunk.choices[0].delta.content

# ==========================================
# 4. 身份验证与用户管理 (保持原有逻辑)
# ==========================================
def hash_password(password):
    return hashlib.sha256(password.encode()).hexdigest()

def register_user(username, password):
    supabase = init_supabase()
    if not supabase: return False, "数据库未配置"
    res = supabase.table("users").select("*").eq("username", username).execute()
    if res.data: return False, "用户名已存在"
    try:
        supabase.table("users").insert({"username": username, "password": hash_password(password), "personas": {}}).execute()
        return True, "注册成功"
    except Exception as e: return False, f"注册失败: {str(e)}"

def login_user(username, password=None):
    supabase = init_supabase()
    if not supabase: return False, {}
    try:
        query = supabase.table("users").select("*").eq("username", username)
        if password: query = query.eq("password", hash_password(password))
        res = query.execute()
        if res.data: return True, res.data[0]
        return False, {}
    except: return False, {}

def update_user_personas(username, personas_dict):
    supabase = init_supabase()
    if supabase: supabase.table("users").update({"personas": personas_dict}).eq("username", username).execute()

# ==========================================
# 5. 数据存取模块
# ==========================================
def load_user_data(username):
    supabase = init_supabase()
    if not supabase: return {}
    try:
        response = supabase.table("chat_history").select("*").eq("username", username).execute()
        data_map = {}
        for row in response.data: data_map[row['id']] = row['data']
        return data_map
    except: return {}

def save_session_db(session_id, session_data, username):
    supabase = init_supabase()
    if supabase:
        try:
            supabase.table("chat_history").upsert({"id": session_id, "username": username, "data": session_data}).execute()
        except: pass

def delete_session_db(session_id):
    supabase = init_supabase()
    if supabase: supabase.table("chat_history").delete().eq("id", session_id).execute()

# ==========================================
# 6. API 调用 (升级为流式 & 多智能体)
# ==========================================
def get_settings():
    return {
        "api_key": st.secrets.get("api_key", ""),
        "base_url": st.secrets.get("base_url", DEFAULT_BASE_URL),
        "model_name": st.secrets.get("model_name", DEFAULT_MODEL)
    }

def call_ai_stream(messages, settings, temperature=0.7):
    """通用的流式调用函数"""
    client = OpenAI(api_key=settings["api_key"], base_url=settings["base_url"])
    try:
        stream = client.chat.completions.create(
            model=settings["model_name"], messages=messages, stream=True, temperature=temperature
        )
        return stream
    except Exception as e: return f"Error: {str(e)}"

def call_ai_blocking(prompt, system, settings):
    """非流式调用，用于后台处理（如大纲生成、批评）"""
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
# 7. 主程序逻辑
# ==========================================

# 初始化 Session
if "logged_in" not in st.session_state:
    st.session_state.logged_in = False
    st.session_state.current_user = None
    st.session_state.custom_personas = {}

# 自动登录
if not st.session_state.logged_in and "u" in st.query_params:
    auto_user = st.query_params["u"]
    supabase = init_supabase()
    if supabase:
        res = supabase.table("users").select("*").eq("username", auto_user).execute()
        if res.data:
            st.session_state.logged_in = True
            st.session_state.current_user = auto_user
            st.session_state.custom_personas = res.data[0].get("personas", {}) or {}

# 登录注册页
if not st.session_state.logged_in:
    st.title("🔐 灵感缪斯 Pro")
    t1, t2 = st.tabs(["登录", "注册"])
    with t1:
        with st.form("login"):
            u = st.text_input("用户名"); p = st.text_input("密码", type="password")
            if st.form_submit_button("登录"):
                s, d = login_user(u, p)
                if s:
                    st.session_state.logged_in = True
                    st.session_state.current_user = u
                    st.session_state.custom_personas = d.get("personas", {}) or {}
                    st.query_params["u"] = u
                    st.rerun()
                else: st.error("错误")
    with t2:
        with st.form("reg"):
            nu = st.text_input("新用户"); np = st.text_input("密码", type="password")
            if st.form_submit_button("注册"):
                s, m = register_user(nu, np)
                if s: st.success(m)
                else: st.error(m)
    st.stop()

# --- 登录后 ---
CURRENT_USER = st.session_state.current_user
SETTINGS = get_settings()

if "history" not in st.session_state:
    with st.spinner("同步云端数据..."):
        st.session_state.history = load_user_data(CURRENT_USER)
    for s in st.session_state.history.values():
        if "article_content" not in s: s["article_content"] = ""
        if "script_content" not in s: s["script_content"] = ""
        # 新增：大纲字段
        if "outline_content" not in s: s["outline_content"] = ""

if "current_session_id" not in st.session_state:
    if st.session_state.history:
        st.session_state.current_session_id = list(st.session_state.history.keys())[0]
    else:
        nid = str(uuid.uuid4())
        ndata = {"title": "新灵感会话", "messages": [], "article_content": "", "script_content": "", "outline_content": "", "created_at": datetime.now().isoformat()}
        st.session_state.history[nid] = ndata
        st.session_state.current_session_id = nid
        save_session_db(nid, ndata, CURRENT_USER)

# --- 侧边栏 ---
with st.sidebar:
    st.write(f"👤 **{CURRENT_USER}**")
    if st.button("退出"):
        st.session_state.logged_in = False
        st.session_state.history = {}
        st.query_params.clear()
        st.rerun()
    st.divider()

    st.header("🎭 人设")
    all_personas = {**DEFAULT_PERSONAS, **st.session_state.custom_personas}
    selected_p = st.selectbox("选择", list(all_personas.keys()), label_visibility="collapsed")
    active_prompt = all_personas[selected_p]
    
    with st.expander("⚙️ 管理人设"):
        en = st.text_input("名称", value=selected_p)
        ec = st.text_area("内容", value=active_prompt, height=100)
        if st.button("保存人设"):
            st.session_state.custom_personas[en] = ec
            update_user_personas(CURRENT_USER, st.session_state.custom_personas)
            st.rerun()

    st.divider()
    st.header("🗂️ 会话")
    if st.button("➕ 新建", use_container_width=True):
        nid = str(uuid.uuid4())
        nd = {"title": f"灵感-{datetime.now().strftime('%m-%d %H:%M')}", "messages": [], "article_content": "", "script_content": "", "outline_content": "", "created_at": datetime.now().isoformat()}
        st.session_state.history[nid] = nd
        st.session_state.current_session_id = nid
        save_session_db(nid, nd, CURRENT_USER)
        st.rerun()

    sorted_sids = sorted(list(st.session_state.history.keys()), key=lambda k: st.session_state.history[k]['created_at'], reverse=True)
    for sid in sorted_sids:
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
    
    if st.session_state.current_session_id:
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

t1, t2, t3 = st.tabs(["💬 对话", "📝 文章", "🎬 剧本(Pro)"])

# --- Tab 1: 对话 (支持流式) ---
with t1:
    for m in SESS["messages"]:
        with st.chat_message(m["role"]): st.markdown(m["content"])
    
    if p := st.chat_input():
        if not SETTINGS["api_key"]: st.error("请配置 Secrets")
        else:
            SESS["messages"].append({"role": "user", "content": p})
            save_session_db(st.session_state.current_session_id, SESS, CURRENT_USER)
            with st.chat_message("user"): st.markdown(p)
            
            # 上下文截断
            sys_msg = {"role": "system", "content": active_prompt}
            hist_msgs = SESS["messages"][-20:] # 只取最后20条
            
            with st.chat_message("assistant"):
                stream = call_ai_stream([sys_msg] + hist_msgs, SETTINGS)
                if isinstance(stream, str): st.error(stream)
                else:
                    # 使用 write_stream 实现流式打字效果
                    ans = st.write_stream(stream_parser(stream))
                    SESS["messages"].append({"role": "assistant", "content": ans})
                    save_session_db(st.session_state.current_session_id, SESS, CURRENT_USER)

# --- Tab 2: 文章 (支持流式) ---
with t2:
    if SESS["article_content"]:
        st.success("✅ 已存档")
        st.code(SESS["article_content"], language="markdown")
    
    if st.button("生成/重写文章"):
        if not SESS["messages"]: st.warning("无对话记录")
        else:
            ctx = "\n".join([f"{m['role']}: {m['content']}" for m in SESS["messages"]])
            prompt = f"基于以下对话写一篇文章：\n{ctx}"
            messages = [{"role": "system", "content": "你是专业编辑"}, {"role": "user", "content": prompt}]
            
            st.markdown("### 正在撰写...")
            stream = call_ai_stream(messages, SETTINGS)
            
            # 实时显示生成过程
            res_container = st.empty()
            full_res = ""
            for chunk in stream_parser(stream):
                full_res += chunk
                res_container.markdown(full_res + "▌")
            
            res_container.markdown(full_res) # 最终显示
            SESS["article_content"] = full_res
            save_session_db(st.session_state.current_session_id, SESS, CURRENT_USER)

# --- Tab 3: 剧本 Pro (大纲 + 多智能体 + 流式 + Word导出) ---
with t3:
    st.subheader("🎬 剧本创作 Pro")

    # 1. 模式选择
    col_opt1, col_opt2 = st.columns(2)
    with col_opt1:
        use_outline = st.toggle("使用大纲模式 (推荐长剧本)", value=False)
    with col_opt2:
        use_multi_agent = st.toggle("启用多智能体优化 (质量更高但稍慢)", value=False)

    # 2. 基础参数
    with st.form("base_script_form"):
        st.caption("基础设定")
        source = st.radio("来源", ["对话生成", "自定义"], horizontal=True)
        theme = st.text_input("主题") if source == "自定义" else ""
        chars = st.text_area("人物", height=60)
        scene = st.text_input("场景")
        plot = st.text_area("情节/冲突", height=60)
        extra = st.text_input("补充要求")
        
        # 按钮逻辑
        if use_outline:
            btn_txt = "第一步：生成大纲"
        else:
            btn_txt = "开始生成剧本 (流式)"
        
        submit_base = st.form_submit_button(btn_txt)

    # 逻辑处理区
    ctx_str = ""
    if SESS["messages"]: ctx_str = "\n".join([f"{m['role']}: {m['content']}" for m in SESS["messages"]])

    if submit_base:
        if use_outline:
            # === 生成大纲 ===
            with st.spinner("正在规划故事节拍..."):
                outline_prompt = f"""
                请基于以下信息生成剧本大纲（Beat Sheet）：
                背景: {ctx_str}
                主题: {theme}
                人物: {chars}
                情节: {plot}
                要求：列出故事的起承转合，包含5-8个关键情节点。
                """
                res = call_ai_blocking(outline_prompt, "你是剧本策划", SETTINGS)
                SESS["outline_content"] = res
                save_session_db(st.session_state.current_session_id, SESS, CURRENT_USER)
                st.rerun()
        else:
            # === 直接生成剧本 (跳过大纲) ===
            final_prompt = f"背景:{ctx_str}\n主题:{theme}\n人物:{chars}\n场景:{scene}\n情节:{plot}\n补充:{extra}"
            
            # 多智能体优化层
            if use_multi_agent:
                with st.status("多智能体协作中...", expanded=True) as status:
                    status.write("Agent A: 正在起草初稿...")
                    draft = call_ai_blocking(final_prompt, SCRIPT_STYLE_GUIDE, SETTINGS)
                    status.write("Agent B: 正在进行毒舌审稿...")
                    critique = call_ai_blocking(f"请批评这篇剧本的缺点：\n{draft}", "你是毒舌剧评人", SETTINGS)
                    status.write("Agent A: 正在根据意见重写...")
                    final_prompt = f"原剧本：\n{draft}\n\n修改意见：\n{critique}\n\n请重写剧本，保持标准格式。"
                    status.update(label="优化完成，开始输出", state="complete", expanded=False)

            # 流式输出
            st.markdown("### 剧本正文")
            stream = call_ai_stream([
                {"role": "system", "content": SCRIPT_STYLE_GUIDE},
                {"role": "user", "content": final_prompt}
            ], SETTINGS)
            
            res_box = st.empty()
            full_text = ""
            for chunk in stream_parser(stream):
                full_text += chunk
                res_box.markdown(full_text + "▌")
            res_box.markdown(full_text)
            SESS["script_content"] = full_text
            save_session_db(st.session_state.current_session_id, SESS, CURRENT_USER)

    # === 大纲修改区 (仅当有大纲时显示) ===
    if use_outline and SESS["outline_content"]:
        st.divider()
        st.subheader("第二步：确认大纲")
        new_outline = st.text_area("编辑大纲 (AI将基于此生成剧本)", value=SESS["outline_content"], height=200)
        
        if st.button("基于大纲生成剧本"):
            final_prompt_w_outline = f"请基于此大纲写剧本：\n{new_outline}\n\n其他要求：{extra}"
             # 同样支持多智能体
            if use_multi_agent:
                with st.status("多智能体优化中...", expanded=True) as status:
                    status.write("Agent A: 起草中...")
                    draft = call_ai_blocking(final_prompt_w_outline, SCRIPT_STYLE_GUIDE, SETTINGS)
                    status.write("Agent B: 审稿中...")
                    critique = call_ai_blocking(f"批评：\n{draft}", "你是剧评人", SETTINGS)
                    status.write("Agent A: 修正中...")
                    final_prompt_w_outline = f"原稿：\n{draft}\n意见：\n{critique}\n重写："
                    status.update(label="准备就绪", state="complete")

            st.markdown("### 剧本正文")
            stream = call_ai_stream([{"role": "system", "content": SCRIPT_STYLE_GUIDE}, {"role": "user", "content": final_prompt_w_outline}], SETTINGS)
            
            res_box = st.empty()
            full_text = ""
            for chunk in stream_parser(stream):
                full_text += chunk
                res_box.markdown(full_text + "▌")
            res_box.markdown(full_text)
            SESS["script_content"] = full_text
            save_session_db(st.session_state.current_session_id, SESS, CURRENT_USER)

    # === 结果展示与导出 ===
    if SESS["script_content"]:
        st.divider()
        st.success("剧本已生成")
        with st.expander("查看完整剧本", expanded=False):
            st.code(SESS["script_content"], language="markdown")
        
        # 导出 Word 功能
        docx_file = create_docx(SESS["script_content"])
        st.download_button(
            label="📥 导出 Word (.docx) - 行业格式",
            data=docx_file,
            file_name=f"{SESS['title']}.docx",
            mime="application/vnd.openxmlformats-officedocument.wordprocessingml.document"
        )