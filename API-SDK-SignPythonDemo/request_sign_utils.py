import hashlib
import hmac
import json
import time
import uuid
import requests

class RequestSignUtils(object):
    def __init__(self):
        self.HEAD_CONTENT_TYPE = "Content-Type"
        self.HEAD_ACCEPT_LANGUAGE = "Accept-Language"
        self.HEAD_VERSION = "Version"
        self.HEAD_TIMESTAMP = "Timestamp"
        self.HEAD_NONCE = "Nonce"
        self.HEAD_ACCESSKEY = "AccessKey"
        self.HEAD_APP_ACCESS_TOKEN = "AppAccessToken"
        self.HEAD_X_TRACEID = "X-TraceId-Header"
        self.HEAD_PRODUCT_ID = "ProductId"
        self.HEAD_SIGN = "Sign"
        self.HEAD_SIGN_HEADERS = "SignHeaders"


    # 开发者获取AppAccessToken鉴权签名
    def open_token_sign(self, sign):
        if not sign.get("access_key") or not sign.get("timestamp") or not sign.get("nonce") or not sign.get("method") or not sign.get("secret_access_key"):
            raise RuntimeError("鉴权参数缺失")
        
        str_to_sign = f'{sign["access_key"]}{sign["timestamp"]}{sign["nonce"]}'
        
        lines = [sign["method"]]
        if sign.get("body"):
            body = self.delete_whitespace(sign["body"])
            lines.append(hashlib.sha512(body.encode("utf-8")).hexdigest())
        
        sign_headers = sign.get("headers", {}).get(self.HEAD_SIGN_HEADERS, "")
        if sign_headers:
            header_lines = []
            for header in sign_headers.split():
                header_name = header.strip()
                if header_name:
                    header_value = sign["headers"].get(header_name.lower(), "")
                    header_lines.append(f"{header_name}:{header_value}")
            lines.append("\n".join(header_lines))
        
        string_to_sign = "\n".join(lines)
        str_to_sign += string_to_sign
        sign_result = hmac.new(sign["secret_access_key"].encode("utf-8"), str_to_sign.encode("utf-8"), hashlib.sha512).hexdigest().upper()
        return sign_result


    # 开发者API鉴权签名
    def open_sign(self, sign):
        if not sign.get("access_key") or not sign.get("app_access_token") or not sign.get("timestamp") or not sign.get("nonce") or not sign.get("method") or not sign.get("secret_access_key"):
            raise RuntimeError("鉴权参数缺失")
        
        str_to_sign = f'{sign["access_key"]}{sign["app_access_token"]}{sign["timestamp"]}{sign["nonce"]}'
        
        lines = [sign["method"]]
        if sign.get("body"):
            body = self.delete_whitespace(sign["body"])
            lines.append(hashlib.sha512(body.encode("utf-8")).hexdigest())
        
        sign_headers = sign.get("headers", {}).get(self.HEAD_SIGN_HEADERS, "")
        if sign_headers:
            header_lines = []
            for header in sign_headers.split():
                header_name = header.strip()
                if header_name:
                    header_value = sign["headers"].get(header_name.lower(), "")
                    header_lines.append(f"{header_name}:{header_value}")
            lines.append("\n".join(header_lines))
        
        string_to_sign = "\n".join(lines)
        str_to_sign += string_to_sign
        
        sign_result = hmac.new(sign["secret_access_key"].encode("utf-8"), str_to_sign.encode("utf-8"), hashlib.sha512).hexdigest().upper()
        return sign_result


    # 删除字符串中的空白字符
    def delete_whitespace(self, str):
        if not str:
            return str
        return "".join(c for c in str if not c.isspace())


    # 发送POST请求
    def post_info(self, url, headers, body):
        try:
            headers["Content-Type"] = "application/json"
            headers["accept"] = "application/json"
            headers["ReadTimeout"] = "20"
            
            with requests.post(url, headers=headers, data=json.dumps(body), timeout=20) as response:
                if response.status_code == 200 or response.status_code == 401:
                    return True, json.loads(response.text)
                else:
                    return False, response.raise_for_status()
        except Exception as error_info:
            return False, f"请求异常: {error_info}"


    # 开发者获取AppAccessToken
    def get_app_access_token(self, access_key, time_stamp, nonce, token_sign, product_id, api_version, domain_name):
        headers = {
            self.HEAD_CONTENT_TYPE: "application/json",
            self.HEAD_ACCESSKEY: access_key,
            self.HEAD_TIMESTAMP: time_stamp,
            self.HEAD_NONCE: nonce,
            self.HEAD_SIGN: token_sign,
            self.HEAD_X_TRACEID: str(uuid.uuid4()),
            self.HEAD_PRODUCT_ID: product_id,
            self.HEAD_VERSION: api_version,
        }

        api_url = f"https://{domain_name}/open-api/api-base/auth/getAppAccessToken"
        request_body = {}
        response_code, response= self.post_info(api_url, headers, request_body)
        if response_code and response.get("code") == "200":
            app_access_token = response["data"]["appAccessToken"]
            return True, app_access_token
        return False, response


    # 主函数
    def main(self):
        access_key = "1862372654909100032"
        secret_access_key = "ugqtkcj0ci0sxd462djk75xwz5xtwb6q"
        method = "POST"
        time_stamp = str(int(time.time() * 1000))
        nonce = str(uuid.uuid4())   

        # 开发者获取AppAccessToken鉴权签名
        sign = {
            "access_key": access_key,
            "timestamp": time_stamp,
            "nonce": nonce,
            "method": method,
            "secret_access_key": secret_access_key
        }
        token_sign = self.open_token_sign(sign)

        product_id = "114479962"
        api_version = "v1"
        domain_name = "open.cloud-dahua.com"

        # 开发者获取AppAccessToken
        get_code, app_access_token = self.get_app_access_token(access_key, time_stamp, nonce, token_sign, product_id, api_version, domain_name)
        if not get_code:
            print(f"获取AppAccessToken失败, 错误信息:{app_access_token}")
            exit(1)

        # 获取设备绑定信息
        api_url = f"https://{domain_name}/open-api/api-iot/device/checkDeviceBindInfo"
        request_body = {
            "deviceId": "5F0679CPAJ7516A"
        }

        headers = {
            self.HEAD_CONTENT_TYPE: "application/json",
            self.HEAD_ACCEPT_LANGUAGE: "zh-CN",
            self.HEAD_VERSION: "v1",
            self.HEAD_TIMESTAMP: time_stamp,
            self.HEAD_NONCE: nonce,
            self.HEAD_ACCESSKEY: access_key,
            self.HEAD_APP_ACCESS_TOKEN: app_access_token,
            self.HEAD_X_TRACEID: str(uuid.uuid4()),
            self.HEAD_PRODUCT_ID: product_id,
            self.HEAD_SIGN: token_sign
        }

        sign = {
            "access_key": access_key,
            "app_access_token": app_access_token,
            "timestamp": time_stamp,
            "nonce": nonce,
            "method": method,
            "body": json.dumps(request_body),
            "secret_access_key": secret_access_key,
            "headers": headers
        }
        api_sign = self.open_sign(sign)
        headers[self.HEAD_SIGN] = api_sign

        response_code, response = self.post_info(api_url, headers, request_body)
        if not response_code:
            print(f"获取设备绑定信息失败, 错误信息:{response}")
        else:
            print(response)
        return

if __name__ == "__main__":
    request_sign_utils = RequestSignUtils()
    request_sign_utils.main()
