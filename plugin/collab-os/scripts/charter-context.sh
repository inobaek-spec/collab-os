#!/bin/sh
# collab-os v0.1.4 - SessionStart hook: tells the session where the charter and logs are.
#
# LIMITS (read first)
#  - Never blocks: every path ends in exit 0. A bug here must not stop a session.
#  - Prints pointers only. The charter text itself is NOT loaded; the model must open it.
#  - Config lookup mirrors tools/_config.py (COLLAB_OS_CONFIG -> ~/.config/collab-os/config.json
#    -> built-in defaults), but this copy reads only flat "key": "value" pairs with no escaped
#    quotes inside values. Keep the two in step by hand when either changes.
#  - It does not check that any file other than the charter exists.

home="${HOME:-}"
note=""
cfg=""
if [ -n "${COLLAB_OS_CONFIG:-}" ]; then
  case "$COLLAB_OS_CONFIG" in
    "~/"*) cand="$home/${COLLAB_OS_CONFIG#\~/}" ;;
    /*) cand="$COLLAB_OS_CONFIG" ;;
    *) cand="$(pwd)/$COLLAB_OS_CONFIG" ;;
  esac
  if [ -f "$cand" ]; then
    cfg="$cand"
  else
    note="COLLAB_OS_CONFIG is set but the file does not exist: $cand - using built-in defaults"
  fi
elif [ -f "$home/.config/collab-os/config.json" ]; then
  cfg="$home/.config/collab-os/config.json"
else
  note="no config file - using built-in defaults"
fi

# read_key KEY -> prints the raw string value of "KEY": "value" from $cfg (empty if absent)
read_key() {
  [ -n "$cfg" ] || return 0
  tr '\n' ' ' < "$cfg" 2>/dev/null \
    | sed -n "s/.*\"$1\"[[:space:]]*:[[:space:]]*\"\\([^\"]*\\)\".*/\\1/p" | head -n 1
}

# resolve VALUE DEFAULT -> absolute path (file values relative to the config folder,
# defaults relative to HOME), with ~ expanded
resolve() {
  v="$1"
  if [ -n "$v" ]; then base="$(dirname "$cfg")"; else v="$2"; base="$home"; fi
  case "$v" in
    "~") printf '%s' "$home" ;;
    "~/"*) printf '%s/%s' "$home" "${v#\~/}" ;;
    /*) printf '%s' "$v" ;;
    *) printf '%s/%s' "$base" "$v" ;;
  esac
}

CHARTER="$(resolve "$(read_key charter)" "~/.config/collab-os/CHARTER.md")"
GATE_LOG="$(resolve "$(read_key gate_log)" "~/.config/collab-os/GATE-LOG.md")"
CLAIM_LOG="$(resolve "$(read_key claim_log)" "~/.config/collab-os/CLAIM-LOG.md")"
HANDOFF_DIR="$(resolve "$(read_key handoff_dir)" "~/.config/collab-os/handoffs")"
LANG_SET="$(read_key lang)"
[ "$LANG_SET" = "ko" ] || LANG_SET="en"

[ -n "$note" ] && printf '[collab-os] %s\n' "$note"

if [ ! -f "$CHARTER" ]; then
  if [ "$LANG_SET" = "ko" ]; then
    printf '[collab-os] 헌장 파일을 찾지 못했다: %s\n' "$CHARTER"
    printf '규칙이 적재되지 않은 상태다. 규칙에 기대어 단정하지 말고 사용자에게 알린다. /collab-os:gate 는 5칸 축소판으로 쓸 수 있다.\n'
  else
    printf '[collab-os] Charter not found: %s\n' "$CHARTER"
    printf 'Rules are NOT loaded. Do not assert anything on the strength of the rules; tell the user. /collab-os:gate still works in its reduced 5-box form.\n'
  fi
  exit 0
fi

if [ "$LANG_SET" = "ko" ]; then
cat <<EOF
[collab-os] 헌장 경로를 알린다. 본문은 아직 읽히지 않았다 - 규칙 판단이 걸리면 직접 열어 읽는다.
- 헌장(규칙의 뿌리): $CHARTER - 충돌하면 헌장이 이긴다. 고치는 것은 사람만.
- 착수 게이트: 단건 질문·작고 되돌릴 수 있는 한 파일 수정이 아니면 본 작업 전에 판정 카드 5칸을 내고 멈춘다.
  가동 중인 시스템·사람·돈·외부에 닿으면 파일 수와 상관없이 낸다. 애매하면 낸다. -> /collab-os:gate
- 착수 전 정찰: 만들기·옮기기·"없다/있다" 단정·문서만 보고 설계하기 전에 실물을 확인하고 결과를 먼저 보인다. -> /collab-os:recon
- 표면·모델 전환: 인계 블록을 먼저 내고 파일로 남긴다($HANDOFF_DIR). -> /collab-os:handoff
- 게이트 예외(안 걸림·과하게 걸림·잘못 판정)만 기록: $GATE_LOG
- 틀린 주장 장부: $CLAIM_LOG
승인이 없으면 기본값은 '안 한다'. 직접 확인한 사실은 [observed 날짜], 사람이 말한 것만 [stated].
EOF
else
cat <<EOF
[collab-os] Charter location announced. Its text is NOT loaded yet - open it yourself when a rule decision comes up.
- Charter (root of all rules): $CHARTER - it wins any conflict. Only the human edits it.
- Launch gate: unless the task is a single question or a small, reversible one-file edit, issue a 5-box decision card and stop before the real work.
  Anything that touches a running system, people, money or the outside world needs a card regardless of file count. When unsure, issue one. -> /collab-os:gate
- Recon: before creating, moving, asserting "it exists / it does not exist", or designing from documents alone, check the real thing and show the result first. -> /collab-os:recon
- Surface or model switch: print a handoff block first and save it as a file ($HANDOFF_DIR). -> /collab-os:handoff
- Gate exceptions only (missed, over-fired, misjudged): $GATE_LOG
- Claim-error ledger: $CLAIM_LOG
Without approval the default is "don't". Mark what you checked yourself as [observed DATE] and what the human told you as [stated].
EOF
fi

exit 0
