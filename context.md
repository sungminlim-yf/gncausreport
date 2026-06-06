## 문서의 목적

우리는 아래의 참조에 의거하여 자동화 설계 및 개발을 위한 구조를 만들고자 한자.

> **플랫폼 갱신 (2026-06-06)**: 게시·Q&A 플랫폼을 **카카오워크 → 슬랙(Slack)**으로 변경했습니다.
> 카카오워크가 호주에서 서비스되지 않아, 호주 사업 현지에서 쓸 수 있는 슬랙으로 전환합니다.
> 구현은 게시·승인·Q&A를 슬랙 Web API(`chat.postMessage`)로 일원화하고, 봇은 **Socket Mode**(공개 HTTPS 불필요),
> 승인은 **Block Kit 버튼**으로 처리합니다. 단일 진실 공급원(SSOT)은 [에이전틱_자동화_설계도.md](에이전틱_자동화_설계도.md) v3.

## 작업 내용

웹 포스트, 뉴스, 보고서, 논문, 발표 등을 검색하여 관련 주제를 검색
검색한 내용을 정리하고, 선별하여 고품질의 콘텐츠를 추려냄
추려낸 정보를 사실에 기반하여, 논리적으로 가공하여, 독자에게 유용한 정보로 재생산
가독성을 높이기 위해, 이해하기 쉬운 표현을 사용.
출처를 항상 밝힘으로써 신뢰도 제고
글을 보고 받는 독자와 정보의 관계성 적합성을 극대화 하기 위해서, 별도의 폴더안에 독자에 대한 정보를 보관.
또 다른 별도의 폴더 안에는 독자 정보를 간추린 문서를 보관, 관리 하여, 위의 업무 과정에서 항상 빠르고 효율적으로 참고 가능하게 끔 설정
최종적으로 가공 재생산된 정보를 슬랙의 지정된 채널에 포스트
가능하다면, 슬랙에 기사 관련 질문이 올라오면, 자동화 로직에 의해서 답변까지 가능하도록 개발
클로드 코드에서 추천하는 서브에이전트, 스킬을 적극 활용하여, 정보의 질을 높이는 쪽으로 개발

## 참조

