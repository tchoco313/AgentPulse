# AgentPulse — Claude Code 사용량 대시보드 설계

작성일: 2026-09-22

## 1. 배경과 목적

Claude Code는 컨텍스트 창이 차면 자동으로 압축(auto-compact)한다. 압축은
대화를 요약으로 갈아끼우므로, 진행 중이던 작업의 세부가 유실되고 같은 일을
다시 설명해야 하는 경우가 생긴다. 사용자는 이 시점을 **미리** 알고 싶어한다 —
정리하거나 새 세션으로 옮길 시간을 벌기 위해서다.

목적은 두 가지다.

1. **경고**: 컨텍스트 점유가 임계값을 넘으면 알린다.
2. **가시성**: 기간별 토큰·도구·MCP·스킬 사용량과 압축 이력을 한 화면에서 본다.

### 확인된 사실 (설계 전 실측)

사용자의 로컬 데이터를 읽기 전용으로 조사한 결과:

- 트랜스크립트: `~/.claude/projects/*/*.jsonl`, 112개 파일 / 388MB
- 사용자의 모델 설정은 `opus[1m]` — 컨텍스트 창이 200K가 아니라 **1M**이다
- 실제 `compact_boundary` 기록의 `preTokens`는 967K, 969K, 967K
  → 압축 임계점은 약 970K이며, 통설인 "200K마다 압축"은 이 환경에 해당하지 않는다
- `~/.claude/statusline.py`는 이미 `ctx %`를 표시한다. 다만 **임계값을 넘었다는
  사실을 알리는 장치는 없다**

이 실측이 임계값 선택(§4)의 근거다.

### 비목표

- 압축을 막거나 지연시키지 않는다. 이 도구는 **읽기 전용 관측**이며, 알림은
  사람이 판단할 시점을 알려줄 뿐이다.
- 요금제 사용 한도(5시간/주간)는 다루지 않는다. statusline이 이미 표시하며,
  트랜스크립트에서 계산할 수 있는 값이 아니다.
- 원격 접속·인증·다중 사용자를 지원하지 않는다. localhost 전용이다.

## 2. 성공 기준

- `python3 pulse.py` 한 줄로 뜬다. 외부 의존성 설치가 필요 없다.
- 388MB 전체 첫 스캔이 끝난 뒤, 이후 갱신은 새로 쓰인 줄만 읽는다.
- 실시간 세션 카드가 5초마다 갱신되고, 현재 컨텍스트 점유를 토큰 수와
  비율로 보여준다.
- 컨텍스트가 임계값을 넘으면 터미널을 보고 있지 않아도 알아챈다.
- 대시보드를 켜 두어도 Claude Code 동작에 영향이 없다.

## 3. 데이터 소스

| 소스 | 경로 | 쓰임 |
|---|---|---|
| 트랜스크립트 | `~/.claude/projects/*/*.jsonl` | 집계 전체의 원천 |
| 전역 설정 | `~/.claude/settings.json` | 모델 → 컨텍스트 한도 추정 |
| MCP 서버 목록 | `~/.claude.json`의 `mcpServers` | 미사용 항목 계산 |
| 스킬 목록 | `~/.claude/skills`, `~/.claude/plugins` | 미사용 항목 계산 |

### 트랜스크립트에서 쓰는 레코드

JSONL 한 줄이 한 레코드다. 쓰는 것만 적는다.

- `type: "assistant"` — `message.usage`(토큰), `message.model`,
  `message.content[]`의 `tool_use`(도구 이름과 입력), `timestamp`, `sessionId`, `cwd`
- `type: "system"`, `subtype: "compact_boundary"` — `compactMetadata`의
  `trigger`, `preTokens`, `postTokens`, `durationMs`
- `type: "ai-title"` — `aiTitle`(세션 카드 제목)
- `type: "user"` — `timestamp`(세션 활동 시각 보정)

