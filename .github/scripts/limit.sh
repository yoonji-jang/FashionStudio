#!/usr/bin/env bash
# Claude 단계가 구독 사용량 한도("You've hit your session limit · resets 9am (UTC)")로 멈췄으면
# daily-results 브랜치 limits/<워크플로>-<실행 번호>.json 에 다시 돌릴 입력을 남긴다.
# 한도가 풀린 뒤 schedule(tools.schedule retry_limited)이 같은 입력으로 한 번 다시 부른다.
# 환경 변수: WORKFLOW (워크플로 파일 이름), INPUTS (JSON), KEY (같은 일 묶음), RUN_DIR (daily 결과 폴더, 선택)
# 한도로 멈췄으면 GITHUB_ENV 에 LIMITED=1 을 남겨 실패 알림이 다시 시작한다고 알린다.
f="${RUNNER_TEMP:-/home/runner/work/_temp}/claude-execution-output.json"
[ -f "$f" ] || exit 0
msg=$(jq -r '.[] | (select(.type=="result") | .result // empty),
  (select(.type=="assistant") | .message.content[]? | select(.type=="text") | .text)' "$f" 2>/dev/null \
  | grep -iE "hit your .*limit" | tail -1 || true)
[ -n "$msg" ] || exit 0
echo "Claude 사용량 한도: $msg"
echo "LIMITED=1" >> "$GITHUB_ENV"
[ "$GITHUB_EVENT_NAME" = workflow_dispatch ] || exit 0  # 입력을 알 수 있을 때만 다시 돌린다

dir=$(mktemp -d)
git fetch -q origin daily-results && git worktree add -q --detach "$dir" FETCH_HEAD || exit 0
mkdir -p "$dir/limits"
inputs="${INPUTS:-}"; [ -n "$inputs" ] || inputs='{}'
jq -n --arg workflow "$WORKFLOW" --argjson inputs "$inputs" --arg key "${KEY:-$WORKFLOW}" \
  --arg run "${RUN_DIR:-}" --arg message "$msg" --argjson at "$(date +%s)" \
  '{workflow: $workflow, inputs: $inputs, key: $key, run: $run, message: $message, at: $at}' \
  > "$dir/limits/$WORKFLOW-$GITHUB_RUN_ID.json"
git -C "$dir" add limits
git -C "$dir" -c user.name="github-actions[bot]" \
  -c user.email="41898279+github-actions[bot]@users.noreply.github.com" commit -qm "사용량 한도 기록 $WORKFLOW"
for i in 1 2 3; do
  git -C "$dir" fetch -q origin daily-results && git -C "$dir" rebase -q FETCH_HEAD \
    && git -C "$dir" push -q origin HEAD:daily-results && break
  sleep $((i * 5))
done
git worktree remove --force "$dir" || true
exit 0
