내가 압축을 푼 폴더에서 collab-os 플러그인을 설치해 줘. 아래 단계를 순서대로 하고, 단계마다 결과를 한 줄로 알려 줘.

이 작업 전체의 규칙:
- 쓰는 곳은 collab-os 폴더, ~/.config/collab-os/, 그리고 `claude plugin` 명령이 스스로 쓰는 것뿐이다: ~/.claude/plugins/, ~/.claude/settings.json 의 extraKnownMarketplaces·enabledPlugins 항목, Claude Code 자체 상태 파일 ~/.claude.json(과 ~/.claude/backups/ 아래 백업). Claude Code 자체 파일(settings.json, .claude.json)은 직접 고치지 않는다. 그 밖에는 아무것도 쓰지 않는다.
- rm 금지, sudo 금지, 이미 있는 파일 덮어쓰기 금지(단 하나의 예외: 3단계에서 백업을 만든 뒤, 내가 「예」라고 한 뒤에만 헌장 끝에 덧붙이기), 네트워크 요청 금지.
- 애매하거나 단계가 실패하면 추측하지 말고 멈춰서 나에게 묻는다.

1. 압축을 푼 collab-os 폴더의 절대경로를 찾는다. 현재 폴더, 그 하위 폴더, 상위 폴더에서 plugin/.claude-plugin/marketplace.json 을 찾아 하나씩 열어 보고, "name" 이 정확히 collab-os 인 것만 후보로 남긴다. 그 plugin 폴더를 담고 있는 폴더가 collab-os 폴더다 — 이름은 collab-os, collab-os-main(GitHub 의 Download ZIP), 압축 도구가 줄인 이름(예 collab-os-v0) 등 무엇이든 된다. 현재 폴더가 바로 그 폴더일 수도 있다. 정확히 하나를 찾지 못하면 나에게 경로를 묻고 멈춘다.
2. `claude plugin marketplace list` 를 먼저 실행한다. 목록에 collab-os 라는 마켓플레이스가 이미 있고 바로 이 폴더를 가리키면 그냥 계속한다. 다른 곳을 가리키면 두 경로를 보여 주고 바꿀지 묻는다 — 내가 「예」라고 할 때만 `claude plugin marketplace remove collab-os` 를 실행하되, 그 전에 이 명령이 거기서 설치된 플러그인도 함께 지우고 그 플러그인의 저장 옵션·비밀·데이터도 삭제한다는 것을 알려 준다. 어느 쪽이든 그다음 `claude plugin marketplace add <그 절대경로>/plugin` → `claude plugin install collab-os@collab-os` → `claude plugin list` 를 차례로 실행한다. 목록에 collab-os@collab-os 와 Status: ✔ enabled 가 함께 보이지 않으면 출력을 그대로 보여 주고 멈춘다.
3. 플러그인과 같은 방법으로 활성 헌장 경로를 찾는다. 환경 변수 COLLAB_OS_CONFIG 가 빈 값이 아닌 값으로 설정돼 있으면 그 변수가 가리키는 JSON 파일의 "charter" 값이 경로다 — 그 파일이 없거나 비었거나 올바른 JSON 이 아니면 기본 경로 ~/.config/collab-os/CHARTER.md 다(config.json 으로 넘어가지 않는다 — 어느 경우였는지 말한다). 변수가 없거나 빈 값이고 ~/.config/collab-os/config.json 이 있고 올바른 JSON 이면 그 파일의 "charter" 값이 경로다. 그 밖의 경우와 "charter" 키가 없거나 비었거나 문자열이 아닌 경우는 ~/.config/collab-os/CHARTER.md 다. 상대 경로는 설정 파일이 있는 폴더 기준이고 ~ 는 홈이다. 그다음: 그 경로에 파일이 없으면 `mkdir -p ~/.config/collab-os`(기본 경로가 아니면 그 경로의 폴더 만들기)를 실행하고 BEGIN 과 END 사이의 글을 그대로 담은 파일을 만든다. 파일이 있으면 아직 건드리지 말고, 그 파일의 `## I<숫자>.` 제목을 BEGIN~END 의 네 조항(I2·I3·I4·I8)과 비교해 경로, 빠진 제목, 덧붙일 글 전문, 백업 이름 <그 경로>.bak-<YYYYMMDD-HHMMSS>, 복원 명령 cp "<백업>" "<그 경로>" 를 보여 주고 나에게 묻는다. 내가 「예」라고 할 때만: 먼저 그 이름으로 백업 사본을 만들고, 빠진 조항을 파일 끝에 덧붙인다 — 덮어쓰기·순서 바꾸기·있는 글 고치기는 하지 않는다. 빠진 것이 없으면 그렇다고 말하고 계속한다. `## I<숫자>.` 제목이 하나도 없는 파일(자기 형식의 헌장)이면 네 조항을 보여 주고 무엇이 빠졌는지 알 수 없다고 말한 뒤 같은 방식으로 묻는다 — 「아니오」면 그대로 둔다.
BEGIN
# My charter

## I2. Reality is the source of truth
The actual state beats documents, memory and guesses - meaning what the running
code path really reads. If you cannot check, do not assert.

## I3. No completion without verification
"Done" only after running and checking the real thing. Irreversible or structural
changes get a second pair of eyes that did not share the context that produced them.

## I4. Irreversible things belong to the human
Anything whose effect reaches people, money, live services or running systems
happens only after my approval. Without approval, the default is "don't".
Editing a file that a running process reads is a deployment.

## I8. Start from purpose
Decide what "finished" means before choosing a tool or a method. Purpose is the
reference point for the whole task: changing track is allowed, drifting without
knowing is not - a change of track is a declared decision. Finished means the
purpose is met, not that an artifact exists.
END
4. 이렇게 안내한다: "이 세션을 끝내고 터미널에서 claude 를 다시 실행하세요. 새 세션에서 'what did the collab-os hook tell you?' 라고 물어보세요. 어시스턴트가 헌장 위치를 답하면 된 것입니다."
5. 새 세션에서 게이트를 써 볼 예시 작업 하나를 제안하고 — 예: `/collab-os:gate set up a nightly job that emails me a disk-usage report` — 멈춘다. 그 작업을 실제로 하지는 않는다.
