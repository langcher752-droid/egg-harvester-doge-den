#!/usr/bin/env python3
"""被动转发 CLI：把粘贴/转发的文本交给核心管线，打印提取+测试结果。

用法：
  echo "https://x.com/v1|sk-xxxx" | python cli.py
  python cli.py -f message.txt
"""
import sys

from pipeline import process_text


def run(text: str):
    results = process_text(text, source="cli")
    print(f"提取到 {len(results)} 个候选：")
    for x in results:
        if x["status"] == "valid":
            print(f"  [VALID] {x.get('base_url')} | {x['provider']} | {len(x['models'])} models | hash={x['hash']}")
        else:
            print(f"  [INVALID] {x.get('base_url')} | {x['api_key_mask']}")


def main():
    if len(sys.argv) > 2 and sys.argv[1] == "-f":
        with open(sys.argv[2], encoding="utf-8") as f:
            text = f.read()
    else:
        text = sys.stdin.read()
    if not text.strip():
        print("无输入。")
        return
    run(text)


if __name__ == "__main__":
    main()
