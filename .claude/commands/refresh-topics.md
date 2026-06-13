# /refresh-topics — 사업 자료로 주제·독자 카드 갱신

입력(선택): `$ARGUMENTS`
- `--weekly --auto` : **다음 주 9개 주제를 자동 선정·확정**(사람 승인 생략) + 슬랙 통보. 토요일 자동 갱신 경로.
- `--replace-pending` : **아직 조사 안 된(pending) 슬롯만** 새 주제로 교체(이미 조사된 done 주제는 보존) + 슬랙 통보. 주중 보충 경로(슬랙 `/지엔씨 주제갱신`).
- (플래그 없음) : 기존 **제안 → 사람 승인 → 적용** 모드(수동 CLI 검토용).

`sungmindata/`의 살아있는 사업 자료를 근거로 **정기 주제(`topics.md`)와 독자 카드(`audience`)를 갱신**한다.
주제는 **주 9건**으로 운영하며, `Mon·Wed·Fri`에 **3건씩** 배정된다(요일·상태는 `scripts/topics_tool.py`가 관리).
`topic-curator` 스킬의 루브릭을 사용한다.

## 0. 준비
1. `sungmindata/`에 README 외 자료가 없으면: "자료를 먼저 올려달라"고 안내하고 **중단**.
2. run-id 생성: `<YYYY-MM-DD>_topics_<짧은난수>`, `runs/<run-id>/` 폴더 생성.

## 1. 입력 수집
- `sungmindata/`의 모든 파일을 읽는다(.md/.txt/.pdf/이미지). 큰 PDF는 핵심 위주로.
- `topics.md`(현행 계획표 — 요일·상태 포함), `archive/topics-history.md`(과거 주제), `archive/posted-index.md`(기보고), 필요 시 최근 `archive/*.md` 제목.
- `facts.md`(회사 사실/가정 — 주제가 사실과 어긋나지 않게 참고).
- `audience/profiles/<채널>.md` + `audience/briefs/<채널>.md`.

## 2. 분석·제안 (topic-curator 스킬)
`topic-curator` 루브릭으로 주제를 도출한다. **반드시 `posted-index`와 `topics-history`를 대조해 이미 다룬 주제 반복 금지**(심화·후속 각도로 전환).
- **`--weekly` 모드**: 정확히 **9개**를 도출(우선순위 순서대로 — 앞 3개=월, 다음 3개=수, 마지막 3개=금에 배정됨).
- **`--replace-pending` 모드**: 먼저 `topics.md`의 `pending` 개수(N)를 확인하고, **현재 `done` 주제 + posted-index + topics-history와 겹치지 않는 새 주제 N개**를 도출.
- **플래그 없음(수동)**: `runs/<run-id>/topic-proposal.md`에 `ADD/REFINE/KEEP/DROP` 태그로 제안만 작성.

각 주제 줄 형식(채널 기본 `exec-team`, depth: 단순 추적·동향=`shallow`, 핵심 진입주제=`medium`).

## 3. 적용
### (A) `--weekly --auto` (토요일 자동 — 사람 승인 생략)
> 보고서 게시 승인 게이트(D2)는 그대로라 안전망이 있으므로, **주제 선정만** 자동 확정한다.
1. 도출한 9개를 JSON으로 `runs/<run-id>/new-topics.json`에 기록:
   ```json
   {"week": "<다음주 ISO주차, 예 2026-W25>", "topics": [
     {"topic": "...", "channel": "exec-team", "depth": "medium"}, ... 9개 ]}
   ```
2. `python3 scripts/topics_tool.py apply-weekly runs/<run-id>/new-topics.json` 실행(전체 교체·3·3·3 배분·전부 pending, 기존 9건은 자동으로 `topics-history`로 이관).
3. 슬랙 통보: `python3 scripts/topics_tool.py notify-weekly` — "📅 다음 주 정기 주제" 요약(월·수·금 배분)과 **[📧 수신자에게 발송] 버튼**을 통보 채널에 게시한다. 버튼(action_id=email_topics)을 지정 승인자가 누르면 상시 봇이 현재 `topics.md`(다음 주 계획)를 수신자 메일 리스트로 안내 발송한다(승인자 전용).

### (B) `--replace-pending` (주중 보충 — 사람 승인 생략)
1. 도출한 N개를 JSON으로 `runs/<run-id>/new-topics.json`에 기록(`topics` 배열만, `week` 불필요).
2. `python3 scripts/topics_tool.py apply-replace-pending runs/<run-id>/new-topics.json` 실행(done 보존·pending만 교체, 빠진 pending은 `topics-history`로 이관).
3. 슬랙 통보: `python3 scripts/topics_tool.py notify "<메시지>"` — "🔄 남은 주제 N건 교체(이미 조사된 건 유지)" + 교체 결과.

### (C) 플래그 없음 (수동 — 사람 승인)
- 제안을 **요약 표(주차 9건 안 또는 교체안)와 사유**로 사용자에게 보여주고 **명시적 승인**을 받는다.
- 승인 시 위 (A)/(B)의 `topics_tool` 명령으로 적용(전체면 apply-weekly, 부분이면 apply-replace-pending).

## 4. 독자 카드(선택)
- sungmindata에서 독자 우선순위·맥락 변화가 드러나면 `audience/profiles/<채널>.md`에 반영 → `/refresh-brief <채널>`로 `briefs/` 재간추림. (자동 경로에서는 생략 가능)

## 5. 마무리
- 변경 요약 출력: 주차/요일 배분, 추가·교체·보존 건수, history 이관 건수, 슬랙 통보 여부. 다음 정기 실행(월·수·금)에 반영됨을 안내.

> 자동 경로(A·B)는 systemd 타이머(토요일) 또는 슬랙 `/지엔씨 주제갱신`이 헤드리스로 호출한다. 민감 자료(sungmindata) 내용은 **외부 검색용 주제로 추상화**(원문 노출 금지).
