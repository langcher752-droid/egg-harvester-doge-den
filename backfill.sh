#!/bin/bash
# 快捷脚本：批量回采历史消息中的鸡蛋

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PYTHON="${PYTHON:-$SCRIPT_DIR/.venv/bin/python}"
if [[ ! -x "$PYTHON" ]]; then PYTHON="${PYTHON_FALLBACK:-python3}"; fi

echo "=== 鸡蛋回采工具 ==="
echo ""
echo "当前选项："
echo "  1. 回采所有未提取消息（限制 5000 条）"
echo "  2. 仅回采 QQ 消息"
echo "  3. 仅回采微信消息"
echo "  4. 指定群聊回采"
echo "  5. 重新提取所有消息（包括已提取）"
echo ""
read -p "选择 [1-5]: " choice

cd "$SCRIPT_DIR"

case $choice in
    1)
        $PYTHON backfill_keys.py --limit 5000
        ;;
    2)
        $PYTHON backfill_keys.py --platform qq --limit 5000
        ;;
    3)
        $PYTHON backfill_keys.py --platform wechat --limit 5000
        ;;
    4)
        read -p "输入群聊 ID: " chat_id
        $PYTHON backfill_keys.py --chat-id "$chat_id" --limit 5000
        ;;
    5)
        echo "⚠️  这将重新提取所有消息，可能重复测试已有 key"
        read -p "确认继续? [y/N]: " confirm
        if [[ "$confirm" == "y" || "$confirm" == "Y" ]]; then
            $PYTHON backfill_keys.py --all --limit 5000
        else
            echo "已取消"
        fi
        ;;
    *)
        echo "无效选择"
        exit 1
        ;;
esac

echo ""
echo "=== 回采完成 ==="
