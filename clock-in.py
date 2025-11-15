# -*- coding: utf-8 -*-

# 打卡脚修改自ZJU-nCov-Hitcarder的开源代码，感谢这位同学开源的代码

import requests
import json
import re
import datetime
import time
import sys


class DaKa(object):
    """Hit card class

    Attributes:
        username: (str) 浙大统一认证平台用户名（一般为学号）
        password: (str) 浙大统一认证平台密码
        login_url: (str) 登录url
        base_url: (str) 打卡首页url
        save_url: (str) 提交打卡url
        self.headers: (dir) 请求头
        sess: (requests.Session) 统一的session
    """

    def __init__(self, username, password):
        self.username = username
        self.password = password
        self.login_url = "https://zjuam.zju.edu.cn/cas/login?service=https%3A%2F%2Fhealthreport.zju.edu.cn%2Fa_zju%2Fapi%2Fsso%2Findex%3Fredirect%3Dhttps%253A%252F%252Fhealthreport.zju.edu.cn%252Fncov%252Fwap%252Fdefault%252Findex"
        self.base_url = "https://healthreport.zju.edu.cn/ncov/wap/default/index"
        self.save_url = "https://healthreport.zju.edu.cn/ncov/wap/default/save"
        self.headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; WOW64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/75.0.3770.100 Safari/537.36"
        }
        self.sess = requests.Session()

      def login(self):
        """Login to ZJU platform (updated regex, no bs4)"""
        res = self.sess.get(self.login_url, headers=self.headers)
        html = res.text

        # 安全解析 execution
        m = re.search(r'name="execution"\s+value="([^"]+)"', html)
        if not m:
            raise LoginError("无法解析 execution，登录页面结构已更新")
        execution = m.group(1)

        # 获取 RSA 公钥
        pub = self.sess.get('https://zjuam.zju.edu.cn/cas/v2/getPubKey',
                            headers=self.headers).json()
        n, e = pub['modulus'], pub['exponent']
        encrypt_password = self._rsa_encrypt(self.password, e, n)

        data = {
            'username': self.username,
            'password': encrypt_password,
            'execution': execution,
            '_eventId': 'submit'
        }

        # 登录
        res = self.sess.post(self.login_url, data=data, headers=self.headers)

        # 判断是否登录失败
        text = res.text
        if "统一身份认证" in text and "登录" in text:
            raise LoginError("登录失败：账号或密码错误")

        return self.sess


    def post(self):
        """Post the hitcard info"""
        res = self.sess.post(self.save_url, data=self.info, headers=self.headers)
        return json.loads(res.text)

    def get_date(self):
        """Get current date"""
        today = datetime.date.today()
        return "%4d%02d%02d" % (today.year, today.month, today.day)

    def get_info(self, html=None):
        """Get hitcard info, which is the old info with updated new time."""
        if not html:
            res = self.sess.get(self.base_url, headers=self.headers)
            html = res.content.decode()

        try:
            old_infos = re.findall(r'oldInfo: ({[^\n]+})', html)
            if len(old_infos) != 0:
                old_info = json.loads(old_infos[0])
            else:
                raise RegexMatchError("未发现缓存信息，请先至少手动成功打卡一次再运行脚本")

            new_info_tmp = json.loads(re.findall(r'def = ({[^\n]+})', html)[0])
            new_id = new_info_tmp['id']
            name = re.findall(r'realname: "([^\"]+)",', html)[0]
            number = re.findall(r"number: '([^\']+)',", html)[0]
        except IndexError:
            raise RegexMatchError('Relative info not found in html with regex')
        except json.decoder.JSONDecodeError:
            raise DecodeError('JSON decode error')

        new_info = old_info.copy()
        new_info['id'] = new_id
        new_info['name'] = name
        new_info['number'] = number
        new_info["date"] = self.get_date()
        new_info["created"] = round(time.time())
        new_info["address"] = "浙江省杭州市西湖区"
        new_info["area"] = "浙江省 杭州市 西湖区"
        new_info["province"] = new_info["area"].split(' ')[0]
        new_info["city"] = new_info["area"].split(' ')[1]
        # form change
        new_info['jrdqtlqk[]'] = 0
        new_info['jrdqjcqk[]'] = 0
        new_info['sfsqhzjkk'] = 1   # 是否申领杭州健康码
        new_info['sqhzjkkys'] = 1   # 杭州健康吗颜色，1:绿色 2:红色 3:黄色
        new_info['sfqrxxss'] = 1    # 是否确认信息属实
        new_info['jcqzrq'] = ""
        new_info['gwszdd'] = ""
        new_info['szgjcs'] = ""
        self.info = new_info
        return new_info

    def _rsa_encrypt(self, password_str, e_str, M_str):
        password_bytes = bytes(password_str, 'ascii')
        password_int = int.from_bytes(password_bytes, 'big')
        e_int = int(e_str, 16)
        M_int = int(M_str, 16)
        result_int = pow(password_int, e_int, M_int)
        return hex(result_int)[2:].rjust(128, '0')


# Exceptions
class LoginError(Exception):
    """Login Exception"""
    pass


class RegexMatchError(Exception):
    """Regex Matching Exception"""
    pass


class DecodeError(Exception):
    """JSON Decode Exception"""
    pass


def main(username, password):
    """Hit card process

    Arguments:
        username: (str) 浙大统一认证平台用户名（一般为学号）
        password: (str) 浙大统一认证平台密码
    """
    print("\n[Time] %s" %
          datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S'))
    print("🚌 打卡任务启动")

    dk = DaKa(username, password)

    print("登录到浙大统一身份认证平台...")
    try:
        dk.login()
        print("已登录到浙大统一身份认证平台")
    except Exception as err:
        print(str(err))
        raise Exception

    print('正在获取个人信息...')
    try:
        dk.get_info()
        print('已成功获取个人信息')
    except Exception as err:
        print('获取信息失败，请手动打卡，更多信息: ' + str(err))
        raise Exception

    print('正在为您打卡打卡打卡')
    try:
        res = dk.post()
        if str(res['e']) == '0':
            print('已为您打卡成功！')
        else:
            print(res['m'])
    except Exception:
        print('数据提交失败')
        raise Exception


if __name__ == "__main__":
    username = sys.argv[1]
    password = sys.argv[2]
    try:
        main(username, password)
    except Exception:
        exit(1)
