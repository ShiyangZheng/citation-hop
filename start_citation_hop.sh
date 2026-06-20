#!/bin/bash
# Citation Hop 启动脚本
# 用法: bash start_citation_hop.sh

# 设置项目路径
PROJECT_DIR="/Users/admin/WorkBuddy/2026-06-19-07-51-51/citation_hop"
PYTHON_BIN="/Users/admin/.workbuddy/binaries/python/envs/citation-tool/bin/python"
LOG_FILE="/tmp/citation_hop_startup.log"

# 切换到项目目录
cd "$PROJECT_DIR" || exit 1

# 终止旧进程
echo "正在检查是否有运行中的进程..."
pkill -f "python.*citation_hop" && sleep 1

# 启动 citation_hop
echo "正在启动 citation_hop..."
PYTHONPATH="$PROJECT_DIR/src" "$PYTHON_BIN" -m citation_hop > "$LOG_FILE" 2>&1 &

# 等待启动
sleep 2

# 显示启动状态
if pgrep -f "python.*citation_hop" > /dev/null; then
    echo "✅ citation_hop 启动成功!"
    echo "📋 PID: $(pgrep -f 'python.*citation_hop')"
    echo "🔑 快捷键: Cmd+Shift+L"
    echo "📄 配置文件: ~/Library/Application Support/citationHop/config.json"
    echo "📝 日志文件: $LOG_FILE"
    echo ""
    echo "当前路由模式: doi_always (总是打开 DOI 页面)"
    echo ""
    tail -3 "$LOG_FILE"
else
    echo "❌ 启动失败,查看日志:"
    cat "$LOG_FILE"
    exit 1
fi