3단계 자동화 워크플로우 아키텍처 설계 검토 완료3단계 자동화 워크플로우 아키텍처 설계 검토 완료슬랙 Q&A 봇(3단계)의 실현 방식을 정확히 하기 위해, 슬랙 봇이 사용자 메시지를 수신·응답할 수 있는지 확인했습니다(슬랙은 스레드·이벤트·Socket Mode로 1급 지원).슬랙 봇 이벤트 수신 확인 후 3단계 아키텍처 설계 구상슬랙 봇 이벤트 수신 확인 후 3단계 아키텍처 설계 구상좋습니다. 요구사항을 모두 반영해, 품질을 끌어올리는 데 초점을 둔 Claude Code 기반 구성을 3단계 로드맵으로 설계했습니다. 핵심 원칙은 "각 단계를 전담 서브에이전트로 분리하고, 규칙·양식·독자맥락은 스킬로 고정한다"입니다.
전체 아키텍처 (데이터 흐름)
주제 입력
└▶ [researcher 서브에이전트] 웹포스트·뉴스·보고서·논문·발표 검색/수집 (출처 URL 보존)
└▶ [curator 서브에이전트 + curate 스킬] 신뢰도·최신성·관련성으로 선별
└▶ [writer + content-production 스킬 + audience-fit 스킬] 사실기반·논리·가독성·출처 + 독자 맞춤
└▶ [reviewer 서브에이전트] 팩트체크·인용 검수 (품질 게이트)
└▶ [slack-post 스킬] 지정된 채널에 게시 (2단계+ 스테이징 초안→승인→본 게시)
└▶ (3단계) [슬랙 봇] 게시물 스레드에 올라온 질문에 자동 답변
요구사항담당 컴포넌트검색·수집(다양한 매체)researcher 서브에이전트 (WebSearch/WebFetch)선별·고품질 추출curator 서브에이전트 + curate 스킬(품질 루브릭)사실기반·논리·가독성·재생산writer + content-production 스킬출처 항상 명시researcher의 URL 보존 + 스킬 인용 규칙독자 정보 보관audience/profiles/ 폴더(전체)간추린 독자 정보(빠른 참조)audience/briefs/ 폴더 + audience-fit 스킬슬랙 게시slack-post 스킬(Web API, 1단계만 Webhook)질문 자동 답변슬랙 봇(Socket Mode)
리포지토리 구조
project/
├── .claude/
│ ├── agents/ # 서브에이전트
│ │ ├── researcher.md
│ │ ├── curator.md
│ │ └── reviewer.md
│ ├── skills/ # 스킬
│ │ ├── curate/SKILL.md
│ │ ├── content-production/SKILL.md
│ │ ├── audience-fit/SKILL.md
│ │ └── slack-post/
│ │ ├── SKILL.md
│ │ └── scripts/send.py
│ └── commands/brief.md # 진입 슬래시 명령 (/brief <주제> <대상채널>)
├── audience/
│ ├── profiles/ # ① 독자 전체 정보 (원본)
│ │ ├── exec-team.md
│ │ └── sales-team.md
│ └── briefs/ # ② 간추린 독자 카드 (빠른 참조용)
│ ├── exec-team.md
│ └── sales-team.md
├── archive/ # 게시한 보고+출처 보관 (3단계 답변 근거)
└── bot/ # 승인+3단계 Q&A 공용 슬랙 봇 (Socket Mode)
서브에이전트 (Claude Code 추천 활용)
서브에이전트는 격리된 컨텍스트에서 작업을 전담합니다. Claude Code에는 Explore(읽기 전용·빠른 검색), Plan, general-purpose 같은 내장 서브에이전트가 있고, 커스텀 서브에이전트는 .claude/agents/에 Markdown+YAML로 만들며 /agents 명령으로 생성합니다. Claude

researcher: WebSearch/WebFetch만 가진 읽기 전용. 검색 노이즈를 본 대화에서 분리. (내장 Explore의 읽기전용·격리 패턴을 웹 버전으로 적용)
curator: 모은 자료를 curate 스킬 루브릭으로 점수화·선별.
reviewer: 생성물을 팩트체크·인용 검증하는 "비평가" 역할 → 생성+검수 분리로 품질↑.
실행 계획은 내장 Plan 서브에이전트로 매 실행마다 단계 점검.

주의: 자동 위임(라우팅)은 완벽하지 않아 본 세션이 직접 처리해버리는 경우가 있으므로, 아래 /brief 명령에서 각 단계를 명시적으로 호출하세요. Kyle Redelinghuys
스킬 (Claude Code 추천 활용)
커스텀 스킬은 SKILL.md가 든 디렉터리로 만들며, Claude Code에서는 파일시스템 기반이라 업로드 없이 관련될 때 자동으로 발견·사용됩니다. 커스텀 명령은 스킬로 통합되어 .claude/commands/x.md와 .claude/skills/x/SKILL.md가 모두 /x로 동작하며, Claude Code에는 /code-review·/batch·/loop 같은 번들 스킬이 기본 포함됩니다. Claude API DocsClaude

curate: 선별 기준(출처 신뢰도, 1차 출처 우선, 최신성, 주제 적합성, 중복 제거).
content-production: 사실기반 종합, 논리 구조(제목→핵심요약 3줄→본문→📎출처), 쉬운 표현, 주장마다 [번호] 인용.
audience-fit: 대상 독자 카드를 읽어 톤·깊이·강조점을 맞춤(아래 폴더 설계 참조).
slack-post: 완성본을 지정 채널로 전송하는 scripts/send.py 번들(슬랙 Web API 일원화, 1단계만 Incoming Webhook).

