#!/bin/bash
# citationHop 重启脚本
# 1. 杀掉所有老进程（菜单栏图标消失）
pkill -f "citation_hop" 2>/dev/null
sleep 1

# 2. 启动新版（前台运行，能看到 banner）
PY=/Users/admin/.workbuddy/binaries/python/envs/citation-tool/bin/python
cd /Users/admin/WorkBuddy/2026-06-19-07-51-51/citation_hop/src
exec $PY -m citation_hop
