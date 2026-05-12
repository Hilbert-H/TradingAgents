#!/usr/bin/env bash
#
# bin/spawn_opus_analysis.sh — 单只标的 Opus 决策链复盘 派发器
#
# 工作流
# ------
# 对一份原始分析报告（.md），启动一个 claude --print 进程（model=opus），
# 让它扮演 Bull / Bear Researchers、Research Manager、Trader、Risk Debators、
# Portfolio Manager 五个角色，重做决策链条的后半段（六至九部分），并把完整
# 的新版 markdown 报告写到 <原文件名>_Opus.md。
#
# 这个脚本通常由 run_deepseek.py / run_batch.py 在分析师跑完后，判断终评级
# 为 Buy / Overweight 时自动调起；也可以单独手动调用。
#
# 用法
# ----
#   bin/spawn_opus_analysis.sh <path_to_report.md>           # 后台异步
#   bin/spawn_opus_analysis.sh --wait <path_to_report.md>    # 前台同步（调试用）
#
# 退出码
# ------
#   0  已成功派发后台 claude（或同步运行成功）
#   1  前置条件失败：参数 / claude / 模板缺失
#   2  目标已经有 _Opus.md（跳过，不覆盖）
#
# 关联文件
# --------
#   .prompts/opus_decision_chain.md   prompt 模板（含 {{REPORT_FILE}} /
#                                     {{REPORT_CONTENT}} / {{OUTPUT_FILE}} 占位符）

set -euo pipefail

# === DATA SCHEMA ===
#
# 全局配置（顶级 ALL_CAPS）：
#   REPO_ROOT             : string  仓库根绝对路径
#   PROMPT_TEMPLATE       : string  prompt 模板文件路径
#   LOG_DIR               : string  Opus claude 进程的日志目录
#   PROMPT_STAGING_DIR    : string  渲染后 prompt 落盘的临时目录(异步模式下子进程需要读)
#   OPUS_MODEL            : string  claude --model 参数（"opus" 或 "claude-opus-4-7"）
#
# 每次调用派生：
#   md_file               : string  绝对路径，传入的原始分析报告
#   out_file              : string  ${md_file%.md}_Opus.md
#   prompt_file           : string  渲染后 prompt 的落盘路径（PROMPT_STAGING_DIR 下）
#   proxy_port            : int     ClashX Meta mixed-port，从配置自动探测
#   log_file              : string  本次调用的日志文件

# ---- 配置常量 ----
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
PROMPT_TEMPLATE="${REPO_ROOT}/.prompts/opus_decision_chain.md"
LOG_DIR="${REPO_ROOT}/.opus_logs"
PROMPT_STAGING_DIR="${REPO_ROOT}/.opus_logs/_prompts"
OPUS_MODEL="${TRADINGAGENTS_OPUS_MODEL:-opus}"   # 允许通过 env 覆盖到具体版本

# ---- 参数解析 ----
WAIT_MODE=0
if [[ "${1:-}" == "--wait" ]]; then
  WAIT_MODE=1
  shift
fi

