# -*- coding: utf-8 -*-
"""
welearn 一体版 - WELearn 自动刷课 / 刷时长工具
适配 2026 年新 SSO 登录系统

v1.2.0 更新：
  1. 修复“提交到 WE Learn 后分数一律为 1 分”：
     - 恢复 CMI 数据必须以 [INTERACTIONINFO] 结尾（少了它服务器解析不到 score，
       只把完成度/正确率记下来，分数留在默认值 1）；
     - 恢复上游“方式1：setscoinfo → 方式2：savescoinfo160928 兜底”的两段式提交；
     - 不再用写死的 crate=100 覆盖用户设置的正确率。
  2. 输入格式有误时不再退出程序：
     - 全程不退登（session / cookies 一直保留），错误输入只提示后原地重问；
     - 任意步骤输入 b（部分步骤输入 0）即可退回上一步重新选择。
"""
import base64
import json
import os
import random
import re
import sys
import time
import traceback
from threading import Thread


def _enable_utf8_output():
    """把标准输出/错误强制成 UTF-8，避免非 UTF-8 代码页（cp1252/cp437）
    或输出被重定向（CI、管道）时打印中文直接抛 UnicodeEncodeError。"""
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding='utf-8', errors='replace')
        except Exception:
            pass


_enable_utf8_output()

try:
    from requests import Session
except ImportError:  # pragma: no cover - 环境缺依赖时给出可操作提示
    print('缺少依赖 requests，请先执行：pip install requests')
    raise SystemExit(1)

VERSION = 'v1.2.3'
BASE = 'https://welearn.sflep.com'
AJAX_SCO = BASE + '/Ajax/SCO.aspx'
STUDY_PAGE = BASE + '/student/StudyCourse.aspx'
COURSE_LIST_URL = BASE + '/ajax/authCourse.aspx?action=gmc'
STUDY_STAT_URL = BASE + '/ajax/StudyStat.aspx'
UA = ('Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 '
      '(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36')

# 调试模式：python welearn_decompiled.py --debug，会打印每次提交的原始响应
DEBUG = '--debug' in sys.argv

# 输入 b / B / back 表示“退回上一步”
BACK_WORDS = ('b', 'B', 'back', 'BACK')

MAX_SECONDS = 86400      # 单个练习的最大刷取时长（秒）
MAX_CRATE = 100          # 正确率上限

session = Session()
session.headers.update({'User-Agent': UA})

# 全局状态（登录后由 load_units 填充）
uid = None
cid = None
classid = None

# 登录状态：回到主菜单后仍然有效，无需重复登录
logged_in = False
login_name = ''

way1Succeed = []
way2Succeed = []
way1Failed = []
way2Failed = []


class BackStep(Exception):
    """用户选择“退回上一步”。"""


class EofReached(Exception):
    """输入流结束（管道 / Ctrl+Z），按退出处理，避免空转。"""


class FetchError(Exception):
    """网络 / 接口异常，需要退回上一步重来。"""


# ============================================================
# 通用小工具
# ============================================================
def clear_screen():
    if os.name == 'nt':
        try:
            os.system('cls')
        except Exception:
            pass


def ask(prompt):
    """读取一行输入；输入流结束（EOF）时抛 EofReached，由 main 统一收尾。"""
    try:
        return input(prompt).strip()
    except EOFError:
        raise EofReached


def is_back(text):
    return text in BACK_WORDS


def new_session():
    """每个任务用独立 session，避免状态互相污染（cookies 从主 session 复制）。"""
    s = Session()
    s.headers.update({'User-Agent': UA})
    s.cookies.update(session.cookies)
    return s


LOG_FILE = None


def debug_log(title, body):
    """--debug 时把请求/响应追加写入 .debug/session-*.log，方便排查提交失败。"""
    global LOG_FILE
    if not DEBUG:
        return
    if LOG_FILE is None:
        try:
            os.makedirs('.debug', exist_ok=True)
            LOG_FILE = os.path.join('.debug', time.strftime('session-%Y%m%d-%H%M%S.log'))
            print(f'[debug] 日志文件: {LOG_FILE}')
        except Exception:
            LOG_FILE = ''
    if not LOG_FILE:
        return
    try:
        with open(LOG_FILE, 'a', encoding='utf-8') as fh:
            fh.write(f'[{time.strftime("%H:%M:%S")}] {title}\n{body}\n{"-" * 70}\n')
    except Exception:
        pass


def short(resp, limit=200):
    """把响应压成一行短文本，便于在控制台/日志里查看。"""
    text = getattr(resp, 'text', '') or ''
    text = ' '.join(text.split())
    return text[:limit] if text else '<空响应>'