`usage`에 실제로 존재하는 키(실측): `input_tokens`, `output_tokens`,
`cache_read_input_tokens`, `cache_creation_input_tokens`,
`output_tokens_details`, `server_tool_use`, `service_tier`, `cache_creation`,
`iterations`, `speed`, `inference_geo`.

## 4. 지표 정의

스크린샷의 지표 이름만으로는 계산식이 모호했다. 아래를 확정 정의로 삼는다.

**컨텍스트 한도**
`settings.json`의 `model` 문자열에 `[1m]`이 있으면 1,000,000, 없으면 200,000.
설정 파일로 덮어쓸 수 있다(§7).

**현재 컨텍스트 점유**
해당 세션의 **마지막** assistant 메시지의
`input_tokens + cache_read_input_tokens + cache_creation_input_tokens`.
출력 토큰은 다음 요청의 입력으로 들어가지만 마지막 메시지 시점에는 아직
반영되지 않으므로 더하지 않는다. 이 값은 추정치이며, 정확한 값은 Claude Code가
statusline에 주는 `context_window.used_percentage`다(§6이 그것을 쓴다).

**처리 토큰 (기간 합계)**
기간 내 모든 assistant `usage`의
`input + output + cache_read + cache_creation` 합. 캐시 읽기 비중을
별도로 표시한다 — 대부분이 캐시 읽기라 총합만 보면 오해하기 쉽다.

**도구 호출 수**
`tool_use` 블록 개수. 세 갈래로 분류한다.
- 이름이 `mcp__`로 시작 → **MCP**. 서버 이름은 `mcp__<서버>__<도구>`의 두 번째 조각
- 이름이 `Skill` → **Skill**. 스킬 이름은 `input.skill`
- 그 외 → **Tools** (Bash, Read, Edit, …)

**압축**
`compact_boundary` 이벤트 수. 1회당 버린 토큰은 `preTokens − postTokens`.
누적과 회당 평균, 그리고 `trigger`별(auto / manual) 분해를 표시한다.

**기간 내 미사용**
(설치된 MCP 서버 ∪ 설치된 스킬) − (기간 내 사용된 MCP 서버 ∪ 스킬).
"설치했지만 안 쓰는 것"을 드러내는 지표다.

**활동 세션**
트랜스크립트 파일의 마지막 레코드 `timestamp`가 최근 15분 이내인 세션.

## 5. 구조

```
~/Workspaces/13_AgentPulse/
  pulse.py              진입점 — 인자 파싱, 서버 기동
  pulse/
    config.py           설정 로드, 컨텍스트 한도 추정
    cache.py            캐시 파일 입출력, 스키마 버전
    scan.py             증분 스캔 → 정규화 이벤트
    aggregate.py        이벤트 → 집계 결과
    live.py             활동 세션 + 현재 점유
    inventory.py        설치된 MCP·스킬 목록
    server.py           http.server, 라우팅
  web/
    index.html
    app.js
    style.css
  hooks/
    context_alert.py    Stop 훅
  tests/
    fixtures/           소형 JSONL 픽스처
    test_scan.py
    test_aggregate.py
    test_live.py
    test_server.py
    test_context_alert.py
```

### 모듈 경계

각 모듈은 하나의 일만 하고, 인접 모듈의 내부를 모른다.

- `scan`은 디스크를 읽고 정규화된 이벤트 리스트를 낸다. 집계가 무엇인지 모른다.
- `aggregate`는 두 개의 순수 함수를 낸다. 파일 시스템을 모른다.
  - `fold(events) -> partial` : 한 파일분 이벤트를 파일별 부분집계로 접는다
  - `merge(partials, range) -> summary` : 부분집계들을 기간으로 걸러 합친다
  덧셈으로 합쳐지는 형태(일별 카운터, 도구별 카운터, 압축 이벤트 목록,
  세션 꼬리 정보)만 부분집계에 담는다 — 그래야 다시 읽지 않은 파일의 결과를
  캐시에서 꺼내 쓸 수 있다.
- `cache`는 읽기/쓰기와 스키마 검증만 한다. 무엇이 담기는지 모른다.
- `server`는 위를 조립하고 JSON으로 낸다. 계산하지 않는다.

