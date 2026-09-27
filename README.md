# AgentPulse

Claude Code가 컨텍스트 창을 채워 조용히 자동 압축(auto-compact)하기 전에
미리 경고하는, 외부 의존성 0개의 로컬 대시보드다.

긴 작업 도중 자동 압축이 걸리면 앞선 맥락이 요약으로 줄어든다. 이 도구는
세션 트랜스크립트(`~/.claude/projects/**/*.jsonl`)를 직접 읽어 세션별
컨텍스트 점유를 재고, 임계값을 넘기 전에 대시보드·macOS 알림·statusline
세 경로로 알린다.

## 요구사항

- macOS (알림 배너에 `osascript` 사용. 대시보드와 집계는 다른 OS에서도 돈다)
- Python 3 표준 라이브러리만 사용 — `pip install` 없음 (Python 3.14에서 검증)
- Claude Code (트랜스크립트가 `~/.claude/projects/` 아래에 쌓이는 환경)

## 빠른 시작

```bash
git clone <이 저장소 주소> && cd AgentPulse
python3 install.py      # Stop 훅 등록(설정은 먼저 백업)
python3 pulse.py        # http://127.0.0.1:8787
```

## 무엇을 보여주는지 (스크린샷 대신)

- **실시간 세션 카드** — 현재 열린 세션들의 컨텍스트 점유를 토큰 수와 %로,
  5초 간격으로 갱신
- **경고 상태 배지** — `여유` → `주의`(70%) → `위험`(85%)로, 임계값을 넘으면
  터미널을 보고 있지 않아도 놓치지 않도록 Stop 훅이 macOS 알림도 띄움
- **statusline의 `⚠`** — 대시보드를 안 보고 있어도 터미널 한 줄에 경고가 뜬다.
  세 번째 경고 채널이고, `install.py`가 붙인다(`~/.claude/statusline.py`가
  이 저장소가 가정한 모양일 때만. 없거나 다르면 건너뛴다)
- **기간별 집계**(7일/30일/전체) — 토큰 사용량, 자주 쓴 도구, MCP 서버·스킬
  사용 현황
- **압축 이력** — 트랜스크립트에 남은 `compact_boundary` 기록으로 실제 언제
  압축이 걸렸는지
- **기간 내 미사용 목록** — 설치는 돼 있지만 그 기간 동안 한 번도 안 쓴
  MCP 서버·스킬

## 설치 — `install.py`

훅 등록과 statusline 패치는 손으로 하지 말고 이 스크립트로 한다. 이것이
지원하는 유일한 연결 방법이다.

```bash
python3 install.py      # Stop 훅 등록 + statusline ⚠ 패치
python3 uninstall.py    # 되돌리기 (= python3 install.py --uninstall)
```

`install.py`는

- `~/.claude/statusline.py`와 `~/.claude/settings.json`을 **둘 다 먼저**
  타임스탬프 백업한 뒤에 손댄다,
- Stop 훅을 **이 체크아웃의 경로로** 등록한다(`hooks.Stop[0].hooks`에 덧붙이며,
  기존 `afplay` 항목은 건드리지 않는다),
- statusline의 `⚠` 표시를 적용한다,
- 두 작업 모두 **여러 번 돌려도 안전하다** — 이미 돼 있으면 아무것도 안 한다,
- 쓰고 난 `settings.json`을 다시 읽어 JSON이 성한지와 있어야 할 훅이 전부
  남았는지 확인하고, 어긋나면 **백업을 복구하고 0이 아닌 코드로 끝낸다**,
- 무엇을 바꿨고 백업이 어디 있는지 찍는다.

`--claude-dir`로 대상 디렉터리를 바꿀 수 있다(테스트는 항상 임시 디렉터리를 준다).

## 실행법

```bash
python3 pulse.py
```

기본 포트는 `8787`(`~/.claude/pulse-config.json`의 `port`로 바꿀 수 있다).
첫 실행은 `~/.claude/projects/` **아래 모든** `*.jsonl`을 재귀로 읽어
시간이 걸리고, 이후는 `pulse-cache.json`을 통해 새로 쓰인 줄만 읽는다.
`projects/*/*.jsonl`처럼 2단계 glob으로 찾으면 안 된다 — 서브에이전트
트랜스크립트는 `<프로젝트>/<세션>/subagents/` 아래, 한 단계 더 깊은 곳에
있어서 절반 가까이를 놓친다(`scan.find_transcripts`가 `rglob`을 쓰는 이유이고,
이 프로젝트가 이미 한 번 고친 버그다).

## 설정 — `~/.claude/pulse-config.json`

파일이 없거나 키가 빠져도 아래 기본값으로 동작한다.

| 키 | 기본값 | 뜻 |
|---|---|---|
| `context_limit` | `null` (자동 추정) | **모델을 모를 때의** 컨텍스트 창 크기(토큰). `null`이면 `settings.json`의 `model` 문자열에 `[1m]`이 있는지로 1,000,000 / 200,000을 추정 |
| `warn_threshold` | `0.85` | 이 비율을 넘으면 경고(위험) |
| `caution_threshold` | `0.70` | 이 비율을 넘으면 주의 |
| `active_window_minutes` | `15` | "실시간 세션"으로 볼 최근 활동 창(분) |
| `port` | `8787` | 대시보드 HTTP 포트 |

