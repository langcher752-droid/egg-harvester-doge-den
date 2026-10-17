"""微信腿（macOS 原生，本人号手机扫码登录）——解密与导出指引。

核心约束（已侦察确认，2026-10-15）：
- pywxdump 是 Windows-only，macOS 上 import 即崩（依赖 winreg），不可用。
- darvincible/wechat-mac-decrypt 仓库已 404 消失，不可用。
- chatlog (sjzar/chatlog) 已被作者于 2025-10-20 因微信官方法律函下架，代码全删，不可用。

本人实测可用的两条 macOS 路径（均适用于微信 4.x + Apple Silicon，且都是你本人
正常 Mac 微信、手机扫码登录，不新增机器人、不连手机/安卓）：

== 主路径：ydotdog/wechat-export-macos（产出明文 SQLite，本项目的 wechat_db.py 直接读）==
1. 前置（你已装好 sqlcipher）：
     brew install sqlcipher
2. 克隆并准备：
     git clone https://github.com/ydotdog/wechat-export-macos.git
     cd wechat-export-macos
     pip3 install pycryptodome
3. 一次性去 Hardened Runtime（否则读不到微信内存里的密钥）：
     # 先退出微信
     sudo codesign --force --deep --sign - /Applications/WeChat.app
     # 重新打开微信并手机扫码登录
   （微信大版本更新后需重跑这步；之后可用 App Store 重装恢复官方签名）
4. 编译密钥扫描器并提取密钥（需 sudo，微信须运行且已登录）：
     cc -O2 -o find_all_keys_macos find_all_keys_macos.c -framework Foundation
     sudo ./find_all_keys_macos        # 输出 all_keys.json
5. 解密本地库（自动探测微信数据目录）：
     python3 decrypt_db.py             # 明文库在 ./decrypted/ 下
6. 交给本项目读取：
     python3 -c "from wechat_db import harvest_wechat; import json;
                 print(json.dumps(harvest_wechat('./wechat-export-macos/decrypted'), indent=2, ensure_ascii=False))"

解密后结构（wechat_db.py 据此读取）：
  decrypted/contact/contact.db        表 contact(username, nick_name, remark, alias)
  decrypted/message/message_*.db      表 Msg_<md5(wxid)>(local_id, server_id, local_type,
                                      create_time, real_sender_id, message_content,
                                      source, WCDB_CT_message_content)
                                     同库 Name2Id(rowid, user_name) 解发送人
  群 = contact.username 以 @chatroom 结尾；create_time 为 unix 时间戳。

== 备用路径：tzwkb/wechat-decrypt（Frida 取密钥，自带 query.py --json 查询接口）==
  特点：维护活跃、macOS/Windows 4.x 均支持；提供 list/read/search/recent --json，
  可直接把 JSON 喂给本项目（需自行写薄适配层调 query.py 再 process_text）。
  取密钥同样需 ad-hoc 重签微信 + 手机扫码登录（extract_key.sh 会关微信后用 Frida
  重开并等扫码，正好契合"手机扫码登录"）。
  用法骨架：
     git clone https://github.com/tzwkb/wechat-decrypt.git
     cd wechat-decrypt && bash setup.sh
     sudo codesign --force --deep --sign - /Applications/WeChat.app
     bash scripts/macos/extract_key.sh     # 扫码后写 key.txt
     .venv/bin/python scripts/common/query.py recent -d 3 -n 100 --json

注意：两条路径都涉及对本人微信客户端的一次性重签 / 内存读取，属平台 ToS 灰色地带，
你已知悉。本项目只读取、不调用微信 API 发任何请求，封号风险集中在取密钥环节。
"""
import os
import sys

# 让本项目在微信解密目录之外也能 import 同目录模块
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))


def main():
    print(__doc__)


if __name__ == "__main__":
    main()