`aggregate`가 파일을 모른다는 점이 중요하다 — 집계 로직 테스트가 388MB나
디스크 상태에 의존하지 않는다.

### 증분 스캔

캐시는 파일 경로마다 `{size, mtime, offset, partial}`을 기억한다. `partial`은
`aggregate.fold`가 낸 그 파일의 부분집계다. 새 줄을 읽으면 그 줄들만 `fold`해서
기존 `partial`에 더한다.

1. 파일 크기가 캐시의 `offset`과 같고 mtime도 같으면 → 건너뛴다
2. 크기가 `offset`보다 크면 → `offset`부터 열어 새 줄만 읽는다
3. 크기가 `offset`보다 **작으면** (파일이 교체·절단됨) → 그 파일만 전체 재스캔
4. 캐시 스키마 버전이 다르면 → 전체 재스캔

마지막 줄이 개행으로 끝나지 않으면 쓰이는 중일 수 있다. 그 줄은 버리고
`offset`을 그 줄 시작으로 물려, 다음 스캔에서 온전히 다시 읽는다.

### 데이터 흐름

```
*.jsonl ──scan(새 줄만)──> 이벤트 ──fold──> 파일별 partial ──> pulse-cache.json
                                                  │
                        건너뛴 파일의 partial ─────┤
                                                  v
                                               merge(기간)
                                                  │
                            inventory ────────────┤
                                                  v
                                   server ──JSON──> 브라우저(5초 폴링)
```

읽지 않은 파일도 캐시에 있는 `partial`이 `merge`에 그대로 들어간다. 이것이
388MB를 매번 파싱하지 않고도 전체 기간 집계를 낼 수 있는 이유다.

### API

| 경로 | 응답 |
|---|---|
| `GET /` | 대시보드 HTML |
| `GET /api/summary?range=7d\|30d\|all` | 기간 집계 — 카드 수치, 일별 시계열, 도구/MCP/스킬 순위, 미사용 목록 |
| `GET /api/live` | 활동 세션 배열 — 제목, 프로젝트, 모델, 마지막 활동 시각, 점유 토큰/비율/상태 |

`/api/live`는 가볍다(활동 세션의 꼬리만 읽음). `/api/summary`는 무거우므로
브라우저가 기간 탭을 바꿀 때만 부른다. 5초 폴링은 `/api/live`만 친다.

## 6. 경고

임계값 **85%** (1M 기준 850K). 근거: 실측 압축 지점이 967K이므로 80%(800K)는
너무 이르고, 85%면 약 12만 토큰 여유가 남아 정리하거나 세션을 옮길 시간이
충분하다. 설정으로 변경할 수 있다.

세 곳에서 알린다.

**statusline** (`~/.claude/statusline.py` 수정)
Claude Code가 stdin으로 주는 `context_window.used_percentage`를 이미 받고
있다. 이 값이 임계값을 넘으면 `ctx 87%` 대신 `⚠ ctx 87% (130K 남음)`처럼
경고 문구와 남은 토큰을 덧붙인다. 가장 정확한 값을 쓰는 경로다.

**macOS 배너** (`hooks/context_alert.py`, Stop 훅으로 등록)
훅도 같은 stdin JSON을 받는다. 임계값 초과 시 `osascript`로 알림 배너를
띄운다. **세션당 1회만** — 발화한 세션 ID를 `~/.claude/pulse-alerted.json`에
기록하고, 이미 있으면 조용히 끝낸다. 훅 실패가 Claude Code를 막으면 안 되므로
모든 예외를 삼키고 exit 0 한다.

**대시보드 카드**
세션 카드의 상태 배지가 `여유` → `주의`(70%) → `위험`(85%)로 바뀌고 바 색이
따라 바뀐다.

폰 푸시는 넣지 않는다. 압축은 자동으로 처리되는 일이라, 자리에 없을 때
알림을 받아도 할 수 있는 조치가 없다.

### 기존 파일 변경

