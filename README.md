# AgentPulse

Claude Code가 컨텍스트 창을 채워 조용히 자동 압축(auto-compact)하기 전에
미리 경고하는, 외부 의존성 0개의 로컬 대시보드다.

## 무엇을 보여주는지 (스크린샷 대신)

- **실시간 세션 카드** — 현재 열린 세션들의 컨텍스트 점유를 토큰 수와 %로,
  5초 간격으로 갱신
- **경고 상태 배지** — `여유` → `주의`(70%) → `위험`(85%)로, 임계값을 넘으면
  터미널을 보고 있지 않아도 놓치지 않도록 Stop 훅이 macOS 알림도 띄움
- **기간별 집계**(7일/30일/전체) — 토큰 사용량, 자주 쓴 도구, MCP 서버·스킬
  사용 현황
- **압축 이력** — 트랜스크립트에 남은 `compact_boundary` 기록으로 실제 언제
  압축이 걸렸는지
- **기간 내 미사용 목록** — 설치는 돼 있지만 그 기간 동안 한 번도 안 쓴
  MCP 서버·스킬

## 실행법

```bash
python3 pulse.py
```

기본 포트는 `8787`(`~/.claude/pulse-config.json`의 `port`로 바꿀 수 있다).
첫 실행은 `~/.claude/projects/*/*.jsonl` 전체를 읽어 시간이 걸리고, 이후는
`pulse-cache.json`을 통해 새로 쓰인 줄만 읽는다.

## 설정 — `~/.claude/pulse-config.json`

파일이 없거나 키가 빠져도 아래 기본값으로 동작한다.

| 키 | 기본값 | 뜻 |
|---|---|---|
| `context_limit` | `null` (자동 추정) | 컨텍스트 창 크기(토큰). `null`이면 `settings.json`의 `model` 문자열에 `[1m]`이 있는지로 1,000,000 / 200,000을 추정 |
| `warn_threshold` | `0.85` | 이 비율을 넘으면 경고(위험) |
| `caution_threshold` | `0.70` | 이 비율을 넘으면 주의 |
| `active_window_minutes` | `15` | "실시간 세션"으로 볼 최근 활동 창(분) |
| `port` | `8787` | 대시보드 HTTP 포트 |

## 이 환경의 실측 근거 — 왜 85%인가

이 기기의 `~/.claude/settings.json` 모델 문자열에는 `[1m]`이 들어 있다
(`opus[1m]`). 그 결과 `pulse/config.py`의 `detect_context_limit`은 컨텍스트
한도를 200K가 아니라 **1,000,000 토큰**으로 잡는다.

"200K마다 압축한다"는 통설은 이 환경에 해당하지 않는다. 실제 트랜스크립트에
남은 `compact_boundary` 기록의 `preTokens`를 읽어보면 **967K, 969K, 967K** —
즉 압축은 200K가 아니라 약 970K 부근에서 걸린다. 1M 한도의 85% = 850K이며,
967K까지 약 12만 토큰(약 120K)의 여유를 두고 경고하도록 임계값을 잡은
것이 `warn_threshold: 0.85`다. 80%(800K)는 너무 이르고, 90%(900K)는 970K에
너무 붙는다.

한도가 자동 추정되지 않는(모델 문자열에 `[1m]`이 없는) 환경에서는 200K
기준으로 같은 85% 임계값이 적용된다 — 이 프로젝트가 검증한 실측은 1M 환경
기준이라는 점은 밝혀둔다.

## Stop 훅은 왜 트랜스크립트에서 직접 계산하는가

`hooks/context_alert.py`는 Claude Code의 실제 Stop 훅 payload를 캡처해
확인한 뒤 설계를 한 번 바꿨다. 캡처해보니 payload에는 `session_id`,
`transcript_path` 등은 있지만 **`context_window`가 없었다** — statusline에
넘어오는 payload와는 다른 모양이다(그쪽은 `context_window.used_percentage`,
`context_window_size`를 갖고 있고, `statusline.py`는 그 값을 그대로 쓴다).
만약 이 차이를 확인하지 않고 등록했다면, 훅은 `None`을 읽고 매 턴 조용히
아무 일도 안 했을 것이다 — 예외를 전부 삼키도록 설계돼 있어 테스트는
계속 초록불이었을 것이다.

그래서 Stop 훅은 `transcript_path`가 가리키는 JSONL을 직접 읽는다.
파일 끝에서 최대 512KB만 tail로 읽어 뒤에서부터 훑고, 서브에이전트
(`isSidechain`) 레코드는 건너뛰어 마지막 사용자 턴의 `assistant` 레코드를
찾은 뒤 `input_tokens + cache_read_input_tokens + cache_creation_input_tokens`를
더해 현재 점유를 얻는다. 실제 4.8MB 세션 트랜스크립트로 검증했을 때
578,612 토큰으로, 파일 전체를 스캔한 값과 정확히 일치했고 호출당 약 0.2ms였다.

## 기간 내 미사용이 세는 범위

"미사용" 집계(`pulse/inventory.py`)는 다음만 센다.

- `~/.claude/skills` 아래 스킬
- `~/.claude/plugins/cache` 아래 설치된 플러그인 스킬
- `~/.claude.json`의 `mcpServers`에 등록된 MCP 서버

**`~/.claude/modes` 아래 모드 번들은 제외한다.** 모드는 전환식이라 안 쓴
모드가 있는 게 당연하고, 전부 세면 실측 기준 314개가 나와 숫자 자체가
의미를 잃는다(ecc 번들 하나만으로도 수백 개가 늘어난다).

## 이 도구가 건드리는 파일

| 파일 | 위치 | 무엇 |
|---|---|---|
| `pulse-cache.json` | `~/.claude/` | 트랜스크립트 증분 읽기용 캐시(새로 쓰인 줄만 다시 읽기 위함) |
| `pulse-config.json` | `~/.claude/` | 위 설정값. 없으면 기본값 사용, 손으로 만들어도 됨 |
| `pulse-alerted.json` | `~/.claude/` | 세션당 한 번만 경고하기 위한 "이미 알림 보낸 세션" 기록 |
| `statusline.py` | `~/.claude/` | ctx% 표시에 `⚠` 경고와 남은 토큰 추정을 추가(Task 11에서 수정) |
| `settings.json` | `~/.claude/` | `hooks.Stop`에 `context_alert.py` 항목 추가(Task 11에서 수정, 기존 `afplay` 항목은 유지) |

`statusline.py`와 `settings.json`의 수정 직전 백업:

```
~/.claude/statusline.py.bak-pulse-<타임스탬프>
~/.claude/settings.json.bak-pulse-<타임스탬프>
```

이 저장소 밖의 두 파일과 그 백업은 git으로 추적하지 않는다.

## 원복 방법

1. `~/.claude/statusline.py`를 `~/.claude/statusline.py.bak-pulse-*` 중
   가장 최근 것으로 덮어쓴다.
2. `~/.claude/settings.json`도 마찬가지로 `.bak-pulse-*`로 되돌리거나,
   `hooks.Stop[0].hooks` 배열에서 `context_alert.py`를 가리키는 항목만
   지운다(기존 `afplay` 항목은 그대로 둔다).
3. 수정 후에는 항상 `python3 -c "import json; json.load(open('~/.claude/settings.json'))"`
   같은 명령으로 JSON이 깨지지 않았는지 확인한다.

## 테스트 실행

```bash
python3 -m unittest discover -s tests -q
```

현재 141개 테스트가 통과한다.