빠른 제작: "skill-creator" 스킬이 워크플로를 물어보고 폴더 구조와 SKILL.md를 자동 생성해 주니 이걸로 스캐폴딩하세요. 다주제 일괄은 /batch, 품질 반복 개선은 /loop로 보강. 주의: description 필드가 스킬 호출의 핵심(최대 200자)이고, SKILL.md에 API 키 등 민감정보를 하드코딩하면 안 됩니다. ClaudeClaude
독자 정보 2폴더 설계 (요구사항 핵심)
독자-정보 적합성을 극대화하기 위해 원본과 요약을 분리합니다.

audience/profiles/ (전체): 부서·역할, 관심 주제, 의사결정 맥락, 금기사항, 선호 길이/톤 등 풍부한 원본. 사람이 관리·갱신.
audience/briefs/ (간추림): 각 독자/채널마다 5~10줄짜리 "독자 카드"(누구·무엇에 관심·어떤 깊이·어떤 톤·피해야 할 것). 매 실행 시 저렴하게 로드해 빠르게 참조.
audience-fit 스킬이 대상 채널에 해당하는 brief를 읽어 보고서를 맞춤화하고, profiles가 갱신되면 brief를 다시 간추리는 "유지보수" 단계(/refresh-brief)를 둡니다. (스킬의 progressive disclosure 원리와 동일 — 가벼운 요약을 먼저, 필요 시 원본을 참조)

오케스트레이션 & 자동 실행
/brief <주제> <대상채널> 한 명령으로 researcher→curator→writer(+audience-fit)→reviewer→slack-post를 순서대로 호출. 정기 실행은 헤드리스 + cron (claude -p "/brief 주제 exec-team") 또는 Claude Code 스케줄드 태스크.
3단계 로드맵
1단계 — 기본 뼈대 + 작동 테스트

산출물: 리포 구조, researcher(기본), 최소 content-production(출처 포함 기본 양식), slack-post(Incoming Webhook 스캐폴드), /brief 명령.
테스트: 한 주제 → 출처 포함 보고 → 테스트 채널에 1회 자동 게시 성공.
합격 기준: 단순 주제 한 건이 사람 개입 없이 채널까지 도달.

2단계 — 심도 검색·가공·공유 + 독자 적합성

산출물: researcher 강화(뉴스·보고서·논문·발표 등 다매체), curate(선별 루브릭), curator·reviewer 서브에이전트, content-production 고도화(논리·가독성·엄격 인용), audience/profiles+audience/briefs+audience-fit, /batch·/loop로 다주제·품질 반복, cron 정기화.
합격 기준: 여러 출처에서 선별된 고품질 보고가 독자 맞춤형으로 정기 자동 게시.

3단계 — 슬랙 질문 답변

슬랙 봇(Socket Mode)으로 구현합니다. 슬랙 봇은 스레드 메시지·멘션 이벤트를 WebSocket으로 수신하고 Web API로 회신할 수 있어, 공개 HTTPS 엔드포인트 없이도 동작합니다.
흐름: 슬랙 봇(bot/, Socket Mode)이 게시물 스레드(thread_ts)의 질문 이벤트 수신 → 즉시 ack() → archive/에 보관된 해당 기사+출처를 근거로(필요 시 researcher 재호출) 답변 생성 → chat.postMessage로 스레드에 비동기 회신.
주의: 이벤트는 3초 내 ack 후 비동기 처리, 스레드 바인딩으로 대상 글 특정, 토큰 기반 인증(Socket Mode는 공개 URL 서명 검증 불필요), 답변에도 출처 포함.
합격 기준: 게시된 기사 관련 질문에 출처 포함 답변 자동 회신.

주의사항

비밀정보(슬랙 Webhook URL·Bot Token xoxb·App Token xapp)는 전부 환경변수.
1단계는 "동작"이 목표 — 품질 고도화는 2단계에서.
승인(2단계)·Q&A(3단계) 봇은 cron이 아니라 상시 떠 있는 프로세스가 필요(Socket Mode WebSocket 연결). 단 공개 HTTPS·도메인은 불필요.
Q&A는 게시한 슬랙 채널·스레드 안에서 처리.
