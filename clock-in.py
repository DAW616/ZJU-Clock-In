# -*- coding: utf-8 -*-
"""
兼容新版 CAS 的浙大健康打卡脚本（改进版）
依赖: requests, beautifulsoup4
保存为 clock_in_fixed.py 后运行:
python3 clock_in_fixed.py <username> <password>
"""

import requests
import json
import re
import datetime
import time
import sys
from bs4 import BeautifulSoup

# ---------------- Exceptions ----------------
class LoginError(Exception):
    pass

class RegexMatchError(Exception):
    pass

class DecodeError(Exception):
    pass

# ---------------- Helper functions ----------------
def _rsa_encrypt(password_str, e_str, M_str):
    """
    与原脚本等价的 RSA 模拟加密（纯 Python 大数模幂）
    注意：使用 utf-8 编码（比 ascii 更通用）
    """
    password_bytes = password_str.encode('utf-8')
    password_int = int.from_bytes(password_bytes, 'big')
    e_int = int(e_str, 16)
    M_int = int(M_str, 16)
    result_int = pow(password_int, e_int, M_int)
    return hex(result_int)[2:].rjust(128, '0')

def find_execution_from_html(html):
    """
    多方式尝试从登录页面 HTML 中提取 execution 字段。
    返回 execution 字符串，找不到则返回 None。
    """
    # 1. BeautifulSoup 查找 input[name=execution]
    try:
        soup = BeautifulSoup(html, 'html.parser')
        tag = soup.find('input', attrs={'name': 'execution'})
        if tag and tag.get('value'):
            return tag.get('value')
    except Exception:
        pass

    # 2. 常规正则（常见格式）
    m = re.search(r'name=["\']execution["\']\s+value=["\']([^"\']+)["\']', html)
    if m:
        return m.group(1)

    # 3. 查找 window.__INITIAL_STATE__ 或 类似 JSON 内嵌的 execution
    m = re.search(r'execution["\']\s*:\s*["\']([^"\']+)["\']', html)
    if m:
        return m.group(1)

    # 4. 查找表单中隐藏字段 lt 或 _eventId 等（有时需要 lt）
    # 不直接 return lt，但允许上层决定
    return None

def extract_old_info_and_def(html):
    """
    从打卡首页 HTML 中提取 oldInfo 与 def（原代码依赖这两个）
    返回 (old_info_dict, def_obj_dict, name, number)
    """
    # 尝试正则提取 oldInfo: ({...})
    try:
        # oldInfo 可能出现在 JS 变量中：oldInfo: {...}
        old_match = re.search(r'oldInfo\s*:\s*({[\s\S]*?})\s*,\s*def\s*=', html)
        if not old_match:
            # 备用匹配：oldInfo = {...}
            old_match = re.search(r'oldInfo\s*=\s*({[\s\S]*?})\s*;', html)
        if old_match:
            old_info = json.loads(old_match.group(1))
        else:
            # 备用：查找 "oldInfo": {...}
            m_json = re.search(r'"oldInfo"\s*:\s*({[\s\S]*?})\s*,', html)
            if m_json:
                old_info = json.loads(m_json.group(1))
            else:
                raise RegexMatchError("未找到 oldInfo，请至少手动打卡一次或检查 HTML。")
    except json.decoder.JSONDecodeError as e:
        raise DecodeError("解析 oldInfo JSON 出错: " + str(e))

    # def 对象（有 id）
    try:
        def_match = re.search(r'def\s*=\s*({[\s\S]*?})', html)
        if def_match:
            def_obj = json.loads(def_match.group(1))
        else:
            # 备用：查找 "def": {...}
            m_def = re.search(r'"def"\s*:\s*({[\s\S]*?})\s*,', html)
            if m_def:
                def_obj = json.loads(m_def.group(1))
            else:
                # 如果仍然找不到，尝试从 old_info 中取 id
                def_obj = {}
                if 'id' in old_info:
                    def_obj['id'] = old_info['id']
                else:
                    raise RegexMatchError("未找到 def 用户数据（或 id），无法继续。")
    except json.decoder.JSONDecodeError as e:
        raise DecodeError("解析 def JSON 出错: " + str(e))

    # 姓名和学号
    name = None
    number = None
    # 尝试正则获取 realname / number
    m = re.search(r'realname\s*:\s*["\']([^"\']+)["\']', html)
    if m:
        name = m.group(1)
    m2 = re.search(r"number\s*:\s*['\"]([^'\"]+)['\"]", html)
    if m2:
        number = m2.group(1)

    # 备用从 old_info 中读取
    if not name and 'name' in old_info:
        name = old_info.get('name')
    if not number and 'number' in old_info:
        number = old_info.get('number')

    return old_info, def_obj, name, number

