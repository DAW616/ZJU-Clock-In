# -*- coding: utf-8 -*-
"""
兼容新版 CAS 的浙大健康打卡脚本（无依赖版）
保存为 clock_in_fixed.py 后运行:
python3 clock_in_fixed.py <username> <password>
"""

import requests
import json
import re
import datetime
import time
import sys

# ---------------- Exceptions ----------------
class LoginError(Exception):
    pass

class RegexMatchError(Exception):
    pass

class DecodeError(Exception):
    pass

# ---------------- Helper functions ----------------
def _rsa_encrypt(password_str, e_str, M_str):
    password_bytes = password_str.encode('utf-8')
    password_int = int.from_bytes(password_bytes, 'big')
    e_int = int(e_str, 16)
    M_int = int(M_str, 16)
    result_int = pow(password_int, e_int, M_int)
    return hex(result_int)[2:].rjust(128, '0')

def find_execution_from_html(html):
    """
    无 BeautifulSoup 提取 execution。
    使用正则表达式。
    """

    # 常规 input[name="execution"]
    m = re.search(
        r'<input[^>]*name=["\']execution["\'][^>]*value=["\']([^"\']+)["\']',
        html
    )
    if m:
        return m.group(1)

    # 在 JS 中: execution: "xxxx"
    m = re.search(r'execution["\']\s*:\s*["\']([^"\']+)["\']', html)
    if m:
        return m.group(1)

    return None

def extract_old_info_and_def(html):
    """
    完全使用正则，不需要 BeautifulSoup。
    """

    # -------- oldInfo 提取 --------
    try:
        old_match = re.search(r'oldInfo\s*:\s*({[\s\S]*?})\s*,\s*def\s*=', html)
        if not old_match:
            old_match = re.search(r'oldInfo\s*=\s*({[\s\S]*?})\s*;', html)
        if old_match:
            old_info = json.loads(old_match.group(1))
        else:
            m_json = re.search(r'"oldInfo"\s*:\s*({[\s\S]*?})\s*,', html)
            if m_json:
                old_info = json.loads(m_json.group(1))
            else:
                raise RegexMatchError("未找到 oldInfo，请至少手动打卡一次或检查 HTML。")

    except json.decoder.JSONDecodeError as e:
        raise DecodeError("解析 oldInfo JSON 出错: " + str(e))

    # -------- def 提取 --------
    try:
        def_match = re.search(r'def\s*=\s*({[\s\S]*?})', html)
        if def_match:
            def_obj = json.loads(def_match.group(1))
        else:
            m_def = re.search(r'"def"\s*:\s*({[\s\S]*?})\s*,', html)
            if m_def:
                def_obj = json.loads(m_def.group(1))
            else:
                def_obj = {}
                if 'id' in old_info:
                    def_obj['id'] = old_info['id']
                else:
                    raise RegexMatchError("未找到 def 用户数据（或 id），无法继续。")

    except json.decoder.JSONDecodeError as e:
        raise DecodeError("解析 def JSON 出错: " + str(e))

    # -------- 姓名 realname --------
    name = None
    number = None

    m = re.search(r'realname\s*:\s*["\']([^"\']+)["\']', html)
    if m:
        name = m.group(1)

    m2 = re.search(r"number\s*:\s*['\"]([^'\"]+)['\"]", html)
    if m2:
        number = m2.group(1)

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
            "User-Agent": ("Mozilla/5.0"),
            "Referer": self.login_url
        }
        self.sess = requests.Session()
        self.sess.headers.update(self.headers)
        self.info = None
        self.debug = debug

    def login(self):
        res = self.sess.get(self.login_url, timeout=15)
        html = res.text

        execution = find_execution_from_html(html)
        if not execution:
            raise LoginError("无法解析 execution（页面可能已变更）。")

        pub = self.sess.get('https://zjuam.zju.edu.cn/cas/v2/getPubKey', timeout=10).json()
        n, e = pub.get('modulus'), pub.get('exponent')
        if not n or not e:
            raise LoginError("无法获取 CAS 公钥")

        encrypt_password = _rsa_encrypt(self.password, e, n)

        data = {
            'username': self.username,
            'password': encrypt_password,
            'execution': execution,
            '_eventId': 'submit'
        }

        res2 = self.sess.post(self.login_url, data=data, allow_redirects=True, timeout=15)
        final_url = res2.url
        text2 = res2.text

        if "healthreport.zju.edu.cn" in final_url:
            return self.sess

        if "统一身份认证" in text2:
            raise LoginError("登录失败，请检查账号密码。")

        return self.sess

    def get_info(self, html=None):
        if not html:
            res = self.sess.get(self.base_url, timeout=12)
            html = res.text

        old_info, def_obj, name, number = extract_old_info_and_def(html)

        new_info = old_info.copy()
        if 'id' in def_obj and def_obj.get('id'):
            new_info['id'] = def_obj['id']

        new_info['name'] = name or new_info.get('name', '')
        new_info['number'] = number or new_info.get('number', '')

        today = datetime.date.today()
        new_info["date"] = "%4d%02d%02d" % (today.year, today.month, today.day)
        new_info["created"] = round(time.time())

        new_info["address"] = "浙江省杭州市西湖区"
        new_info["area"] = "浙江省 杭州市 西湖区"
        new_info["province"] = "浙江省"
        new_info["city"] = "杭州市"

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
            raise Exception("请先 get_info()")
        res = self.sess.post(self.save_url, data=self.info, timeout=12)
        try:
            return res.json()
        except:
            return {"e": -1, "m": "非 JSON 返回", "raw": res.text[:2000]}

# ---------------- main entry ----------------
def main(username, password, debug=False):
    print("\n[Time] %s" % datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S'))
    print("🚌 打卡任务启动")

    dk = DaKa(username, password, debug=debug)

    print("登录中...")
    dk.login()
    print("登录成功")

    print("获取信息...")
    dk.get_info()
    print("已获取信息")

    print("正在打卡...")
    res = dk.post()
    if isinstance(res, dict) and str(res.get('e', '')) == '0':
        print("打卡成功！")
    else:
        print("服务器返回：", res)

if __name__ == "__main__":
    if len(sys.argv) < 3:
        print("用法: python3 clock_in_fixed.py <ACCOUNT> <PASSWORD> [debug]")
        sys.exit(1)
    user = sys.argv[1]
    pwd = sys.argv[2]
    debug_flag = False
    if len(sys.argv) > 3 and sys.argv[3].lower() in ('1','true','debug'):
        debug_flag = True
    main(user, pwd, debug=debug_flag)