def is_ok(resp):
    """服务器返回 ret==0 视为成功；兼容 {"ret":0} / {"ret": 0} / {"ret":"0"}。"""
    try:
        payload = resp.json()
    except Exception:
        payload = None
    if isinstance(payload, dict) and 'ret' in payload:
        try:
            return int(payload['ret']) == 0
        except (TypeError, ValueError):
            return False
    return '"ret":0' in (resp.text or '')


def post_sco(conn, url, data, referer):
    """提交 SCO 请求，并记录请求/响应（--debug）。"""
    resp = conn.post(url, data=data, headers={'Referer': referer}, timeout=20)
    debug_log(f'POST {url} action={data.get("action")}',
              f'form={data}\nstatus={getattr(resp, "status_code", "?")}\nresp={short(resp, 800)}')
    return resp


# ============================================================
# 输入解析（纯函数，方便单元测试）
# ============================================================
def parse_seconds_spec(text):
    """解析时长设置。

    返回 (下限秒, 上限秒)。格式非法或越界时抛 ValueError。
        30     -> (30, 30)
        10,30  -> (10, 30)
    """
    text = text.strip().replace('，', ',').replace(' ', '')
    if not text:
        raise ValueError('不能为空')
    parts = text.split(',')
    if len(parts) > 2 or any(p == '' for p in parts):
        raise ValueError('只能是一个数字或“下限,上限”')
    if not all(p.isdigit() for p in parts):
        raise ValueError('只能输入非负整数，多个值用英文逗号隔开')
    values = [int(p) for p in parts]
    low, high = (values[0], values[0]) if len(values) == 1 else (min(values), max(values))
    if low <= 0:
        raise ValueError('时长必须大于 0 秒')
    if high > MAX_SECONDS:
        raise ValueError(f'时长不能超过 {MAX_SECONDS} 秒')
    return low, high


def parse_crate_spec(text):
    """解析正确率设置。

    返回 (下限, 上限)，取值 0~100。格式非法或越界时抛 ValueError。
        100    -> (100, 100)
        70,100 -> (70, 100)
    """
    text = text.strip().replace('，', ',').replace(' ', '')
    if not text:
        raise ValueError('不能为空')
    parts = text.split(',')
    if len(parts) > 2 or any(p == '' for p in parts):
        raise ValueError('只能是一个数字或“下限,上限”')
    if not all(p.isdigit() for p in parts):
        raise ValueError('只能输入 0~100 的整数，多个值用英文逗号隔开')
    values = [int(p) for p in parts]
    if any(v > MAX_CRATE for v in values):
        raise ValueError(f'正确率不能超过 {MAX_CRATE}')
    low, high = (values[0], values[0]) if len(values) == 1 else (min(values), max(values))
    return low, high


def parse_index_list(text, upper):
    """解析“1,3,5”这种序号列表，返回升序去重后的 1-based 列表。非法抛 ValueError。"""
    text = text.strip().replace('，', ',').replace(' ', '')
    if not text:
        raise ValueError('不能为空')
    parts = text.split(',')
    if any(p == '' or not p.isdigit() for p in parts):
        raise ValueError('只能输入正整数，多个数字用英文逗号隔开')
    indexes = [int(p) for p in parts]
    out_of_range = [i for i in indexes if i < 1 or i > upper]
    if out_of_range:
        raise ValueError(f'序号必须在 1~{upper} 之间')
    return sorted(set(indexes))


def parse_cookie_header(text):
    """解析浏览器复制出来的 Cookie 字符串，返回 dict。非法抛 ValueError。"""
    cookies = {}
    for item in text.split(';'):
        item = item.strip()
        if not item:
            continue
        if '=' not in item:
            raise ValueError(f'“{item}”不是 key=value 形式')
        key, value = item.split('=', 1)
        key, value = key.strip(), value.strip()
        if not key:
            raise ValueError('Cookie 名不能为空')
        cookies[key] = value
    if not cookies:
        raise ValueError('没有解析出任何 Cookie')
    return cookies