# ---------------- Main DaKa class ----------------
class DaKa:
    def __init__(self, username, password, debug=False):
        self.username = username
        self.password = password
        self.login_url = ("https://zjuam.zju.edu.cn/cas/login"
                          "?service=https%3A%2F%2Fhealthreport.zju.edu.cn%2Fa_zju%2Fapi%2Fsso%2Findex"
                          "%3Fredirect%3Dhttps%253A%252F%252Fhealthreport.zju.edu.cn%252Fncov%252Fwap%252Fdefault%252Findex")
        self.base_url = "https://healthreport.zju.edu.cn/ncov/wap/default/index"
        self.save_url = "https://healthreport.zju.edu.cn/ncov/wap/default/save"
        self.headers = {
            "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; WOW64) "
                           "AppleWebKit/537.36 (KHTML, like Gecko) "
                           "Chrome/75.0.3770.100 Safari/537.36"),
            "Referer": self.login_url
        }
        self.sess = requests.Session()
        self.sess.headers.update(self.headers)
        self.info = None
        self.debug = debug

    def login(self):
        # 1. 取登录页面（可能包含 execution）
        res = self.sess.get(self.login_url, timeout=15)
        html = res.text

        if self.debug:
            print("[debug] login page length:", len(html))

        execution = find_execution_from_html(html)
        if not execution:
            # 如果没找到 execution，打印部分 HTML 帮助调试
            snippet = html[:3000].replace('\n', ' ')
            raise LoginError("无法从 CAS 登录页解析 execution（页面可能已更新）。HTML 前3k: " + snippet)

        # 2. 获取公钥（与原脚本相同）
        pub = self.sess.get('https://zjuam.zju.edu.cn/cas/v2/getPubKey', timeout=10).json()
        n, e = pub.get('modulus'), pub.get('exponent')
        if not n or not e:
            raise LoginError("无法获取 CAS 公钥 (getPubKey 返回不包含 modulus/exponent)")

        encrypt_password = _rsa_encrypt(self.password, e, n)

        data = {
            'username': self.username,
            'password': encrypt_password,
            'execution': execution,
            '_eventId': 'submit'
        }

        # 3. 提交登录（POST）
        res2 = self.sess.post(self.login_url, data=data, allow_redirects=True, timeout=15)

        # 4. 判定是否登录成功：若最终跳转到了健康打卡域名或页面含跳转目标，则认为登录成功
        final_url = res2.url
        text2 = res2.text

        if self.debug:
            print("[debug] after login final_url:", final_url)
            print("[debug] after login snippet:", text2[:800].replace('\n', ' '))

        # 登录失败常见表现：仍然包含登录页关键词 / 包含“统一身份认证”或页面里有 '未找到用户' 等
        if "healthreport.zju.edu.cn" in final_url or "统一身份认证" not in text2:
            # 尝试再次访问打卡首页（登录成功后应该能访问）
            r_check = self.sess.get(self.base_url, timeout=10)
            if r_check.status_code == 200 and "oldInfo" in r_check.text:
                return self.sess
            # 有些情况 final_url 已是健康站点，但 content 用 JS 渲染，仍返回登录页片段
            # 若仍能访问 base_url，就返回 sess
            # 否则继续判断 text2 是否明显包含 login 错误信息
        # 如果登录没有成功（仍在登录页或有错误提示）
        if "统一身份认证" in text2 or "请输入用户名" in text2 or "用户名" in text2 and "密码" in text2:
            raise LoginError("登录失败，用户名或密码可能错误，或 CAS 页面已改变。请手动检查登录页面。")
        # 兜底：返回 sess（尝试继续流程）
        return self.sess

    def get_info(self, html=None):
        """从打卡首页取得旧信息并构造新打卡 info"""
        if not html:
            res = self.sess.get(self.base_url, timeout=12)
            html = res.text

        # 解析 oldInfo, def, name, number
        old_info, def_obj, name, number = extract_old_info_and_def(html)

        # 构造 new_info
        new_info = old_info.copy()
        # id 优先用 def_obj 的 id
        if 'id' in def_obj and def_obj.get('id'):
            new_info['id'] = def_obj['id']
        new_info['name'] = name or new_info.get('name', '')
        new_info['number'] = number or new_info.get('number', '')
        # 日期与时间
        today = datetime.date.today()
        new_info["date"] = "%4d%02d%02d" % (today.year, today.month, today.day)
        new_info["created"] = round(time.time())

        # 常用默认字段（可按需修改）
        new_info["address"] = "浙江省杭州市西湖区"
        new_info["area"] = "浙江省 杭州市 西湖区"
        new_info["province"] = new_info["area"].split(' ')[0]
        new_info["city"] = new_info["area"].split(' ')[1] if len(new_info["area"].split(' ')) > 1 else ''
        # form change（常用的打卡字段调整）
        new_info['jrdqtlqk[]'] = 0
        new_info['jrdqjcqk[]'] = 0
        new_info['sfsqhzjkk'] = 1
        new_info['sqhzjkkys'] = 1
        new_info['sfqrxxss'] = 1
        new_info['jcqzrq'] = ""
        new_info['gwszdd'] = ""
        new_info['szgjcs'] = ""

        self.info = new_info
        return new_info

    def post(self):
        if not self.info:
            raise Exception("info 未构造，请先调用 get_info()")
        res = self.sess.post(self.save_url, data=self.info, timeout=12)
        try:
            return res.json()
        except Exception:
            # 返回文本以便调试
            return {"e": -1, "m": "返回非 JSON 内容", "raw": res.text[:2000]}

