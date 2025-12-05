import streamlit as st
import json
import os
import uuid
import hashlib
import io
import re
from datetime import datetime
from openai import OpenAI
from supabase import create_client, Client
from docx import Document
from docx.shared import Pt, Inches
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.oxml.ns import qn

# ==========================================
# 1. 基础配置与样式优化
# ==========================================
st.set_page_config(
    page_title="灵感缪斯 Pro Max",
    page_icon="🎬",
    layout="wide",
    initial_sidebar_state="expanded"
)

# CSS 修复：手机适配、隐藏多余按钮、优化字体
hide_streamlit_style = """
<style>
    /* 隐藏 Deploy 按钮 */
    .stDeployButton {display:none;}
    /* 隐藏页脚 */
    footer {visibility: hidden;}
    /* 编剧字体优化 */
    code {font-family: 'Courier New', Courier, monospace !important; line-height: 1.2 !important;}
    
    /* 核心修复：防止手机底部遮挡，增加底部留白 */
    .block-container {
        padding-top: 1rem;
        padding-bottom: 6rem; 
    }
    
    /* 移动端适配 */
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

# 剧本生成规则 (紧凑版，适配 Word 导出)
SCRIPT_STYLE_GUIDE = """
你是一名好莱坞专业编剧。请严格按照以下【紧凑格式】、【内容要求】创作剧本，不要有多余空行。

格式要求：
1. 剧本标题：居中，书名号。
2. 人物列表：列出人物名、性别、年龄、简短特征。
3. 场景头 (SCENE HEADING)：使用 "第一幕" 或 "INT./EXT." + 时间/地点。
4. 动作描述 (ACTION)：用全角括号（）包裹，如（翻来覆去睡不着）。
5. 对话格式：
   - 角色名：(情绪/动作) 对白内容。
   - 例如：A：（低声）……在吗？
6. 禁止：对话中间不要有多余的空行。动作和对话之间紧凑排列。

内容要求：
1. 自然且真实的对话：贴近日常口语，避免过度修辞，避免翻译腔。
2. 对话推动剧情：台词具有目的性。
3. 情感层次：善于从潜台词中展示冲突，不要直白喊出来。