# ============================================================
# 密码加密 (复刻自 SSO 前端 generateCipherText)
# ============================================================
def generate_cipher_text(password):
    """模拟前端的 generateCipherText 函数"""
    T0 = int(time.time() * 1000)  # Date.now()
    P = password.encode('utf-8')
    V = (T0 >> 16) & 0xFF
    for byte in P:
        V ^= byte
    remainder = V % 100
    T1 = (T0 // 100) * 100 + remainder
    P1 = ''.join(f'{b:02x}' for b in P)
    S = f'{T1}*{P1}'
    E = base64.b64encode(S.encode('utf-8')).decode('utf-8')
    return E, T1


# ============================================================
# 登录 SSO (新 OIDC 系统)
# ============================================================
def sso_login(username, password):
    """通过 SSO 登录，成功返回 cookies，失败返回 None（不抛异常、不退出程序）。"""
    from urllib.parse import unquote, parse_qs, urlparse

    sso = Session()
    sso.headers.update({'User-Agent': UA})

    # Step 1: 获取 OIDC 授权 URL
    prelogin_url = (BASE + '/user/prelogin.aspx?loginret='
                    'http%3a%2f%2fwelearn.sflep.com%2fuser%2floginredirect.aspx')
    resp = sso.get(prelogin_url, allow_redirects=False, timeout=20)
    if 'Location' not in resp.headers:
        print('获取登录地址失败，可能是网络问题！')
        return None

    authorize_url = resp.headers['Location']

    # Step 2: 访问授权 URL，跟随跳转到 transfer.html
    resp2 = sso.get(authorize_url, allow_redirects=True, timeout=20)

    # 从 transfer.html 的 URL 中提取 returnUrl 作为 rturl
    parsed = urlparse(resp2.url)
    url_params = parse_qs(parsed.query)
    rturl = unquote(url_params.get('returnUrl', [''])[0])
    if not rturl:
        print('获取登录参数失败，可能是登录系统改版！')
        return None

    # Step 3: 加密密码并提交登录
    pwd_enc, ts = generate_cipher_text(password)
    login_data = {
        'account': username,
        'pwd': pwd_enc,
        'ts': ts,
        'rturl': rturl,
    }

    login_headers = {
        'Content-Type': 'application/x-www-form-urlencoded',
        'X-Requested-With': 'XMLHttpRequest',
        'Origin': 'https://sso.sflep.com',
        'Referer': 'https://sso.sflep.com/idsvr/login.html',
    }

    login_resp = sso.post(
        'https://sso.sflep.com/idsvr/account/login',
        data=login_data,
        headers=login_headers,
        timeout=20,
    )

    try:
        result = login_resp.json()
    except Exception:
        print(f'登录响应异常: {login_resp.text[:200]}')
        return None

    if result.get('code') != 0:
        msg = result.get('msg', result.get('message', '未知错误'))
        print(f'登录失败!! {msg}')

        # 检查是否需要验证码
        extra = result.get('extraCheck', {})
        if extra.get('vcToken'):
            print('需要短信验证码验证')
        if 'captcha' in str(result).lower() or '验证码' in str(result):
            print('需要图片验证码（连续失败次数过多）；也可以改用 Cookie 登录')
        return None

    redirect_url = result.get('data', '')
    if redirect_url:
        # 返回的是相对路径，需要添加基础 URL
        if redirect_url.startswith('/'):
            redirect_url = 'https://sso.sflep.com/idsvr' + redirect_url
        # 跟随 OIDC 回调完成认证
        sso.get(redirect_url, allow_redirects=True, timeout=20)

    return sso.cookies


# ============================================================
# 登录流程（不停退出，失败只重问）
# ============================================================
def login_flow():
    """完成登录。成功返回；用户按 b 抛 BackStep。整个过程不会退出程序。"""
    while True:
        print('请选择登录 Welearn 的方式:')
        print('   1. 账号密码登录')
        print('   2. Cookie 值登录')
        print()
        choice = ask('请输入数字 1 或 2（输入 b 返回上一步）: ')

        if is_back(choice):
            raise BackStep
        if choice == '1':
            if login_by_password():
                return
            continue          # 登录失败：留在登录页重新选择，登录状态/已经输入的账号都不丢
        if choice == '2':
            if login_by_cookie():
                return
            continue
        print('输入无效！只能输入 1 或 2。\n')


def login_by_password():
    """账号密码登录；输入非法或登录失败返回 False，不退出程序。"""
    global logged_in, login_name

    username = ask('请输入账号（输入 b 返回上一步）: ')
    if is_back(username):
        return False
    if not username:
        print('账号不能为空！\n')
        return False

    password = ask('请输入密码: ')
    if not password:
        print('密码不能为空！\n')
        return False

    print('登录中...')
    try:
        cookies = sso_login(username, password)
    except Exception as exc:
        print(f'登录请求异常: {exc}\n')
        return False

    if not cookies:
        print('登录失败，请检查账号密码后重新输入。\n')
        return False

    session.cookies.update(cookies)
    logged_in = True
    login_name = username
    print('登录成功!!\n')
    return True


def login_by_cookie():
    """Cookie 登录；格式错误只提示，不退出程序，且不会清空已有登录状态。"""
    global logged_in, login_name

    raw = ask('请粘贴 Cookie（输入 b 返回上一步）: ')
    if is_back(raw):
        return False

    try:
        cookies = parse_cookie_header(raw)
    except ValueError as exc:
        print(f'Cookie 格式错误：{exc}')
        print('正确格式示例：ASP.NET_SessionId=xxxx; .ASPXAUTH=yyyy\n')
        return False

    session.cookies.update(cookies)
    print(f'Cookie 解析成功，共 {len(cookies)} 项!!\n')

    # 顺手验证一下 Cookie 是否可用，避免后面选课时报错
    try:
        courses = load_courses()
    except FetchError as exc:
        print(f'Cookie 校验失败：{exc}\n')
        return False
    if not courses:
        print('Cookie 校验失败：没有取到课程列表（Cookie 可能已过期）。\n')
        return False

    print(f'Cookie 有效，共 {len(courses)} 门课程。\n')
    logged_in = True
    login_name = 'Cookie 登录'
    return True


# ============================================================
# 接口封装（带重试）
# ============================================================
def request_with_retry(method, url, retries=3, **kwargs):
    """带重试的请求；三次都失败抛 FetchError。"""
    kwargs.setdefault('timeout', 20)
    last_error = None
    for attempt in range(1, retries + 1):
        try:
            response = getattr(session, method)(url, **kwargs)
            if response.status_code >= 500:
                raise RuntimeError(f'HTTP {response.status_code}')
            return response
        except Exception as exc:  # 网络类异常
            last_error = exc
            if attempt < retries:
                print(f'网络异常，正在重试({attempt}/{retries})...')
                time.sleep(2)
    raise FetchError(str(last_error))


def load_courses():
    """取课程列表；接口异常抛 FetchError。"""
    response = request_with_retry(
        'get', COURSE_LIST_URL,
        headers={'Referer': BASE + '/student/index.aspx'})
    try:
        data = response.json()
    except Exception:
        raise FetchError('课程列表返回的不是合法 JSON（可能是登录已失效）')
    courses = data.get('clist')
    if courses is None:
        raise FetchError('课程列表格式异常（可能是登录已失效）')
    if not isinstance(courses, list):
        raise FetchError('课程列表格式异常')
    return courses


def load_units(course):
    """取课程详情 + 单元列表，并刷新全局 cid / uid / classid。失败抛 FetchError。"""
    global uid, cid, classid

    cid_value = course['cid']
    cid = cid_value
    course_url = f'{BASE}/student/course_info.aspx?cid={cid_value}'
    response = request_with_retry('get', course_url)
    text = response.text

    uid_match = re.search(r'"uid":(.*?),', text)
    classid_match = re.search(r'"classid":"(.*?)"', text)
    if not (uid_match and classid_match):
        raise FetchError('课程信息解析失败（可能是登录已失效）')
    uid = uid_match.group(1).strip().strip('"')
    classid = classid_match.group(1)

    response = request_with_retry(
        'post', STUDY_STAT_URL,
        data={'action': 'courseunits', 'cid': cid_value, 'uid': uid},
        headers={'Referer': course_url})
    try:
        info = response.json().get('info')
    except Exception:
        raise FetchError('单元列表返回的不是合法 JSON')
    if not isinstance(info, list) or not info:
        raise FetchError('没有取到单元列表')
    return info


def fetch_sco_leaves(cid_value, unit_index):
    """取某单元下的小节列表；解析失败返回空列表。"""
    url = (f'{STUDY_STAT_URL}?action=scoLeaves&cid={cid_value}'
           f'&uid={uid}&unitidx={unit_index}&classid={classid}')
    response = request_with_retry(
        'get', url,
        headers={'Referer': f'{BASE}/student/course_info.aspx?cid={cid_value}'})
    debug_log(f'GET {url}', f'status={response.status_code}\nresp={short(response, 1500)}')
    try:
        items = response.json().get('info')
    except Exception:
        return []
    return items if isinstance(items, list) else []


# ============================================================
# 菜单交互
# ============================================================
def choose_mode():
    """选择模式；返回 '1' / '2'，选 3 返回 'logout'，退出返回 None。"""
    while True:
        if logged_in:
            print(f'当前登录：{login_name}（已保持登录，无需重新输入）')
        print('请选择 Welearn 刷课模式:')
        print('   1. Welearn 课程刷取（完成度 + 正确率）')
        print('   2. Welearn 时长刷取')
        if logged_in:
            print('   3. 重新登录 / 切换账号')
        print('   0. 退出程序')
        print()
        choice = ask('请输入数字' + (' 1、2、3 或 0: ' if logged_in else ' 1、2 或 0: '))
        if choice in ('0', 'q', 'Q', 'quit', 'exit'):
            return None
        if choice in ('1', '2'):
            return choice
        if choice == '3' and logged_in:
            return 'logout'
        print('输入无效，请重新选择！\n')


def do_logout():
    """退出登录：清空登录状态与 Cookie，下次运行需要重新登录。"""
    global logged_in, login_name
    logged_in = False
    login_name = ''
    session.cookies.clear()
    print('已退出登录，下次运行需要重新登录。\n')


def select_course(mode):
    """列出课程并让用户选择；输入 b 抛 BackStep。"""
    global logged_in
    try:
        courses = load_courses()
    except FetchError as exc:
        print(f'获取课程列表失败：{exc}')
        raise BackStep

    if not courses:
        # 空列表通常意味着 Cookie 失效，标记为未登录，下次回到主菜单会要求重新登录
        logged_in = False
        print('没有取到任何课程，可能是登录已失效或账号下没有课程。')
        raise BackStep

    print('我的课程：\n')
    for i, course in enumerate(courses, start=1):
        percent = course.get('per', 0)
        if mode == '2':
            print(f'[id:{i:>2d}]  完成度 {percent:>3}%  {course["name"]}')
        else:
            print(f'[NO.{i:>2d}]  完成度 {percent:>3}%  {course["name"]}')

    while True:
        if mode == '2':
            raw = ask('\n请输入需要刷时长的课程 id（输入 b 返回上一步）: ')
        else:
            raw = ask('\n请输入需要完成的课程编号（输入 b 返回上一步）: ')
        if is_back(raw):
            raise BackStep
        if not raw.isdigit():
            print(f'输入无效，只能输入 1~{len(courses)} 的数字！')
            continue
        index = int(raw)
        if not 1 <= index <= len(courses):
            print(f'输入无效，只能输入 1~{len(courses)} 的数字！')
            continue
        return courses[index - 1]


def select_units(mode, units):
    """列出单元并让用户选择；返回 0-based 的单元索引列表。输入 b 抛 BackStep。"""
    print()
    if mode == '2':
        print('[id: 0]  按顺序刷全部单元学习时长')
    else:
        print('[NO. 0]  按顺序完成全部单元课程')

    for i, unit in enumerate(units, start=1):
        visible = str(unit.get('visible', '')).lower() == 'true'
        tag = '[已开放]' if visible else '![未开放]!'
        if mode == '2':
            print(f'[id:{i:>2d}]  {tag}  {unit.get("unitname", "单元%d" % i)}')
        else:
            print(f'[NO.{i:>2d}]  {tag}  {unit.get("unitname", "单元%d" % i)}')

    while True:
        raw = ask('\n请选择单元（输入 0 为全部单元，多选用英文逗号隔开如 1,3,5，'
                  '输入 b 返回上一步）: ')
        if is_back(raw):
            raise BackStep
        if raw == '0':
            return list(range(len(units)))
        try:
            indexes = parse_index_list(raw, len(units))
        except ValueError as exc:
            print(f'输入无效：{exc}，请重新输入！')
            continue
        return [i - 1 for i in indexes]


def ask_params(mode):
    """询问刷取参数；返回 ('time'|'crate', 下限, 上限)。输入 b 抛 BackStep。"""
    if mode == '2':
        print('\n模式1：每个练习增加固定学习时长，请直接输入秒数')
        print('        如希望每个练习增加 30 秒，输入 30')
        print('模式2：每个练习增加随机时长，请输入“下限,上限”（英文逗号）')
        print('        如希望每个练习增加 10~30 秒，输入 10,30')
        print('提示：输入 b 返回上一步，重新选择单元')
        while True:
            raw = ask('请按格式输入时长: ')
            if is_back(raw):
                raise BackStep
            try:
                low, high = parse_seconds_spec(raw)
            except ValueError as exc:
                print(f'输入格式有误：{exc}，请重新输入！\n')
                continue
            return ('time', low, high)

    print('\n模式1：每个练习指定正确率，请直接输入百分比')
    print('        如希望全部 100%，输入 100')
    print('模式2：每个练习随机正确率，请输入“下限,上限”（英文逗号）')
    print('        如希望 70%~100%，输入 70,100')
    print('提示：输入 b 返回上一步，重新选择单元')
    while True:
        raw = ask('请按格式输入正确率: ')
        if is_back(raw):
            raise BackStep
        try:
            low, high = parse_crate_spec(raw)
        except ValueError as exc:
            print(f'输入格式有误：{exc}，请重新输入！\n')
            continue
        return ('crate', low, high)


# ============================================================
# CMI 数据（关键：必须以 [INTERACTIONINFO] 结尾）
# ============================================================
def build_cmi_data(crate, session_time='0', total_time='0'):
    """构造 setscoinfo 需要的 CMI 数据。

    上游实现（Avenshy / WelearnBrainBurst / welearn-farm）都会在 JSON 后面拼一个
    [INTERACTIONINFO] 标记，服务器靠它切分 JSON 与交互数据。少了这个标记，
    setscoinfo 里携带的 score 不会被写入，WE Learn 上就会一直显示默认的 1 分。
    """
    payload = {
        'cmi': {
            'completion_status': 'completed',
            'interactions': [],
            'launch_data': '',
            'progress_measure': '1',
            'score': {'scaled': str(crate), 'raw': '100'},
            'session_time': str(session_time),
            'success_status': 'unknown',
            'total_time': str(total_time),
            'mode': 'normal',
        },
        'adl': {'data': []},
        'cci': {
            'data': [],
            'service': {
                'dictionary': {'headword': '', 'short_cuts': ''},
                'new_words': [],
                'notes': [],
                'writing_marking': [],
                'record': {'files': []},
                'play': {'offline_media_id': '9999'},
            },
            'retry_count': '0',
            'submit_time': '',
        },
    }
    return json.dumps(payload, separators=(',', ':')) + '[INTERACTIONINFO]'


def save_sco(conn, scoid, location, crate, session_time='0', total_time='0'):
    """两段式提交：方式1 setscoinfo；失败时用方式2 savescoinfo160928 兜底。

    返回 (方式1 是否成功, 方式2 是否成功, 失败原因)。
    """
    referer = f'{STUDY_PAGE}?cid={cid}&classid={classid}&sco={scoid}'
    url = f'{AJAX_SCO}?uid={uid}'

    if not cid or not uid:
        return False, False, f'内部状态异常（cid={cid}, uid={uid}），请重新选择课程'

    try:
        post_sco(conn, url, {
            'action': 'startsco160928',
            'uid': uid, 'cid': cid, 'scoid': scoid,
            'classid': classid, 'tid': '-1',
        }, referer)
    except Exception as exc:
        return False, False, f'startsco160928 异常: {exc}'

    # 方式1：把完整 CMI 数据交给服务器（含分数）
    data = build_cmi_data(crate, session_time, total_time)
    try:
        resp1 = post_sco(conn, url, {
            'action': 'setscoinfo',
            'cid': cid, 'scoid': scoid, 'uid': uid,
            'data': data,
            'isend': 'False',
        }, referer)
    except Exception as exc:
        return False, False, f'setscoinfo 异常: {exc}'

    way1_ok = is_ok(resp1)
    if way1_ok:
        return True, False, ''

    # 方式2：兜底接口，crate 使用用户设置的正确率（不再写死 100）
    try:
        resp2 = post_sco(conn, url, {
            'action': 'savescoinfo160928',
            'cid': cid, 'scoid': scoid, 'uid': uid,
            'progress': '100', 'crate': str(crate),
            'status': 'unknown', 'cstatus': 'completed', 'trycount': '0',
        }, referer)
    except Exception as exc:
        return False, False, f'savescoinfo160928 异常: {exc}'

    if is_ok(resp2):
        return False, True, ''

    return False, False, f'setscoinfo→{short(resp1)}；savescoinfo→{short(resp2)}'


# ============================================================
# 多线程刷课
# ============================================================
class NewThread(Thread):
    def __init__(self, crate, x):
        super().__init__(daemon=True)
        self.crate = crate
        self.x = x

    def run(self):
        startstudy(self.crate, self.x)


class TimeThread(Thread):
    def __init__(self, task_idx, statuses, target_time, x):
        super().__init__(daemon=True)
        self.task_idx = task_idx
        self.statuses = statuses
        self.target_time = target_time
        self.x = x

    def run(self):
        startstudy_time(self.task_idx, self.statuses, self.target_time, self.x)


def startstudy(crate, x):
    """刷课模式：完成单个小节（完成度 + 正确率 + 分数）。"""
    scoid = x['id']
    location = x.get('location', scoid)
    conn = new_session()

    print(f'开始学习: {location}   目标正确率 {crate}%')
    way1_ok, way2_ok, reason = save_sco(conn, scoid, location, crate)

    if way1_ok:
        way1Succeed.append(location)
        print(f'[ 已完成 ]    {location}')
    elif way2_ok:
        way2Succeed.append(location)
        print(f'[ 已完成 ]    {location}（方式2）')
    else:
        way1Failed.append(location)
        print(f'[ 提交失败 ]    {location}')
        print(f'             服务器返回: {reason}')


def startstudy_time(task_idx, statuses, target_time, x):
    """刷时长模式：每个线程独立 session，通过 keepsco 累积时间。"""
    scoid = x['id']
    location = x.get('location', scoid)
    referer = f'{STUDY_PAGE}?cid={cid}&classid={classid}&sco={scoid}'
    url = f'{AJAX_SCO}?uid={uid}'

    conn = new_session()

    # 开始学习
    try:
        back = conn.post(url, data={
            'action': 'startsco160928',
            'uid': uid, 'cid': cid, 'scoid': scoid,
            'classid': classid, 'tid': '-1',
        }, headers={'Referer': referer}, timeout=20)
        if back.json().get('ret', -1) != 0:
            statuses[task_idx]['status'] = '失败'
            way1Failed.append(location)
            return
    except Exception:
        statuses[task_idx]['status'] = '异常'
        way1Failed.append(location)
        return

    # keepsco 循环累积时间
    elapsed = 0
    session_time = 0
    total_time = 0
    statuses[task_idx]['status'] = '刷取中'

    while elapsed < target_time:
        try:
            resp = conn.post(url, data={
                'action': 'keepsco_with_getticket_with_updatecmitime',
                'uid': uid, 'cid': cid, 'scoid': scoid,
                'session_time': str(session_time),
                'total_time': str(total_time),
                'timelimitsec': '0', 'endcaltime': 'false',
            }, headers={'Referer': referer}, timeout=20)

            if resp.json().get('ret', -1) not in (0, 1):
                break

            elapsed += 1
            session_time += 1
            total_time += 1
            statuses[task_idx]['elapsed'] = elapsed
            time.sleep(1)
        except Exception:
            time.sleep(2)
            break

    way1_ok, way2_ok, reason = save_sco(
        conn, scoid, location, '100',
        session_time=session_time, total_time=total_time)

    if way1_ok:
        statuses[task_idx]['status'] = '已完成'
        way1Succeed.append(location)
    elif way2_ok:
        statuses[task_idx]['status'] = '已完成'
        way2Succeed.append(location)
    else:
        statuses[task_idx]['status'] = '失败'
        way1Failed.append(location)
        debug_log(f'刷时长提交失败 {location}', reason)


# ============================================================
# 任务收集与执行
# ============================================================
def is_visible(item):
    return str(item.get('isvisible', 'true')).lower() != 'false'


def is_completed(item):
    """iscomplete 里带“未”字样表示未完成，其余（已完成/已通过）视为已完成。"""
    status = str(item.get('iscomplete', '') or '')
    return bool(status.strip()) and '未' not in status


def build_tasks(mode, course, units, unit_indexes, spec, redo_completed=False):
    """收集待处理小节；返回 (task_list, 跳过数)。"""
    cid_value = course['cid']
    kind, low, high = spec
    tasks = []
    skipped = 0

    for unit_index in unit_indexes:
        if unit_index >= len(units):
            print(f'[!!跳过!!]    单元 {unit_index + 1} 不存在')
            continue
        unit_name = units[unit_index].get('unitname', f'单元{unit_index + 1}')

        items = fetch_sco_leaves(cid_value, unit_index)
        if not items:
            print(f'[!!跳过!!]    {unit_name} (无项目)')
            continue

        for item in items:
            if not isinstance(item, dict):
                continue
            if not is_visible(item):
                skipped += 1
                continue
            # 刷课模式下默认跳过已完成的小节：重复提交反而会把已有分数覆盖掉
            if mode == '1' and not redo_completed and is_completed(item):
                skipped += 1
                continue

            location = item.get('location', item['id'])
            if kind == 'time':
                target = random.randint(low, high)
                tasks.append({'id': item['id'], 'location': location, 'target': target})
            else:
                if low == high:
                    crate = low
                elif high - low <= 2:
                    crate = random.randint(low, high)
                else:
                    crate = int(round(random.gauss((low + high) / 2, (high - low) / 6)))
                    crate = max(low, min(high, crate))
                tasks.append({'id': item['id'], 'location': location, 'crate': crate})

    return tasks, skipped


def run_course_tasks(tasks):
    """刷课模式：并发执行并等待完成。"""
    threads = []
    for task in tasks:
        thread = NewThread(task['crate'], {'id': task['id'], 'location': task['location']})
        thread.start()
        threads.append(thread)
    for thread in threads:
        thread.join()


def run_time_tasks(tasks):
    """刷时长模式：多线程并发 + 主循环刷新进度。"""
    total = len(tasks)
    max_loc_len = max(len(str(t['location'])) for t in tasks)
    statuses = [{'status': '等待中', 'elapsed': 0, 'target': t['target']} for t in tasks]

    def redraw():
        for i in range(total):
            location = str(tasks[i]['location'])
            pad = ' ' * (max_loc_len - len(location))
            status = statuses[i]
            if status['status'] == '刷取中':
                line = (f'  [{i + 1:>3d}/{total}]  {location}{pad}  '
                        f'{status["elapsed"]:>4d}/{status["target"]} 秒')
            else:
                line = f'  [{i + 1:>3d}/{total}]  {location}{pad}  {status["status"]}'
            print(f'\r\033[K{line}')

    print(f'\n共 {total} 个待刷时长项目，开始并发刷取...\n')
    threads = []
    for i, task in enumerate(tasks):
        thread = TimeThread(i, statuses, task['target'],
                            {'id': task['id'], 'location': task['location']})
        thread.start()
        threads.append(thread)

    while any(t.is_alive() for t in threads):
        print(f'\r\033[{total}A')
        redraw()
        time.sleep(1)

    print(f'\r\033[{total}A')
    redraw()
    print()


def report_recorded(cid_value, unit_indexes):
    """提交后回读服务器记录，打印每个小节实际被写入的 crate 与完成状态。"""
    print('\n服务器记录（crate 为 WE Learn 保存的正确率字段）：')
    for unit_index in unit_indexes:
        try:
            items = fetch_sco_leaves(cid_value, unit_index)
        except FetchError as exc:
            print(f'  单元 {unit_index + 1} 回读失败: {exc}')
            continue
        for item in items:
            if not isinstance(item, dict):
                continue
            if str(item.get('isvisible', 'true')).lower() == 'false':
                continue
            name = item.get('location') or item.get('id')
            print(f"  crate={str(item.get('crate', '?')):>4}  "
                  f"{item.get('iscomplete', '?')}  {name}")


def print_summary(mode):
    total_success = len(way1Succeed) + len(way2Succeed)
    total_failed = len(way1Failed) + len(way2Failed)
    print('\n        ****************************************************************')
    print('        全部完成!!\n')
    print(f'        总计: {total_success} 成功, {total_failed} 失败')
    if mode == '1':
        print(f'        (方式1: {len(way1Succeed)} 成功, {len(way1Failed)} 失败)')
        print(f'        (方式2: {len(way2Succeed)} 成功, {len(way2Failed)} 失败)')
    print('\n        **********    有问题请邮件联系Fanyuc0418@gmail.com    **********')


# ============================================================
# 一次完整的刷课会话
# ============================================================
def run_session(mode):
    """登录 → 选课 → 选单元 → 设参数 → 执行。任何一步选择“返回”都只回退一步。"""
    global logged_in

    for bucket in (way1Succeed, way2Succeed, way1Failed, way2Failed):
        bucket.clear()

    if not logged_in:
        login_flow()                   # 登录（输入错误/登录失败都不退出程序）
        logged_in = True               # login_by_* 成功才会返回，这里兜底

    while True:                        # 课程层
        course = select_course(mode)   # 输入 b -> BackStep -> 回主菜单

        while True:                    # 单元层
            try:
                units = load_units(course)
            except FetchError as exc:
                print(f'获取单元信息失败：{exc}')
                print('已保持登录状态，返回课程选择。\n')
                break

            try:
                unit_indexes = select_units(mode, units)
            except BackStep:
                print('已返回课程选择。\n')
                break

            try:
                spec = ask_params(mode)
                redo_completed = False
                if mode == '1':
                    raw = ask('是否重新提交已完成的小节？（y/N，输入 b 返回上一步）: ')
                    if is_back(raw):
                        print('已返回上一步。\n')
                        continue
                    redo_completed = raw.lower() in ('y', 'yes')
            except BackStep:
                # 参数输错返回上一步 = 重新选单元
                print('已返回上一步。\n')
                continue

            tasks, skipped = build_tasks(mode, course, units, unit_indexes, spec,
                                         redo_completed)
            if not tasks:
                print(f'没有需要处理的项目（跳过 {skipped} 个）。')
                print('已保持登录状态，请重新选择单元。\n')
                continue

            print(f'\n共 {len(tasks)} 个待处理项目（跳过 {skipped} 个）')
            if mode == '2':
                run_time_tasks(tasks)
            else:
                run_course_tasks(tasks)

            print_summary(mode)
            report_recorded(course['cid'], unit_indexes)
            back = ask('\n按回车返回主菜单，输入 q 退出程序: ')
            if back in ('q', 'Q', 'quit', 'exit'):
                return 'quit'
            return 'restart'


# ============================================================
# 主程序入口
# ============================================================
def banner():
    print('**********    致敬初代版本开发者： Avenshy & SSmJaE   **********')
    print(f'**********      当前版本--welearn一体版： {VERSION}      **********')
    print('**********   您所使用的版本由饭昱昶于2026.05.24更新   **********')
    print('**********      QQ官方群：无 || 感谢支持       **********')
    print('****************************************************************\n')


def main():
    banner()
    while True:
        try:
            mode = choose_mode()
        except BackStep:
            continue
        except EofReached:
            print('\n输入已结束，程序退出。')
            return
        if mode is None:
            print('已退出。')
            return

        if mode == 'logout':
            do_logout()
            continue

        clear_screen()
        try:
            result = run_session(mode)
        except BackStep:
            print('\n已返回主菜单。\n')
            continue
        except EofReached:
            print('\n输入已结束，程序退出。')
            return
        except KeyboardInterrupt:
            print('\n已中断，返回主菜单。\n')
            continue
        except Exception as exc:  # 兜底：任何异常都不退出，保持登录状态回主菜单
            print(f'\n出现未预期的错误：{exc}')
            if DEBUG:
                traceback.print_exc()
            print('已保持登录状态，返回主菜单。\n')
            continue

        if result == 'quit':
            print('已退出。')
            return
        print()


if __name__ == '__main__':
    try:
        main()
    except KeyboardInterrupt:
        print('\n已退出。')