# ---------------- main entry ----------------
def main(username, password, debug=False):
    print("\n[Time] %s" % datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S'))
    print("🚌 打卡任务启动")

    dk = DaKa(username, password, debug=debug)

    print("登录到浙大统一身份认证平台...")
    try:
        dk.login()
        print("已登录到浙大统一身份认证平台")
    except Exception as err:
        print("登录失败：", str(err))
        raise

    print('正在获取个人信息...')
    try:
        dk.get_info()
        print('已成功获取个人信息')
    except Exception as err:
        print('获取信息失败，请手动在浏览器至少成功打卡一次后再运行脚本。更多信息: ' + str(err))
        # 为便于调试，若有 HTML 片段可打印，可在调用 get_info 时传入 html 并观察
        raise

    print('正在为您提交打卡')
    try:
        res = dk.post()
        # 返回 JSON 时通常存在 'e' 字段
        if isinstance(res, dict) and str(res.get('e', '')) == '0':
            print('已为您打卡成功！')
        elif isinstance(res, dict) and 'm' in res:
            print('服务器返回：', res.get('m'))
            # 如果返回 raw 调试信息
            if 'raw' in res:
                print("raw response snippet:", res['raw'][:800].replace('\n', ' '))
        else:
            print('未知返回：', res)
    except Exception as e:
        print('数据提交失败：', str(e))
        raise

if __name__ == "__main__":
    if len(sys.argv) < 3:
        print("用法: python3 clock_in_fixed.py <ACCOUNT> <PASSWORD> [debug]")
        sys.exit(1)
    user = sys.argv[1]
    pwd = sys.argv[2]
    debug_flag = False
    if len(sys.argv) > 3 and sys.argv[3].lower() in ('1', 'true', 'debug'):
        debug_flag = True
    try:
        main(user, pwd, debug=debug_flag)
    except Exception as ee:
        print("Error:", str(ee))
        sys.exit(1)