if [[ $# -ne 1 ]]; then
  echo "用法: $0 [--wait] <path_to_report.md>" >&2
  exit 1
fi

md_file_raw="$1"
# 解析为绝对路径（macOS bash 3.2 没有 realpath -m，用 python3 兜底）
md_file="$(python3 -c 'import os, sys; print(os.path.abspath(sys.argv[1]))' "$md_file_raw")"

# ---- 前置检查 ----
[[ -f "$md_file" ]] || { echo "❌ 报告文件不存在: $md_file" >&2; exit 1; }
[[ -f "$PROMPT_TEMPLATE" ]] || { echo "❌ prompt 模板不存在: $PROMPT_TEMPLATE" >&2; exit 1; }
command -v claude  >/dev/null || { echo "❌ 需要 claude CLI" >&2; exit 1; }
command -v python3 >/dev/null || { echo "❌ 需要 python3" >&2; exit 1; }

# 输出路径：xxxx.md → xxxx_Opus.md（同目录）
out_file="${md_file%.md}_Opus.md"
if [[ -f "$out_file" ]]; then
  echo "⏭️  已存在，跳过: $out_file"
  exit 2
fi

mkdir -p "$LOG_DIR" "$PROMPT_STAGING_DIR"
basename_no_ext="$(basename "${md_file%.md}")"
timestamp="$(date +%Y%m%d_%H%M%S)"
log_file="${LOG_DIR}/${basename_no_ext}.${timestamp}.log"
prompt_file="${PROMPT_STAGING_DIR}/${basename_no_ext}.${timestamp}.prompt.md"

# ---- ClashX Meta mixed-port 自动探测 ----
# 历史教训：早期实现只读 ClashX cacheConfigs/*.yaml 里的 mixed-port，但
#   1) cacheConfigs 目录里可能有多个 yaml，文件名 UUID 不带 mtime，glob 顺序不稳定，
#      可能选中已 stale 的旧 yaml（实测：旧 yaml 写 7893，新 yaml 写 7894）；
#   2) 调用方（run_batch.py worker）启动时 strip 了 http_proxy 环境变量，所以也读不到
#      用户 shell 里实际生效的 7894。
# 因此重写为「先收集候选，再 TCP 探活，选第一个能连上的」。
port_is_alive() {
  local port="$1"
  # macOS nc 没有 -w 时直连阻塞；--zero -G 1 用 1s 超时
  nc -z -G 1 127.0.0.1 "$port" >/dev/null 2>&1
}

# 收集所有候选端口（顺序 = 优先级），去重但保序
collect_candidate_ports() {
  local candidates=()
  # 1) 当前 shell 的 http_proxy（如果合法）— 用户最权威的信息
  if [[ "${http_proxy:-}" =~ ^https?://127\.0\.0\.1:([0-9]+)$ ]]; then
    candidates+=("${BASH_REMATCH[1]}")
  fi
  if [[ "${HTTP_PROXY:-}" =~ ^https?://127\.0\.0\.1:([0-9]+)$ ]]; then
    candidates+=("${BASH_REMATCH[1]}")
  fi
  # 2) 从 ps 抠出 ClashX 正在用的 yaml 路径，读那个 yaml 的 mixed-port
  local active_yaml
  active_yaml="$(ps -ef 2>/dev/null | grep -iE "clash" | grep -v grep \
                 | grep -oE "/[^ ]+\.yaml" | head -1)"
  if [[ -n "$active_yaml" && -f "$active_yaml" ]]; then
    local p
    p="$(awk '/^mixed-port:/{print $2; exit}' "$active_yaml")"
    [[ -n "$p" ]] && candidates+=("$p")
  fi
  # 3) cacheConfigs 下按 mtime 倒序的所有 yaml — 兜底
  if [[ -d "$HOME/Library/Caches/com.MetaCubeX.ClashX.meta/cacheConfigs" ]]; then
    while IFS= read -r y; do
      local p
      p="$(awk '/^mixed-port:/{print $2; exit}' "$y")"
      [[ -n "$p" ]] && candidates+=("$p")
    done < <(ls -t "$HOME/Library/Caches/com.MetaCubeX.ClashX.meta/cacheConfigs/"*.yaml 2>/dev/null)
  fi
  # 4) Clash 常见默认端口
  candidates+=(7890 7891 7893 7894 7897 7898 1087 8080)
  # 去重保序
  printf '%s\n' "${candidates[@]}" | awk '!seen[$0]++'
}

detect_proxy_port() {
  local port
  for port in $(collect_candidate_ports); do
    if port_is_alive "$port"; then
      echo "$port"
      return 0
    fi
  done
  # 没找到 — 让调用方决定怎么处理（这里返回非 0 让上层报错）
  return 1
}

if ! proxy_port="$(detect_proxy_port)"; then
  echo "❌ 未探测到任何可用的本地代理端口（7890-7898/1087/8080 全部 dead）" >&2
  echo "   ClashX 是否运行？请用 \`netstat -an -p tcp | grep LISTEN\` 检查实际监听端口。" >&2
  exit 1
fi

# ---- 渲染 prompt 到 prompt_file ----
# sed 不适合大文件正文替换（特殊字符冲突），用 python3 做安全的字符串替换。
python3 - "$PROMPT_TEMPLATE" "$md_file" "$out_file" "$prompt_file" <<'PY'
import pathlib
import sys

template_path, report_path, output_path, prompt_path = sys.argv[1:5]
template = pathlib.Path(template_path).read_text(encoding="utf-8")
report_content = pathlib.Path(report_path).read_text(encoding="utf-8")

# 占位符替换:先替换短字符串(避免被 REPORT_CONTENT 里偶然出现的占位符再次匹配),
# 再注入 REPORT_CONTENT 大段正文。
rendered = template.replace("{{REPORT_FILE}}", report_path)
rendered = rendered.replace("{{OUTPUT_FILE}}", output_path)
rendered = rendered.replace("{{REPORT_CONTENT}}", report_content)
pathlib.Path(prompt_path).write_text(rendered, encoding="utf-8")
PY

# ---- 写一个自包含的 launcher 脚本 ----
# 把所有运行时变量内嵌成字面量（避免后台脱离父 shell 后丢失上下文）。
# 这与 spawn-refactor.sh 的 write_launcher 思路一致。
launcher_file="${PROMPT_STAGING_DIR}/${basename_no_ext}.${timestamp}.launch.sh"
cat > "$launcher_file" <<EOF
#!/usr/bin/env bash
# 自动生成的一次性 launcher，运行完即可删除

set -uo pipefail

# 注入代理（ClashX Meta mixed-port = ${proxy_port}，spawn 时探测）
# Anthropic API 域名对 CN IP region 有限制，必须走代理（否则 403）
export http_proxy="http://127.0.0.1:${proxy_port}"
export https_proxy="http://127.0.0.1:${proxy_port}"
export all_proxy="socks5://127.0.0.1:${proxy_port}"
unset HTTP_PROXY HTTPS_PROXY ALL_PROXY

PROMPT_FILE='${prompt_file}'
MD_FILE='${md_file}'
OUT_FILE='${out_file}'

# claude 选项语义：
#   --print                            非交互模式;运行完即退出
#   --model '${OPUS_MODEL}'            强制使用 Opus
#   --add-dir "\$(dirname ...)"         允许工具访问报告所在目录(用于 Write _Opus.md)
#   --strict-mcp-config                跳过项目 .mcp.json 加载(无 MCP trust 对话)
#   --permission-mode bypassPermissions
#                                      跳过权限确认(自动派发,无人交互)
#   --allowedTools "Read Write Edit"   只允许文件读写,本任务不需要 Bash/网络
#   --output-format text               简洁文本输出,便于 grep
claude --print \\
       --model '${OPUS_MODEL}' \\
       --add-dir "\$(dirname "\$MD_FILE")" \\
       --strict-mcp-config \\
       --permission-mode bypassPermissions \\
       --allowedTools "Read Write Edit" \\
       --output-format text \\
       < "\$PROMPT_FILE"
claude_rc=\$?

if [[ -f "\$OUT_FILE" ]]; then
  echo "[done] \$(date +%H:%M:%S) ✅ Opus 报告: \$OUT_FILE (claude_rc=\$claude_rc)"
else
  echo "[done] \$(date +%H:%M:%S) ⚠️ claude 已退出但未生成 \$OUT_FILE (claude_rc=\$claude_rc)"
fi

# 清理 prompt 文件（保留 launcher + log 用于事后排查）
rm -f "\$PROMPT_FILE"
EOF
chmod +x "$launcher_file"

echo "▶ Opus 复盘派发: $(basename "$md_file") → $(basename "$out_file")"
echo "  proxy_port=$proxy_port  model=$OPUS_MODEL"
echo "  launcher=$launcher_file"
echo "  log=$log_file"

if [[ $WAIT_MODE -eq 1 ]]; then
  # 同步模式（调试）：当前 shell 等待 launcher 完成，输出同时落 log + tty
  bash "$launcher_file" 2>&1 | tee "$log_file"
  if [[ -f "$out_file" ]]; then
    echo "✅ Opus 报告已生成: $out_file"
  else
    echo "⚠️  claude 已退出,但未生成 $out_file,请查看 $log_file" >&2
    exit 3
  fi
else
  # 异步模式（默认）：nohup 把 launcher 完全脱离父进程，不阻塞调用方
  nohup bash "$launcher_file" >"$log_file" 2>&1 &
  disown
  echo "  PID=$! (后台运行)"
fi