"""

DEFAULT_PERSONAS = {
    "默认-知心老友":"你是我无话不谈的创意搭档。请用自然、口语化、直率的语气和我对话。严禁使用括号描写动作，直接说话。**重要：请时刻跟随用户最新的话题，不要反复纠结于用户之前提到的旧话题**。",
    "模式-严厉导师":"你是一位在好莱坞拥有30年经验的严厉编剧导师。不要说客套话，不要盲目鼓励。你需要一针见血地指出用户灵感中的逻辑漏洞、陈词滥调和人物动机不合理之处。说话风格：犀利、专业、不留情面，提出的建议必须具有建设性。",
    "模式-苏格拉底":"你是一个只会提问的哲学家，通过提出层层递进的问题引导用户自己发现答案，或者发现自己思维中的盲区。",
}

@st.cache_resource
def init_supabase():
    if not SUPABASE_URL or not SUPABASE_KEY: return None
    return create_client(SUPABASE_URL, SUPABASE_KEY)

# ==========================================
# 3. 工具函数：Word 导出与流式解析
# ==========================================

def set_courier_font(run, size=12):
    """设置专业剧本字体 Courier New"""
    run.font.name = 'Courier New'
    run._element.rPr.rFonts.set(qn('w:eastAsia'), 'Courier New')
    run.font.size = Pt(size)

def create_docx(script_content):
    """生成行业标准格式的 Word 剧本"""
    doc = Document()
    style = doc.styles['Normal']
    style.font.name = 'Courier New'
    style.font.size = Pt(12)
    
    lines = script_content.split('\n')
    for line in lines:
        line = line.strip()
        if not line: continue
        
        p = doc.add_paragraph()
        run = p.add_run(line)
        set_courier_font(run)

        # 智能格式解析
        if line.startswith("《") and line.endswith("》"): # 标题
            p.alignment = WD_ALIGN_PARAGRAPH.CENTER
            run.bold = True
            run.font.size = Pt(16)
            p.paragraph_format.space_after = Pt(24)
        elif any(k in line for k in ["第一幕", "INT.", "EXT.", "内.", "外."]) or (len(line)<15 and "点" in line and "分" in line): # 场景头
            run.bold = True
            p.paragraph_format.space_before = Pt(18)
            p.paragraph_format.space_after = Pt(6)
            p.paragraph_format.keep_with_next = True
        elif line.startswith("（") and line.endswith("）"): # 动作
            p.paragraph_format.left_indent = Inches(0.0)
            p.paragraph_format.space_after = Pt(6)
        elif "：" in line or ":" in line: # 对白
            parts = re.split(r"[：:]", line, 1)
            if len(parts) == 2 and len(parts[0].strip()) < 15:
                p.clear()
                # 角色名居中
                p_role = doc.add_paragraph()
                p_role.alignment = WD_ALIGN_PARAGRAPH.CENTER
                r_role = p_role.add_run(parts[0].strip())
                set_courier_font(r_role); r_role.bold = True
                p_role.paragraph_format.space_before = Pt(12)
                p_role.paragraph_format.keep_with_next = True
                # 对白块居中
                p_dial = doc.add_paragraph()
                p_dial.paragraph_format.left_indent = Inches(1.5)
                p_dial.paragraph_format.right_indent = Inches(1.5)
                r_dial = p_dial.add_run(parts[1].strip())
                set_courier_font(r_dial)
        else:
            p.paragraph_format.space_after = Pt(6)

    buffer = io.BytesIO()
    doc.save(buffer)
    buffer.seek(0)
    return buffer

def stream_parser(stream):
    """流式输出解析器"""
    for chunk in stream:
        if chunk.choices[0].delta.content is not None:
            yield chunk.choices[0].delta.content

# ==========================================
# 4. 身份验证与用户管理
# ==========================================
def hash_password(password):
    return hashlib.sha256(password.encode()).hexdigest()

def register_user(username, password):
    sb = init_supabase()
    if not sb: return False, "DB未配置"
    if sb.table("users").select("*").eq("username", username).execute().data:
        return False, "用户已存在"
    try:
        sb.table("users").insert({"username": username, "password": hash_password(password), "personas": {}}).execute()
        return True, "注册成功"
    except Exception as e: return False, str(e)

def login_user(username, password=None):
    sb = init_supabase()
    if not sb: return False, {}
    try:
        q = sb.table("users").select("*").eq("username", username)
        if password: q = q.eq("password", hash_password(password))
        res = q.execute()
        return (True, res.data[0]) if res.data else (False, {})
    except: return False, {}

def update_user_personas(u, p):
    sb = init_supabase()
    if sb: sb.table("users").update({"personas": p}).eq("username", u).execute()

# ==========================================
# 5. 数据存取模块
# ==========================================
def load_user_data(u):
    sb = init_supabase()
    if not sb: return {}
    try:
        res = sb.table("chat_history").select("*").eq("username", u).execute()
        return {r['id']: r['data'] for r in res.data}
    except: return {}

def save_session_db(sid, data, u):
    sb = init_supabase()
    if sb:
        try: sb.table("chat_history").upsert({"id": sid, "username": u, "data": data}).execute()
        except: pass

def delete_session_db(sid):
    sb = init_supabase()
    if sb: sb.table("chat_history").delete().eq("id", sid).execute()

# ==========================================
# 6. API 调用 (流式 + 阻塞)
# ==========================================
def get_settings():
    return {
        "api_key": st.secrets.get("api_key", ""),
        "base_url": st.secrets.get("base_url", DEFAULT_BASE_URL),
        "model_name": st.secrets.get("model_name", DEFAULT_MODEL)
    }

def call_ai_stream(messages, settings, temperature=0.7):
    client = OpenAI(api_key=settings["api_key"], base_url=settings["base_url"])
    # 修复话题死循环：如果消息太长，只取最近20条 (System Prompt除外)
    if len(messages) > 20:
        messages = [messages[0]] + messages[-20:]
    try:
        return client.chat.completions.create(model=settings["model_name"], messages=messages, stream=True, temperature=temperature)
    except Exception as e: return f"Error: {e}"

def call_ai_blocking(prompt, system, settings):
    client = OpenAI(api_key=settings["api_key"], base_url=settings["base_url"])
    try:
        res = client.chat.completions.create(model=settings["model_name"], messages=[{"role": "system", "content": system}, {"role": "user", "content": prompt}], temperature=1.0)
        return res.choices[0].message.content
    except Exception as e: return f"Error: {e}"

# ==========================================
# 7. 主程序逻辑
# ==========================================

# Session 初始化
if "logged_in" not in st.session_state:
    st.session_state.logged_in = False
    st.session_state.current_user = None
    st.session_state.custom_personas = {}

# 自动登录逻辑 (URL参数)
if not st.session_state.logged_in and "u" in st.query_params:
    auto_user = st.query_params["u"]
    sb = init_supabase()
    if sb:
        res = sb.table("users").select("*").eq("username", auto_user).execute()
        if res.data:
            st.session_state.logged_in = True
            st.session_state.current_user = auto_user
            st.session_state.custom_personas = res.data[0].get("personas", {}) or {}
            st.toast(f"欢迎回来，{auto_user}")

# 登录注册页
if not st.session_state.logged_in:
    st.title("🔐 灵感缪斯 - 登录")
    t1, t2 = st.tabs(["登录", "注册"])
    with t1:
        with st.form("l"):
            u = st.text_input("用户名"); p = st.text_input("密码", type="password")
            if st.form_submit_button("登录"):
                s, d = login_user(u, p)
                if s:
                    st.session_state.logged_in=True; st.session_state.current_user=u; st.session_state.custom_personas=d.get("personas", {}) or {}
                    st.query_params["u"]=u; st.rerun()
                else: st.error("失败")
    with t2:
        with st.form("r"):
            nu = st.text_input("新用户"); np = st.text_input("密码", type="password")
            if st.form_submit_button("注册"):
                s, m = register_user(nu, np)
                if s: st.success(m)
                else: st.error(m)
    st.stop()

CURRENT_USER = st.session_state.current_user
SETTINGS = get_settings()

# 加载数据
if "history" not in st.session_state:
    with st.spinner("同步云端数据..."): st.session_state.history = load_user_data(CURRENT_USER)
    for s in st.session_state.history.values():
        for k in ["article_content", "script_content", "outline_content"]: 
            if k not in s: s[k] = ""

if "current_session_id" not in st.session_state:
    if st.session_state.history: st.session_state.current_session_id = list(st.session_state.history.keys())[0]
    else:
        nid = str(uuid.uuid4())
        nd = {"title": "新会话", "messages": [], "article_content": "", "script_content": "", "outline_content": "", "created_at": datetime.now().isoformat()}
        st.session_state.history[nid]=nd; st.session_state.current_session_id=nid; save_session_db(nid, nd, CURRENT_USER)

# --- 侧边栏 ---
with st.sidebar:
    st.write(f"👤 **{CURRENT_USER}**")
    if st.button("退出登录"):
        st.session_state.logged_in=False; st.session_state.history={}; st.query_params.clear(); st.rerun()
    st.divider()

    # 人设管理 (包含自定义存数据库)
    st.header("🎭 人设管理")
    all_p = {**DEFAULT_PERSONAS, **st.session_state.custom_personas}
    sel_p = st.selectbox("当前人设", list(all_p.keys()))
    act_p = all_p[sel_p]
    with st.expander("⚙️ 修改/新建人设"):
        en = st.text_input("名称", value=sel_p); ec = st.text_area("内容", value=act_p, height=100)
        if st.button("保存人设"):
            st.session_state.custom_personas[en]=ec; update_user_personas(CURRENT_USER, st.session_state.custom_personas); st.rerun()
        if st.button("删除人设"):
            if en in st.session_state.custom_personas: del st.session_state.custom_personas[en]; update_user_personas(CURRENT_USER, st.session_state.custom_personas); st.rerun()

    st.divider()
    st.header("🗂️ 会话")
    if st.button("➕ 新建"):
        nid = str(uuid.uuid4()); nd = {"title": f"灵感-{datetime.now().strftime('%H:%M')}", "messages": [], "article_content": "", "script_content": "", "outline_content": "", "created_at": datetime.now().isoformat()}
        st.session_state.history[nid]=nd; st.session_state.current_session_id=nid; save_session_db(nid, nd, CURRENT_USER); st.rerun()

    for sid in sorted(list(st.session_state.history.keys()), key=lambda k: st.session_state.history[k]['created_at'], reverse=True):
        sdata = st.session_state.history[sid]
        c1, c2 = st.columns([0.8, 0.2])
        with c1: 
            if st.button(f"{'🔵' if sid==st.session_state.current_session_id else '📄'} {sdata['title']}", key=f"b_{sid}", use_container_width=True): st.session_state.current_session_id=sid; st.rerun()
        with c2:
            if st.button("x", key=f"d_{sid}"): del st.session_state.history[sid]; delete_session_db(sid); 
                if sid==st.session_state.current_session_id: st.session_state.current_session_id=None
                st.rerun()
    if st.session_state.current_session_id:
        curr = st.session_state.history[st.session_state.current_session_id]
        nt = st.text_input("重命名", value=curr['title'])
        if nt != curr['title']: curr['title']=nt; save_session_db(st.session_state.current_session_id, curr, CURRENT_USER); st.rerun()

if not st.session_state.current_session_id: st.stop()
SESS = st.session_state.history[st.session_state.current_session_id]
st.title(SESS['title'])
t1, t2, t3 = st.tabs(["💬 对话", "📝 文章", "🎬 剧本Pro"])

# --- Tab 1: 对话 (流式 + 修复死循环) ---
with t1:
    for m in SESS["messages"]: 
        with st.chat_message(m["role"]): st.markdown(m["content"])
    if p := st.chat_input():
        if not SETTINGS["api_key"]: st.error("Secrets未配")
        else:
            SESS["messages"].append({"role": "user", "content": p}); save_session_db(st.session_state.current_session_id, SESS, CURRENT_USER)
            with st.chat_message("user"): st.markdown(p)
            with st.chat_message("assistant"):
                strm = call_ai_stream([{"role":"system","content":act_p}] + SESS["messages"], SETTINGS)
                if isinstance(strm, str): st.error(strm)
                else:
                    ans = st.write_stream(stream_parser(strm))
                    SESS["messages"].append({"role": "assistant", "content": ans}); save_session_db(st.session_state.current_session_id, SESS, CURRENT_USER)

# --- Tab 2: 文章 (流式 + 进度条 + 一键复制) ---
with t2:
    if SESS["article_content"]: 
        st.success("✅ 已存档"); st.code(SESS["article_content"], language="markdown")
    if st.button("生成/重写文章"):
        if not SESS["messages"]: st.warning("空")
        else:
            with st.status("正在构思文章...", expanded=True) as status:
                ctx = "\n".join([f"{m['role']}: {m['content']}" for m in SESS["messages"]])
                status.write("开始撰写...")
                strm = call_ai_stream([{"role": "system", "content": "你是编辑"}, {"role": "user", "content": f"整理文章:\n{ctx}"}], SETTINGS)
                bx = st.empty(); ft = ""
                for c in stream_parser(strm): ft+=c; bx.markdown(ft+"▌")
                bx.markdown(ft); SESS["article_content"]=ft; save_session_db(st.session_state.current_session_id, SESS, CURRENT_USER)
                status.update(label="完成", state="complete", expanded=False)

# --- Tab 3: 剧本 Pro (大纲 + 多智能体 + 流式 + Word + 局部精修 + 参数补全) ---
with t3:
    st.subheader("🎬 剧本创作 Pro")
    c_opt1, c_opt2 = st.columns(2)
    with c_opt1: use_outline = st.toggle("大纲模式", value=False)
    with c_opt2: use_multi_agent = st.toggle("多智能体优化", value=False)

    with st.form("base"):
        src = st.radio("来源", ["对话生成", "自定义"], horizontal=True)
        thm = st.text_input("主题") if src=="自定义" else ""
        chars = st.text_area("人物", height=60)
        scene = st.text_input("场景")
        plot = st.text_area("情节", height=60)
        extra = st.text_input("补充")
        sub_base = st.form_submit_button("生成大纲" if use_outline else "生成剧本")

    ctx_str = "\n".join([f"{m['role']}: {m['content']}" for m in SESS["messages"]]) if SESS["messages"] else ""

    if sub_base:
        if use_outline:
            with st.status("生成大纲..."):
                res = call_ai_blocking(f"背景:{ctx_str}\n主题:{thm}\n人物:{chars}\n情节:{plot}\n要求:生成Beat Sheet", "你是策划", SETTINGS)
                SESS["outline_content"] = res; save_session_db(st.session_state.current_session_id, SESS, CURRENT_USER); st.rerun()
        else:
            final_p = f"背景:{ctx_str}\n主题:{thm}\n人物:{chars}\n场景:{scene}\n情节:{plot}\n补充:{extra}"
            if use_multi_agent:
                with st.status("多智能体协作...") as s:
                    s.write("起草..."); d = call_ai_blocking(final_p, SCRIPT_STYLE_GUIDE, SETTINGS)
                    s.write("审稿..."); c = call_ai_blocking(f"批评:\n{d}", "毒舌审稿", SETTINGS)
                    s.write("修正..."); final_p = f"原稿:\n{d}\n意见:\n{c}\n重写:"
                    s.update(label="完成", state="complete")
            st.markdown("### 剧本")
            strm = call_ai_stream([{"role": "system", "content": SCRIPT_STYLE_GUIDE}, {"role": "user", "content": final_p}], SETTINGS)
            bx = st.empty(); ft = ""
            for c in stream_parser(strm): ft+=c; bx.markdown(ft+"▌")
            bx.markdown(ft); SESS["script_content"]=ft; save_session_db(st.session_state.current_session_id, SESS, CURRENT_USER)

    if use_outline and SESS["outline_content"]:
        st.divider(); st.subheader("确认大纲")
        new_out = st.text_area("编辑大纲", value=SESS["outline_content"], height=200)
        if st.button("生成剧本"):
            fp = f"大纲:\n{new_out}\n要求:{extra}"
            if use_multi_agent:
                with st.status("优化中...") as s:
                    s.write("起草..."); d = call_ai_blocking(fp, SCRIPT_STYLE_GUIDE, SETTINGS)
                    s.write("审稿..."); c = call_ai_blocking(f"批评:\n{d}", "审稿", SETTINGS)
                    s.write("修正..."); fp = f"原稿:\n{d}\n意见:\n{c}\n重写:"
                    s.update(label="完成", state="complete")
            st.markdown("### 剧本")
            strm = call_ai_stream([{"role": "system", "content": SCRIPT_STYLE_GUIDE}, {"role": "user", "content": fp}], SETTINGS)
            bx = st.empty(); ft = ""
            for c in stream_parser(strm): ft+=c; bx.markdown(ft+"▌")
            bx.markdown(ft); SESS["script_content"]=ft; save_session_db(st.session_state.current_session_id, SESS, CURRENT_USER)

    if SESS["script_content"]:
        st.divider(); st.success("完成")
        st.code(SESS["script_content"], language="markdown") # 一键复制
        docx = create_docx(SESS["script_content"])
        st.download_button("📥 导出 Word (.docx)", data=docx, file_name=f"{SESS['title']}.docx", mime="application/vnd.openxmlformats-officedocument.wordprocessingml.document")

        # --- 功能6：局部精修 ---
        st.divider()
        st.subheader("🛠️ 局部精修")
        st.info("复制上方剧本片段进行修改")
        with st.form("refine"):
            target = st.text_area("粘贴片段", height=100)
            instr = st.text_input("修改意见", placeholder="例如：换个表达方式")
            if st.form_submit_button("修改"):
                with st.spinner("修改中..."):
                    p_refine = f"原片段:\n{target}\n意见:\n{instr}\n请仅输出修改后的片段。"
                    res_refine = call_ai_blocking(p_refine, f"剧本助手。背景:\n{SESS['script_content'][:1000]}", SETTINGS)
                    st.markdown("### 结果"); st.code(res_refine, language="markdown")