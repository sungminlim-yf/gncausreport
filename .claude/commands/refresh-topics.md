# /refresh-topics — 사업 자료로 주제·독자 카드 갱신

입력(선택): 대상 채널(기본 `exec-team`).

`sungmindata/`의 살아있는 사업 자료를 근거로 **정기 주제(`topics.md`)와 독자 카드(`audience`)를 갱신**한다.
고정 주제 반복의 한계를 넘어, 기보고와 겹치지 않는 **유익한 새 주제**를 도출하는 것이 목적(D27).
`topic-curator` 스킬의 루브릭을 사용하고, **제안 → 사람 승인 → 적용** 순서를 지킨다(완전 자동 금지, D2 정신).

## 0. 준비
1. `sungmindata/`에 README 외 자료가 없으면: "자료를 먼저 올려달라"고 안내하고 **중단**.
2. run-id 생성: `<YYYY-MM-DD>_topics_<짧은난수>`, `runs/<run-id>/` 폴더 생성.

## 1. 입력 수집
- `sungmindata/`의 모든 파일을 읽는다(.md/.txt/.pdf/이미지). 큰 PDF는 핵심 위주로.
- `topics.md`(현행 주제), `archive/posted-index.md`(기보고), 필요 시 최근 `archive/*.md` 제목.
- `audience/profiles/<채널>.md` + `audience/briefs/<채널>.md`.

## 2. 분석·제안 (topic-curator 스킬)
`topic-curator` 루브릭으로 다음을 작성해 `runs/<run-id>/topic-proposal.md`에 기록:
- **주제 변경안**: 각 줄에 `ADD/REFINE/KEEP/DROP` 태그 + `<주제> | <채널> | <depth>` + 사유 + 근거자료(sungmindata).
  - 반드시 `posted-index`와 대조해 **이미 다룬 주제 반복 금지**(심화·후속 각도로 전환).
- **독자 카드 갱신안**: `audience/profiles/<채널>.md`에 반영할 우선순위·맥락·관심사·금기.
- 민감 자료 내용은 **외부 검색용 주제로 추상화**(원문 노출 금지).

## 3. 제안 제시 → 사람 승인
- 제안을 **요약 표(ADD/REFINE/DROP)와 사유**로 사용자에게 보여준다. 적용 전 **명시적 승인**을 받는다.
- 사용자가 일부만 채택/수정하면 반영.

## 4. 적용 (승인 후에만)
- `topics.md`: 승인된 ADD/REFINE/DROP를 반영(형식 `<주제> | <채널> | <depth>` 유지, 주석/구조 보존).
- `audience/profiles/<채널>.md`: 승인된 갱신 반영 → 이어서 **`/refresh-brief <채널>`**로 `briefs/` 재간추림.

## 5. 마무리
- 변경 요약 출력: 추가/재정의/폐기 건수, 갱신된 독자 카드 항목, 다음 정기 실행에 반영됨을 안내.
- (선택) 새 주제 중 하나를 바로 `/brief`로 시험 생성할지 제안.

> 정기화: 운영자가 sungmindata를 갱신할 때마다 수동 실행 권장. (원할 경우 스케줄러/슬랙 `/지엔씨 주제갱신`으로 확장 가능)
