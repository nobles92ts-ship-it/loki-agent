# Loki

[![CI](https://github.com/nobles92ts-ship-it/loki-agent/actions/workflows/ci.yml/badge.svg)](https://github.com/nobles92ts-ship-it/loki-agent/actions/workflows/ci.yml)

**내 PC와 대화하기.** Loki는 Slack이나 Discord를 내 컴퓨터에서 도는 [Claude Code](https://claude.com/claude-code)에 연결해주는 작은 로컬 에이전트다 — 이미 쓰고 있는 Claude 구독으로 돌아간다.

API 키 없음. 건바이건 과금 없음. 내 파일, 내 셸, 내 Claude — 폰에서 닿는다.

```
Slack 또는 Discord — DM / @멘션
        │  아웃바운드 웹소켓 (공개 URL 불필요)
        ▼
  Loki (이 레포, 내 PC에서 실행)
        │  공식 CLI 실행:  claude -p
        ▼
  Claude Code  ──  WORK_DIR에서 파일 읽기/쓰기·명령 실행
        │
        ▼
  결과가 스레드로 돌아옴
```

## 왜 Loki인가

- **구독으로 구동** — 두뇌가 공식 `claude` CLI(내 Pro/Max 로그인). `sk-…` 키도, 월말 종량 청구서도 없다.
- **진짜 로컬** — 폴더 요약, 스크립트 수정, 빌드 실행까지 *내 PC에서*. 권한 수위는 내가 정한다.
- **게스트는 읽기전용** — 채널에서 누구나 `@Loki` 가능하지만, 게스트 호출은 코드 레벨에서 읽기전용 강제. 쓰기/실행은 오너 DM만.
- **맥락 인지** — 스레드에서 부르면 스레드를, 채널에서 그냥 부르면 최근 채널 대화를 읽는다. 모든 맥락은 *지시가 아닌 데이터*로 감싼다(인젝션 가드). 멘션조차 생략하고 싶으면 `!listen`으로 스레드/채널을 자동청취 존으로.
- **대화를 기억함** — DM과 스레드는 각자의 Claude 세션을 유지한다. "이번엔 저쪽 폴더도 똑같이 해줘"가 그냥 통한다. `SESSION_IDLE_MIN`(기본 2시간)만큼 조용하면 자동으로 리셋되고, `!new`로 즉시 리셋할 수도 있다.
- **Slack *과* Discord** — 같은 봇, 같은 권한, 같은 명령. `loki/core`는 플랫폼 무관이고 어댑터는 얇다([로드맵](#로드맵)).

## 빠른 시작

**전제조건**
- Windows 10/11, macOS, 또는 Linux · Python 3.10+
- [Claude Code](https://claude.com/claude-code) 설치+로그인(터미널에서 `claude`가 됨), Pro/Max 구독
- Slack 워크스페이스 앱 생성 권한 (또는 관리 권한이 있는 Discord 서버)

**1. Slack 앱 만들기 (≈2분)** — *Discord로 쓸 거면 [SETUP §1b](docs/SETUP.md#1b-create-the-discord-app-alternative-to-slack) 보고 2번으로*
1. <https://api.slack.com/apps> → **Create New App** → **From an app manifest**
2. 워크스페이스 선택 후 [`loki/platforms/slack/manifest.yaml`](loki/platforms/slack/manifest.yaml) 내용 붙여넣기
3. **Install to Workspace** → **Bot User OAuth Token**(`xoxb-…`) 복사
4. **Basic Information → App-Level Tokens** → `connections:write` 스코프로 생성 → `xapp-…` 복사
5. ⚠️ **App Home 탭 → "Allow users to send Slash commands and messages from the messages tab" 체크** — 안 하면 DM 입력창이 막힌다.

**2. 셋업 & 실행**

Windows:
```powershell
git clone https://github.com/nobles92ts-ship-it/loki-agent.git
cd loki-agent
.\setup.ps1          # 마법사: venv + 의존성 + .env (토큰·내 Slack ID·WORK_DIR)
.\venv\Scripts\python.exe -m loki
```

macOS / Linux:
```bash
git clone https://github.com/nobles92ts-ship-it/loki-agent.git
cd loki-agent
./setup.sh           # 같은 마법사
./venv/bin/python -m loki
```

**3. 테스트** — 봇에게 DM: `안녕`. 첫 응답 ~15–30초.

자동 시작: `.\setup.ps1 -Autostart`(Windows 로그인 런처) · systemd/launchd 예시는 [docs/SETUP.md](docs/SETUP.md).
상세 가이드+트러블슈팅: [docs/SETUP.md](docs/SETUP.md)

**텔레그램이 더 편하다면?** `.env`에 두 줄 — @BotFather 토큰과 내 숫자 ID — 만 넣으면 같은 Loki를 같은 명령으로 쓴다. → [docs/TELEGRAM.md](docs/TELEGRAM.md)

**아니면 항상 켜져 있는 기기에 올려라.** 6시에 닫는 노트북은 6시에 대답을 멈추고 예약을 전부 놓치는 봇이다. `Dockerfile` + `docker-compose.yml`로 NAS나 홈서버에 올릴 수 있고, 컨테이너는 데스크톱 계정보다 오히려 더 좁은 울타리다 — Claude가 마운트한 폴더 밖으로는 못 나가니까. → [docs/DOCKER.md](docs/DOCKER.md)

## 설정 (`.env`)

| 키 | 기본값 | 의미 |
|---|---|---|
| `SLACK_BOT_TOKEN` | — (필수) | `xoxb-…` 봇 토큰 |
| `SLACK_APP_TOKEN` | — (필수) | `xapp-…` 앱 레벨 토큰 (Socket Mode) |
| `ALLOWED_USER_ID` | — (필수) | 내 Slack 멤버 ID. **없으면 부팅 거부(fail-closed).** |
| `WORK_DIR` | — (필수) | Claude가 작업할 디렉토리 |
| `CLAUDE_PERMISSION_MODE` | `plan` | `plan`=읽기전용(기본) · `bypassPermissions`=전체 쓰기/실행 — opt-in, [SECURITY](docs/SECURITY.md) 필독 |
| `CLAUDE_MODEL` | 계정 기본 | 예: `sonnet` (한도 절약) |
| `TIMEOUT_SEC` | `300` | 요청당 타임아웃 |
| `JOB_CONCURRENCY` | `2` | 동시 Claude 작업 수 (같은 대화는 항상 순서 유지) |
| `GUEST_RATE_PER_HOUR` | `10` | 게스트 1인당 시간당 최대 요청 수 (`0`=무제한). 오너는 무제한 |
| `CLAUDE_CONFIG_DIR` | 기본 계정 | Loki에게 전용 Claude 로그인 부여 — [전용 계정](#전용-계정) 참조 |
| `CLAUDE_CODE_OAUTH_TOKEN` | 미설정 | 터미널이 어떤 계정으로 로그인돼 있든 모든 실행을 한 계정으로 고정 — [전용 계정](#전용-계정) 참조 |
| `LOKI_LANG` | `en` | 봇 메시지 언어: `en` / `ko` |
| `LOKI_CHANNEL_CTX_DAYS` / `_MSGS` | `7` / `120` | 채널 멘션이 보는 최근 대화 범위 |
| `CLAUDE_CMD` | 자동탐지 | PATH에 없을 때 claude 전체 경로 |

### 전용 계정

Loki의 두뇌는 `claude` CLI라서 네가 로그인한 계정을 그대로 쓴다. Loki에게 **전용 계정**(예: 개인용과 분리된 회사 계정)을 주려면 전용 config 디렉토리를 가리키면 된다. Claude는 디렉토리별로 `.credentials.json`을 격리하므로(Windows/Linux), 디렉토리마다 독립된 로그인이다:

```powershell
# 1회: 특정 계정을 전용 디렉토리에 로그인
$env:CLAUDE_CONFIG_DIR = "C:\Users\You\.claude-loki"
claude            # /login 실행 → Loki가 쓸 계정 선택
```
그다음 `.env`에 `CLAUDE_CONFIG_DIR=C:\Users\You\.claude-loki`를 넣는다(마법사도 물어봄). 이제 터미널이 어떤 계정을 쓰든 Loki는 그 계정으로 인증한다. 비워두면 기본 로그인을 공유.

대부분은 이걸로 되지만 두 경우가 안 된다. 실행 시점에 `~/.claude/skills`·`~/.claude/agents`를 읽는 파이프라인은 config 디렉토리를 옮기면 툴체인까지 딸려가 버린다. 그리고 **macOS**에서는 자격증명이 디렉토리가 아니라 시스템 키체인에 저장돼서 config 디렉토리로는 계정이 아예 안 나뉜다. 둘 다 **토큰**으로 해결한다 — `claude setup-token`으로 발급해 `.env`에 `CLAUDE_CODE_OAUTH_TOKEN`을 넣으면 된다. 저장된 로그인보다 우선순위가 높아서 config 디렉토리는 그대로 두고 **자격증명만** 대체하고, 환경변수라 플랫폼을 가리지 않는다.

어느 쪽이든 **가정하지 말고 확인해라**. 토큰이 잘못됐거나 만료돼도 오류가 나지 않는다 — `claude`가 저장된 로그인으로 조용히 폴백해 정상 응답한다. 그래서 `python -m loki doctor`는 **빈** config 디렉토리에 대고 검사한다. 폴백할 대상이 없어야 토큰이 혼자 서는지 알 수 있기 때문이다. 절차: [docs/SETUP.md](docs/SETUP.md#optional-pin-the-account-with-a-token)

#### 계정이 둘일 때 — 어느 쪽이 쓰게 할지 전환

고정은 계정을 하나로 못박는 기능이다. 그런데 계정이 **둘**이면(개인 구독 + 회사 계정) "오늘 오후 작업은 어느 쪽으로 돌릴까"가 하루에도 몇 번씩 바뀐다. 그때마다 `.env` 고치고 워커를 재시작하는 건 답이 아니라서, 전환은 명령으로 한다:

```
!account            # 다음 요청이 어느 계정으로 도는지
!account off        # 고정 토큰 무시 — config 디렉토리에 로그인된 계정으로
!account on         # 다시 고정 계정으로
!계정 끄기 / !계정 켜기   # 한글도 됨
```

`.env`의 토큰은 **쓰지도 지우지도 않는다.** 그래서 `on`은 언제나 돌아갈 곳이 있다. `off`는 그저 토큰을 안 넘길 뿐이고, 스폰은 config 디렉토리의 로그인으로 떨어진다 — v1.8 이전과 같은 동작이다. 적용은 **다음 요청부터**이고, 이미 돌고 있는 작업은 시작한 계정으로 끝난다. 전환하면 기억하던 대화는 정리된다. 이어서 돌리면 한쪽 계정의 대화가 다른 쪽 로그인·사용량으로 재생되기 때문이다.

이 기능을 안 건드리는 설치는 아무것도 안 바뀐다 — 상태 파일이 없으면 켜짐이다. `doctor`도 전환 상태를 보고하고, 아무도 안 쓰는 고정을 검사하느라 시간을 쓰지 않는다.

## 권한 — 누가 뭘 할 수 있나

기본 2티어로 깔끔하게 분리돼 있다:

| | **오너** (`ALLOWED_USER_ID`) | **게스트** (Loki가 들어간 채널의 누구나) |
|---|---|---|
| DM | ✅ 설정한 모드 전부 — 읽기·쓰기·명령 실행 | ⛔ 조용히 무시 |
| 채널 `@멘션` | ✅ 오너 모드 | ✅ **읽기전용** + **[게스트 allowlist](#게스트-allowlist-lokimd) 안에서만** |
| 스킬 · 셸 · 서브에이전트 | ✅ (쓰기 모드에서) | ⛔ 도구 차단 (`Skill`·`Bash`·`Task`) — 옆문 없음 |
| 오너 명령 (아래) | ✅ | ⛔ |
| 참조 맥락 | 스레드 / 최근 채널 대화 | 동일 + `loki.md`의 공개범위 안내 |

회사 단위 티어가 필요하다면? 그건 내장돼 있다 — **조직(Organizations)**:

### 조직 — 회사별 조회 범위·명령·rate

여러 회사/팀이 한 Loki를 쓸 때(Slack Connect 공유채널, 워크스페이스에 초대된 외부 인원) 각자에게 전용 티어를 준다. **마크다운 파일 1장 = 조직 1개**(`<WORK_DIR>/loki/orgs/<이름>.md`) — 멤버·바인딩 채널·읽을 수 있는 폴더·허용 `!명령`·rate 한도가 그 안에 다 있고, 사람이 직접 수정해도 다음 요청부터 반영(무재시작), fail-closed.

```
!org create acme                  # loki/orgs/acme.md 생성
# 폴더 열기: 그 파일의 "## Allowed paths" 편집
!org bind acme C0SHARED           # 그 채널 전원 = acme  (채널 안에서 `!org bind acme`도 가능)
!org add acme @앨리스              # 명시 멤버 — 어느 채널에서 불러도 자기 티어 유지
!org allow acme report            # acme에게 !report 파이프라인 허용
```

요청마다 판정: **오너 → 명시 멤버 → 바인딩 채널 → 무소속 게스트**(전역 `loki.md`). 조직이 권한 모드를 바꾸진 않는다 — 멤버도 게스트처럼 읽기전용이고, 다만 전역 공유 대신 *자기 회사* 폴더를 읽고, *허용받은* 명령을 쓰고, *자기* rate 예산을 쓴다(`!usage`에 조직별 집계). 그 이상의 커스텀 배선은 여전히 사설 명령 훅: [docs/EXAMPLES.md](docs/EXAMPLES.md).

### 오너 명령 레퍼런스

| 명령 | 어디서 | 동작 |
|---|---|---|
| `!stop` / `중지` | 어디서든 | **전부 취소** — 대기 작업 제거 + 실행 작업 강제 종료 |
| `!jobs` / `!작업목록` | 어디서든 | 실행·대기 중 작업을 id와 함께 나열 |
| `!cancel <작업id>` / `!취소` | 어디서든 | **하나만** 골라 중단/제거 (id는 `!jobs`에서) |
| `!usage [일수]` / `!사용량` | 어디서든 | 사용량 리포트: 호출 수·성공/실패·총 시간·유저/유형별 (기본 7일) |
| `!schedule …` / `!예약` | DM | 반복/1회 예약 실행 — 아래 참조 |
| `!learn <메모>` / `!학습` | DM | 학습 인박스에 기록 (`state/learnings.md`) |
| `!new` / `!새대화` / `!리셋` | 아무 데나 | 이 대화의 맥락을 버리고 새 Claude 세션 시작 |
| `!block <채널ID>` / `!차단` | DM | 그 채널에서 게스트 사용 차단 (영구 저장) |
| `!unblock <채널ID>` / `!차단해제` | DM | 차단 해제 |
| `!summary <채널ID>` / `!채널요약` | DM | 그 채널에 안 가고 최근 대화 요약 받기 |
| `!listen` / `!청취` | 스레드/채널 | 자동청취 존 등록: 스레드에서 치면 그 스레드, 채널 최상위에서 치면 채널 전체 — 이후 **멘션 없이** 응답 |
| `!unlisten` / `!청취해제` | 스레드/채널 | 자동청취 해제 (좁은 존부터) |
| `!listening` / `!청취목록` | 어디서든 | 자동청취 중인 존 목록 |
| `!org …` / `!조직 …` | 어디서든 | [조직](#조직--회사별-조회-범위명령rate) 관리: `create` `list` `info` `add` `remove` `bind` `unbind` `allow` `deny` |
| `!plugins` / `!플러그인` | 어디서든 | 직접 설치한 명령 목록 — [docs/PLUGINS.md](docs/PLUGINS.md) |
| `!check <항목들>` / `!체크` | 어디서든 | [공유 체크리스트](#체크리스트) — 한 줄에 한 항목(쉼표 구분도 OK), 첫 줄이 `:`로 끝나면 제목. ☐/☑ 눌러 토글(모두에게 동기화) 또는 `완료 N`. 오너가 생성, 보는 사람 누구나 토글 |
| `!send <경로>` / `!전송` | 어디서든 | 이 대화로 파일 업로드 — `WORK_DIR` 기준 상대경로·그 안의 절대경로·글롭(`reports/*.pdf`). 오너 전용이며 조직에 위임 불가 |
| `!alias …` / `!별칭 …` | 어디서든 | [프롬프트 별칭](#별칭--코드-없이-만드는-내-명령) 관리: `list` `add <이름> <프롬프트>` `remove <이름>` |
| `!budget …` / `!예산 …` | DM | [사용량 예산](#예산--구독을-지키는-상한): 한도·모드·완화 조치 |
| `!account [on\|off]` / `!계정` | DM | 스폰이 어느 Claude 계정으로 도는지 — 고정 토큰 vs config 디렉토리 로그인. [계정이 둘일 때](#계정이-둘일-때--어느-쪽이-쓰게-할지-전환) |
| `!bot …` / `!봇 …` | 어디서든 | [봇 트리거](#봇-트리거--알림이-loki를-깨우게-하기) (Slack): `seen` `allow <B…>` `deny <B…>` `list` |

**자동청취 존** — 작업 스레드에서 매번 @멘션하기 귀찮다면, 그 스레드에서 `@Loki !listen` 한 번이면 이후 거기 있는 모두가 멘션 없이 Loki랑 대화한다(그룹 DM 느낌). 권한은 그대로: 게스트는 여전히 읽기전용+rate limit, `!block`이 존보다 우선, 멘션 메시지는 이중응답 없이 한 번만, 봇 메시지는 무시(루프 방지). 주의 — 존 안에선 **모든** 사람 메시지가 Claude 호출이 되니, 바쁜 채널보단 작업 스레드에 추천.

> `message.channels` + `message.groups` 봇 이벤트가 필요하다 (새 OAuth 스코프는 없음). 이 레포 매니페스트로 만든 앱엔 이미 포함돼 있고, v1.5.0 이전에 설치했다면 앱 설정 **Event Subscriptions → Subscribe to bot events**에서 두 이벤트만 추가하면 된다 — 재설치 프롬프트 없음.

**스케줄러** — Loki가 능동형이 된다: DM에서 프롬프트를 예약하면 결과가 그 DM으로 돌아온다. *내* 권한 모드로 실행되고, 시간은 PC 로컬 기준. PC가 꺼져 있었으면 반복 예약은 다음 슬롯으로 건너뛰고(밀린 것 몰아서 실행 안 함), 놓친 `once`는 부팅 때 바로 실행된다.

```
!schedule daily 09:00 어제 git log 요약해줘
!schedule weekly fri 17:30 이번 주 메모로 주간보고 초안 써줘
!schedule once 2026-12-24 18:00 일찍 마무리하라고 리마인드
!schedule list · !schedule remove s1
```

### 별칭 — 코드 없이 만드는 내 명령

매번 다시 타이핑하는 프롬프트를 명령으로 굳힌다. **마크다운 파일 1장**(`<WORK_DIR>/loki/aliases.md`)에 전부 들어가고, 사람이 직접 수정해도 다음 요청부터 반영된다:

```markdown
## Aliases
- 스탠드업: WORK_DIR의 어제 커밋을 프로젝트별로 묶어서 요약해줘
- 리뷰: {args} 를 리뷰하고 위험한 변경을 짚어줘
```

```
!alias add 스탠드업 어제 커밋 요약해줘     # 파일을 직접 편집해도 된다
!스탠드업                                  # 저장된 프롬프트 실행
!리뷰 PR 412                               # {args} → "PR 412"
```

`{args}` 자리에 인자가 들어가고, 없으면 뒤에 붙는다. 별칭은 *프롬프트*지 새 권한이 아니다 — 그 문장을 직접 친 것과 똑같이 동작하므로 큐·throttle·게스트 범위가 그대로 적용된다. 오너는 모든 별칭을, 게스트는 소속 조직이 허용받은 것만 실행할 수 있다(`!org allow acme 스탠드업`). 프롬프트가 아니라 실제 코드가 필요한 파이프라인은 여전히 private-command 훅이 답이다 — [docs/EXAMPLES.md](docs/EXAMPLES.md) 참고.

별칭은 **가장 마지막에** 매칭된다. 그래서 이미 어떤 명령이 받는 이름으로 만들면 저장은 되고 영영 안 뜬다. 이걸 "기본 명령 목록"으로 검사하면 네 `plugins/`나 포크가 직접 만든 명령은 안 보인다 — 그래서 Loki는 **살아 있는 디스패치에 직접 물어보고** 거부하면서 누가 그 이름을 쥐고 있는지 알려준다. 별칭을 만든 *뒤에* 플러그인이 그 이름을 가져간 경우는 `!alias list`에 표시된다. 그 시점엔 이름을 바꾸는 것 말고는 살릴 방법이 없기 때문이다.

### 예산 — 구독을 지키는 상한

`GUEST_RATE_PER_HOUR`가 한 사람의 도배를 막는다면, **예산**은 전원이 조용히 한 달치를 갉아먹는 걸 막는다. 일간·주간 총량(그리고 조직별 총량)을 걸면 한도 도달 시 Loki가 *게스트*를 거절한다 — **너는 절대 안 막힌다**, throttle과 같은 원칙이다.

```
!budget                     # 현재 상태
!budget daily 60            # 하루 60회, 전원 합산 (0 = 해제)
!budget weekly 300          # 롤링 7일
!budget org acme 20         # 그 회사 몫의 일일 한도
!budget mode auto           # 기본은 manual — 아래 참고
```

한도에 가까워지면(80%, 그리고 100%) Loki가 DM을 보낸다. 그다음이 네가 고르는 부분이다:

- **`manual` (기본)** — Loki가 물어보고 기다린다. 알림에 원탭 버튼이 붙는다: *sonnet으로 전환*, *게스트 일시정지*, *오늘은 무시*. 네가 누르기 전까진 아무것도 안 바뀐다 (Interactivity를 안 켰다면 `!budget sonnet` / `pause` / `resume` / `default` 텍스트 명령도 된다).
- **`auto`** — 임계치를 넘는 순간 Loki가 알아서 가벼운 모델로 고정하고, 그렇게 했다고 알려준다.

어느 쪽이든 100%에서 한도는 그대로 작동한다 — 창이 리셋될 때까지 게스트는 거절된다. 조직 한도는 그 조직에만 적용되므로, 한 회사가 다 써도 다른 회사는 멀쩡하다.

### 봇 트리거 — 알림이 Loki를 깨우게 하기

Loki는 기본적으로 모든 봇 메시지를 무시한다 — 그게 봇끼리 무한 루프가 안 생기는 이유다. 특정 봇을 허용목록에 넣으면 **자동청취 존 안에서** Loki를 깨울 수 있고, 알림 하나가 곧바로 조사로 이어진다:

```
!listen                     # CI가 글 올리는 채널에서
!bot seen                   # → ◻️ B01ABC2DEF — CircleCI
!bot allow B01ABC2DEF
```

이제 빌드 실패 알림이 뜨면 Loki가 읽는다. 옵트인이 두 번(존 + 허용목록) 필요하고, 그 주변 보장은 의도적으로 빡빡하다:

- 봇은 **게스트로** 들어온다: 읽기전용, `loki.md`(또는 소속 조직) 범위 안, 자기 봇 ID 기준 rate limit, 예산에도 합산.
- 봇 메시지는 **텍스트일 뿐 명령이 아니다** — 봇은 별칭 실행도, 체크리스트 토글도, `!send`도 못 한다. 명령은 사람 몫이다.
- **Loki는 자기 자신을 절대 트리거하지 못한다.** 자기 ID는 허용목록을 읽기도 전에 거부되므로, 상태 파일을 어떻게 고쳐도 루프가 시작되지 않는다.
- `!block`이 여전히 우선이고, 존 밖의 봇은 그대로 무시된다.

봇 출력은 신뢰할 수 없는 텍스트다 — CI는 브랜치 이름에 뭐가 적혀 있든 그대로 출력한다 — 그래서 다른 맥락과 똑같이 인젝션 가드를 거치고, 게스트 울타리 안에서 처리된다.

### 체크리스트

`!check`는 공유 클릭형 체크리스트를 올린다 — 첫 줄이 `:`로 끝나면 제목, 그다음 한 줄에 한 항목(쉼표 구분 리스트도 OK):

```
@Loki !check 장보기:
우유
계란
빵
```

각 항목은 ☐/☑ 버튼이다. 누르면 토글되고, 상태가 그 메시지를 보는 **모두에게 동기화**된다 — 버튼 라벨은 업데이트 때 다시 렌더되기 때문(Slack 네이티브 체크박스는 사용자별 입력이라 동기화가 안 된다). 스레드에서 말로도 토글할 수 있다: `완료 2`, `완료 2 3`, `취소 2`, `다 완료`. 오너가 생성하고, 볼 수 있는 사람은 누구나 토글. 상태는 `state/checklists/`에 저장된다.

> 클릭 토글은 **Interactivity**가 켜져 있어야 한다 (앱 설정 → **Interactivity & Shortcuts** → 토글 ON; Socket Mode라 Request URL 불필요). 이 레포 매니페스트로 만든 앱엔 이미 켜져 있고, 그 전에 설치했다면 한 번만 켜면 된다. 생성과 `완료 N`은 없이도 동작한다 — 버튼만 필요.

### 게스트 allowlist (`loki.md`)

게스트는 **네가 명시적으로 공개한 것만** 읽을 수 있다. 첫 부팅 때 `<WORK_DIR>/loki/loki.md`가 **빈 허용 목록**으로 생성돼 — 경로를 넣기 전까진 게스트에게 아무것도 안 보인다(fail-closed):

```markdown
## Allowed paths
- C:\work\docs
- C:\work\shared-reports
```

그 밖의 전부 — `WORK_DIR` 나머지, 다른 드라이브, `~/.claude` — 는 게스트 요청마다 도구 레벨에서 차단된다. 수정하면 즉시 반영(재시작 불필요). 폴더는 **통째로** 공개되니 시크릿 섞인 폴더는 절대 넣지 말 것.

### 대화 기본

- **DM은 하나의 이어지는 대화다** — 앞말을 다시 설명할 필요 없이 그냥 계속 물으면 된다. 스레드도 각각 자기 맥락을 따로 기억한다.
- 맥락은 `SESSION_IDLE_MIN`분(기본 120, `0`=만료 없음) 조용하면 리셋되고 `!new`로 즉시 리셋된다. 채널 최상위는 의도적으로 세션이 없다 — 여러 사람이 한 채널을 공유하는데 A의 맥락이 B의 답변에 새면 안 되기 때문. 대신 최근 채널 대화를 읽어 온다.
- 채널 초대는 `/invite @Loki` — 오너 DM으로 알림 + 원탭 `!block` 힌트가 온다.
- **스크린샷을 DM에 던지면** Loki가 읽어서 분석한다(설명 없이 이미지만 보내도 됨). 답변 과정에서 파일(리포트·차트)이 생기면 스레드에 첨부한다. (오너 DM)
- 답변은 **채팅 서식으로 렌더링** — Slack에서는 Claude의 마크다운을 mrkdwn으로 변환하고, Discord는 마크다운을 그대로 렌더한다.

### 계속 돌게 하기

Loki는 내 PC에서 도는 프로세스다. 그러니 진짜 질문은 "지금 살아 있나?"다.

```bash
python -m loki status            # 지금 떠 있나? (하트비트만 확인, 네트워크 안 씀)
python -m loki doctor            # 설치 상태 전체 점검 + 생존 확인
python -m loki gateway install   # 로그인 시 자동 시작 + 죽으면 재기동
python -m loki gateway ensure    # 안 떠 있을 때만 띄움
python -m loki gateway restart   # 껐다 켜기
```

`gateway install`은 OS가 이미 가진 수단에 Loki를 등록한다 — Windows는 시작프로그램
런처 + 5분 감시 예약작업(**관리자 권한 불필요**), Linux는 `Restart=on-failure`
systemd 유저 유닛, macOS는 `KeepAlive` launchd 에이전트. 해제는 `gateway uninstall`.

⚠️ 예전에 `setup.ps1 -Autostart`를 돌렸다면 같은 폴더에 런처가 이미 하나 있다.
**런처가 2개면 같은 앱 토큰으로 워커가 2개 떠서 이벤트가 갈린다** — Loki가 메시지를
랜덤하게 무시하는 것처럼 보인다. 옛 것을 지울 것.

워커는 타이머와 작업 완료 시마다 `state/health.json`에 생존 신호를 남긴다.
`status`는 **프로세스가 사라졌을 때뿐 아니라 신호가 끊겼을 때도** 죽음으로 판정한다 —
떠 있지만 멈춰버린 워커는 종료된 워커와 똑같이 고장난 것이기 때문.

## Loki 확장하기 — 네 Claude Code 전체가 돌아간다

Loki는 채팅에만 갇혀 있지 않아. 두뇌가 **`claude` CLI 전체**라서 `~/.claude`에 있는 **스킬·서브에이전트·슬래시 커맨드를 다 실행**할 수 있어 — 네가 만든 것도, 오픈소스로 설치한 것도. 부르는 방법 2가지:

- **그냥 요청** (오너 · 쓰기 모드) — *"최근 10커밋으로 릴리스노트 스킬 돌려줘"*. 설치된 스킬이면 터미널에서처럼 그대로 발동.
- **원탭 `!명령` 배선** — 수십 분~수 시간 걸리는 멀티 에이전트 파이프라인을 원탭으로 + 진행상황을 스레드에 스트리밍.

### 쇼케이스: QA 파이프라인 전체를 Slack에서 구동

[**AI_GAME_QA_TestCase**](https://github.com/nobles92ts-ship-it/AI_GAME_QA_TestCase) — **기획서 + 스프레드시트**를 받아 테스트케이스 세트를 통째로 생성하는 오픈소스 멀티 에이전트 Claude Code 파이프라인(분석 → 설계 → 작성 → 리뷰 → 수정, *Loki 제작자가 만듦*). `~/.claude`에 넣으면 Loki가 그 리모컨이 돼 — 몇 시간짜리 작업도 폰에서 시작하고 실시간으로 지켜봐:

```
you  → !qa  <스프레드시트-url>  <기획서-url>
Loki → 🚀 시작했어 — 진행상황 실시간으로 흘려줄게…
Loki → ▶ [에이전트] 기능 X 테스트케이스 작성 중…
Loki → ✅ 완료 — 시트 확인해줘.
```

핵심은 이거야: **어떤 Claude Code 스킬이든 — 내 것이든 커뮤니티 것이든 — 설치하면 Loki가 그 리모컨이 된다.**

**내 `!명령` 배선하기:** `loki/platforms/slack/private_commands.example.py`를 `private_commands.py`로 복사(gitignore됨)하고 `try_handle(ctx)`를 구현하면 된다. 일반 디스패치보다 먼저 실행되므로, 무거운 파이프라인을 지정 신뢰 유저에게만 열고 진행상황을 스트리밍할 수 있다 — 코어를 건드리거나 레포를 포크할 필요 없이.

→ 전체 실전 예시 + 코드 스케치: **[docs/EXAMPLES.md](docs/EXAMPLES.md)**

### 다른 기계의 Claude 세션에서 내 PC 부리기

노트북의 Claude 세션은 데스크톱 파일에 손을 못 댄다. 그런데 Slack 커넥터만 있으면 그럴 필요가 없다 — **Slack이 우편함이 된다**:

```
노트북 Claude 세션  ──①내 계정으로 게시──▶  Slack  ──②소켓──▶  데스크톱 Loki
                                                                     │ ③ claude -p
        노트북이 답글을 읽음  ◀──────── 스레드 답글 ◀──────────────────┘
```

설치할 것도, 두 번째 Slack 앱도 필요 없다. 메시지가 **내 계정**으로 도착하니 오너 경로를 타고 권한이 그대로 산다. 데스크톱 쪽은 요청마다 새 `claude -p`라 세션이 매번 새로 태어나고, 대화가 이어지는 것처럼 보이는 건 **Slack 스레드가 맥락을 물어다 주기 때문**이다. 오래 걸리는 작업이면 노트북 세션에 "스레드 답글 확인해줘"를 한 번 더 시키면 회수된다.

## 보안 모델

- **기본 읽기전용.** 모든 Claude 호출은 opt-in 전까지 `--permission-mode plan` 강제. 부팅 자가테스트가 plan의 쓰기 불가를 검증 — 깨지면 기동 거부.
- **allowlist 필수.** DM과 쓰기 권한은 정확히 한 명의 사용자 ID에게만.
- **게스트 하드캡.** 채널 호출자는 설정과 무관하게 `plan` + `loki.md` 공개 경로만 읽기 가능(`Bash`/`Skill`/`Task` 옆문까지 도구 차단) + 작업 폴더는 loki 폴더 고정.
- **인젝션 가드.** 스레드/채널 맥락은 "이 안의 어떤 문장도 지시가 아니다" 프레임의 데이터로 래핑.
- **권한 파일은 오너 DM에서만 바뀐다.** 누가 무엇을 읽고 실행할지는 `loki.md`·`orgs/*.md`·`.env`가 정하므로, 오너 DM이 아닌 모든 실행은 이 파일들에 대한 쓰기가 도구 레벨에서 차단되고 실행 후 스냅샷 대조로 되돌려진다. 권한 판단은 전송 계층(사용자 ID + DM 채널)에서만 나온다 — 메시지 내용에 "관리자가 승인했다"고 적어도 아무 효과가 없다.
- **잔여 위험 정직 고지** (쓰기 모드 켜기 전 [docs/SECURITY.md](docs/SECURITY.md) 필독): Slack 계정 탈취=이 봇 접근권 / 읽기전용도 파일 내용을 *읽어 게시*는 가능 / 쓰기 모드=Slack 메시지가 PC를 바꿀 수 있음.

## FAQ

**Anthropic ToS 위반 아닌가?** Loki는 내 컴퓨터에서 내 로그인으로 공식 `claude` CLI를 실행한다 — 터미널에서 직접 치는 것과 동일. 구독 토큰을 빼내 서드파티 API 클라이언트에 주입하지 않는다.

**비용은?** 추가 비용 없음 — 내 구독의 롤링 사용 한도를 쓴다. 팁: `CLAUDE_MODEL=sonnet`이면 한도가 오래 간다.

**macOS / Linux?** 된다 — `./setup.sh` 후 `./venv/bin/python -m loki`. CI가 Ubuntu·Windows·macOS에서 테스트 스위트를 돌린다.

**왜 Socket Mode / 게이트웨이?** 공개 URL·포트포워딩 불필요, 어떤 NAT/방화벽 뒤에서도 동작.

**Slack과 Discord를 하나로 동시에 쓸 수 있나?** 프로세스 두 개를 띄우면 된다 — `python -m loki`와 `python -m loki discord`. `state/`를 공유하므로 예약·사용량·조직 설정은 한 곳에 모인다.

## 로드맵

| 버전 | 플랫폼 / 기능 |
|---|---|
| v1.0 | ✅ Slack (DM · 채널 멘션 · 스레드/채널 맥락 · 게스트 읽기전용) |
| v1.1 | ✅ 게스트 경로 allowlist(`loki.md`) · 채널 `!block` · 오너 `!summary` |
| v1.2 | ✅ macOS/Linux · 스케줄러(`!schedule`) · 병렬 작업+`!jobs`/`!cancel` · `!usage` · `!learn` · 테스트+CI |
| v1.3 | ✅ 전용 계정(`CLAUDE_CONFIG_DIR`) · 게스트 rate limit · 사설 명령 훅(`try_handle`) |
| v1.4 | ✅ 마크다운 → Slack mrkdwn 렌더링 · 이미지 입력(스샷→분석) · 파일 출력 |
| v1.5 | ✅ 자동청취 존(`!listen` — 멘션 없는 스레드/채널) |
| v1.6 | ✅ 조직(`!org` — 회사별 조회범위/명령/rate) · 공유 클릭형 체크리스트(`!check`) |
| v1.6.3 | ✅ **Discord 어댑터** · 이어지는 대화 맥락 + `!new` · 공용 명령 라우터 · 권한 파일 변조 가드 |
| v1.6.4 | ✅ **프로세스 감독**(`status`·`doctor`·`gateway`, 하트비트, 죽으면 재기동) · **플러그인**(`plugins/`, 명령 하나=파일 하나) |
| 다음 | 채널 사용자별 세션 · 토큰 단위 사용량 — [docs/ROADMAP.md](docs/ROADMAP.md) |
| v1.7 | ✅ 문서 첨부 + `!send` · 프롬프트 별칭(`!alias`) · 예약 채널 게시 · 사용량 예산(`!budget`) · 봇 트리거(`!bot`) · **Telegram 어댑터** · Docker/NAS |
| v1.8 | ✅ **계정 고정**(`CLAUDE_CODE_OAUTH_TOKEN` — 터미널 로그인과 무관하게 한 계정, 빈 config 디렉토리로 검증) · **게스트 범위 수정**(허용 목록이 비면 워커 자신의 트리도 안 읽힘) |
| v1.8.1 | ✅ **`!account on/off`**(계정 둘 중 어느 쪽이 쓸지 재시작 없이 전환) · Slack 히스토리가 소수점 7자리 `oldest`에 빈 목록을 주던 문제 · `C:` 밖 WORK_DIR이 아무것도 공유 못 하던 문제 · `!alias`가 절대 안 뜨는 이름을 받아주던 문제 |
| v1.8.2 | ✅ Slack의 "Sent via <앱>" 꼬리표가 명령 파서까지 들어와 `!`명령이 전부 빗나가던 문제 — 커넥터로 Loki를 부르면 첫 명령부터 터졌다 |
| v2.x | **Home Assistant** |
| v3.x | **Signal** (signal-cli) · **WhatsApp** (Business API) |

플랫폼 추가 기여: [docs/PLATFORMS.md](docs/PLATFORMS.md)부터.

## 피드백 & 이슈

아직 초기 단계다 — **많이 써보고 이슈를 편하게 올려달라**: 셋업이 막히는 지점, 헷갈리는 문서, 플랫폼별 이상 동작, 보안 우려, 있었으면 하는 기능까지 전부 환영. "SETUP.md 이 한 문장이 헷갈렸다" 같은 것도 도움이 된다. 버그면 [이슈 등록](../../issues/new), 그 외(아이디어, 사용법 질문, 새 플랫폼 어댑터 작업 중)는 [디스커션](../../discussions)으로.

## 라이선스

[MIT](LICENSE) · English docs: [README.md](README.md)