세션이 자기 모델을 알려주면(트랜스크립트의 `model`) 그 모델의 창으로 잰다 —
`pulse/config.py`의 `MODEL_LIMITS`. `context_limit`은 그 표에 없는 모델일
때만 쓰인다. `claude --model ...`은 `settings.json`을 다시 쓰지 않으므로,
`settings.json` 하나로 모든 세션을 재면 실제 69%인 세션이 카드에 14%
"여유"로 뜨고 훅도 조용하다 — 이 도구가 막으려는 바로 그 상황이다.

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

MCP 서버는 `~/.claude.json`의 **최상위 `mcpServers`만** 센다. 그래서
"설치된 MCP" 목록은 실제로 쓰이는 MCP보다 짧다 — 이 기기에서는 최상위에
`dbhub` 하나뿐인데, 트랜스크립트에는 5개(`dbhub`, `claude-in-chrome`,
`claude_ai_Claude_Docs`, `claude_ai_Google_Drive`,
`plugin_ecc_chrome-devtools`)가 나타난다. 나머지는 플러그인과 claude.ai
연결이 붙여주는 것이라 이 파일에 없다. 즉 이 타일은 "설치된 MCP 전부"가
아니라 "`~/.claude.json`에 직접 적힌 MCP 중 안 쓴 것"이다.

**`~/.claude/modes` 아래 모드 번들은 제외한다.** 모드는 전환식이라 안 쓴
모드가 있는 게 당연하고, 전부 세면 실측 기준 314개가 나와 숫자 자체가
의미를 잃는다(ecc 번들 하나만으로도 수백 개가 늘어난다).

## 이 도구가 건드리는 파일

| 파일 | 위치 | 무엇 |
|---|---|---|
| `pulse-cache.json` | `~/.claude/` | 트랜스크립트 증분 읽기용 캐시(새로 쓰인 줄만 다시 읽기 위함) |
| `pulse-config.json` | `~/.claude/` | 위 설정값. 없으면 기본값 사용, 손으로 만들어도 됨 |
| `pulse-alerted.json` | `~/.claude/` | **한 번 찬 컨텍스트당** 한 번만 경고하기 위한 기록. 점유가 `caution_threshold` 아래로 떨어지면(= 압축이 걸리면) 항목이 지워져 다시 장전된다 |
| `statusline.py` | `~/.claude/` | ctx% 표시에 `⚠` 경고와 남은 토큰 추정을 추가(`install.py`가 적용) |
| `settings.json` | `~/.claude/` | `hooks.Stop`에 `context_alert.py` 항목 추가(`install.py`가 적용, 기존 `afplay` 항목은 유지) |

## 이 도구가 HTTP로 내보내는 것

대시보드는 `127.0.0.1`에만 묶이고, 그 위에 `Host` 헤더가 로컬이 아닌 요청은
403으로 막는다(DNS 리바인딩 차단 — 127.0.0.1로 재해석되는 남의 페이지는
브라우저에게 같은 출처가 된다). 그래도 프로세스 밖으로 나가는 것이 무엇인지는
적어둔다. `/api/live`와 `/api/summary`가 내보내는 것은 이게 전부다.

| 항목 | 예 |
|---|---|
| 세션 id | `a1b2c3d4-…` |
| 세션 제목 | Claude가 붙인 대화 제목 — **대화 내용에서 나온 문장이다** |
| 프로젝트 디렉터리 이름 | `cwd`의 마지막 조각(`13_AgentPulse`), 전체 경로는 아님 |
| 모델 | `claude-opus-5` |
| 토큰 수·도구 호출 수·압축 기록 | 숫자와 도구 이름뿐 |

대화 본문, 도구 인자, 파일 경로 전체는 나가지 않는다.

`statusline.py`와 `settings.json`의 수정 직전 백업:

```
~/.claude/statusline.py.bak-pulse-<타임스탬프>
~/.claude/settings.json.bak-pulse-<타임스탬프>
```

이 저장소 밖의 두 파일과 그 백업은 git으로 추적하지 않는다.

## 원복 방법

```bash
python3 uninstall.py
```

훅 등록을 빼고 statusline의 `⚠` 패치를 되돌린다. 기존 `afplay` 항목은
그대로 둔다. 손으로 해야 한다면:

1. `~/.claude/settings.json`의 `hooks.Stop[0].hooks` 배열에서
   `context_alert.py`를 가리키는 항목만 지운다(`afplay` 항목은 그대로).
2. statusline의 `⚠`는 `install.py`의 `STATUSLINE_PATCHED` 블록을
   `STATUSLINE_ORIGINAL`로 되돌리면 된다.
   **`~/.claude/statusline.py`를 `.bak-pulse-*` 백업으로 통째로 덮어쓰면
   `⚠` 표시도 같이 사라진다** — 그건 패치 전 파일이기 때문이다. 다시 붙이려면
   `python3 install.py`를 한 번 더 돌리면 된다(이미 돼 있으면 아무것도 안 한다).
3. 수정 후에는 JSON이 깨지지 않았는지 확인한다:

   ```bash
   python3 -m json.tool ~/.claude/settings.json > /dev/null && echo OK
   ```

   `python3 -c "... open('~/.claude/settings.json')"`은 쓰지 마라. 파이썬의
   `open()`은 `~`를 풀지 않아서 **항상** `FileNotFoundError`가 난다 —
   설정을 망가뜨렸다고 생각하는 사람에게 복구 절차가 또 하나의 에러를
   던지는 꼴이다. 위처럼 셸이 `~`를 풀게 하거나,
   `pathlib.Path(...).expanduser()`를 써라.

## 테스트 실행

```bash
python3 -m unittest discover -s tests -q
```

현재 207개 테스트가 통과한다. 경고도 트레이스백도 찍히지 않아야 정상이다.