`~/.claude/statusline.py`와 `~/.claude/settings.json`을 수정한다. 둘 다
수정 전에 타임스탬프를 붙여 백업한다. settings.json 변경은 Stop 훅 하나를
`hooks.Stop`에 **추가**하는 것이며, 기존 `afplay` 훅은 유지한다.

## 7. 설정

`~/.claude/pulse-config.json` (없으면 기본값):

```json
{
  "context_limit": null,
  "warn_threshold": 0.85,
  "caution_threshold": 0.70,
  "active_window_minutes": 15,
  "port": 8787
}
```

`context_limit`이 `null`이면 settings.json의 모델에서 추정한다.

## 8. 오류 처리

| 상황 | 처리 |
|---|---|
| JSON 파싱 실패한 줄 | 건너뛰고 개수만 집계, UI 하단에 표시 |
| 마지막 줄이 잘림 | 버리고 offset 물림, 다음 스캔에서 재시도 |
| 파일이 스캔 중 사라짐 | 캐시 항목 제거, 계속 진행 |
| 캐시 스키마 불일치·손상 | 전체 재스캔 |
| 포트 점유 | 다음 포트로, 실제 주소를 출력 |
| `~/.claude.json` 없음/파손 | 미사용 목록만 비우고 나머지는 정상 |
| 훅에서 예외 | 삼키고 exit 0 — Claude Code를 절대 막지 않음 |

서버는 `127.0.0.1`에만 바인딩한다. 트랜스크립트에는 대화 내용이 들어 있으므로
외부에 노출하지 않는다. API 응답에는 메시지 본문을 담지 않는다 — 수치와
세션 제목만 낸다.

## 9. 테스트

TDD로 구현한다. 각 모듈에 대해 테스트를 먼저 쓴다.

**`test_scan.py`** — 픽스처 JSONL로
- 정상 파일 파싱
- 깨진 JSON 줄이 섞였을 때 건너뛰고 개수를 보고
- 파일에 줄을 덧붙인 뒤 재스캔하면 새 줄만 읽음
- 파일이 줄어들면 전체 재스캔
- 마지막 줄이 개행 없이 끝나면 다음 스캔에서 온전히 읽힘

**`test_aggregate.py`** — 순수 함수, 파일 없음
- `fold`한 뒤 `merge`한 결과가, 같은 이벤트를 한 번에 처리한 결과와 같음
  (증분 경로와 전체 경로의 동치 — 증분 스캔이 옳다는 근거)
- 토큰 합계와 캐시 비중
- MCP / Skill / Tools 분류 (`mcp__dbhub__execute_sql` → MCP `dbhub`)
- `compact_boundary` → 횟수, 버린 토큰, trigger별 분해
- 기간 필터(7d / 30d / all)
- 미사용 목록 = 설치 − 사용

**`test_live.py`**
- 활동 창 안/밖 세션 구분
- 현재 점유 계산 (마지막 assistant usage 기준)
- assistant 메시지가 없는 세션 → 점유 미상으로 처리

**`test_server.py`** — 서버를 띄워 스모크
- `/api/summary`, `/api/live`가 200과 유효 JSON을 냄
- 없는 경로는 404
- 응답에 메시지 본문이 들어 있지 않음

**`test_context_alert.py`** — 가짜 stdin JSON
- 임계값 미만이면 조용
- 초과하면 발화하고 세션 ID 기록
- 같은 세션 두 번째 호출은 조용
- 입력이 깨져도 exit 0

실제 `~/.claude`를 건드리는 테스트는 없다. 모든 경로는 주입 가능하게 만든다.

## 10. 구현 순서

1. `config` + `cache` + `scan` (테스트 먼저)
2. `aggregate` (테스트 먼저)
3. `live` + `inventory`
4. `server` + API
5. `web/` 프론트엔드 — 차트 작업 전에 `dataviz` 스킬을 읽는다
6. `hooks/context_alert.py` + statusline 수정 + settings.json 훅 등록 (백업 후)
7. 실데이터로 전체 검증
