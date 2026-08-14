# Phase 9 production import·승격 보고서

상태: bounded Phase 9 구현 및 외부 독립감사 APPROVE, 실제 production activation은 운영 gate 미충족으로 차단

## 1. 구현 범위

Phase 9는 repaired bundle을 현재 live store에 바로 쓰지 않는다. 다음 아홉 artifact를 서로 독립된
계약으로 둔다.

```text
ProductionImportInventoryManifest v1
ProductionBatchImportReceipt v1
ProductionRecordArtifactManifest v1
ProductionReleaseArtifactManifest v1
ProductionReleaseTransaction v1
ProductionReleaseConfigurationManifest v1
ProductionReleaseManifest v1
ProductionCurrentPointer v1
ProductionCompanyMemoryAttestation v1
```

`production build-inventory`는 sequential repair manifest 1,397행을 다시 읽고 READY_FOR_IMPORT 행의
source/repaired/quality-gate 파일을 실제 bytes로 SHA-256 검증한다. repair gate의 passed/status/hash,
deep audit, deterministic repair, isolated import, loss/count parity도 함께 검사한다. 결과는
content-addressed `runs/production_import/inventories/<inventory_id>`에 저장한다.

`production seal-inventory`는 검증된 inventory 전체를
`NSLAB_PRODUCTION_PROMOTION_HMAC_KEY`로 봉인한다. 발급 시각은 호출자가 주입할 수 없고, 동일 content ID를
재생성해도 기존 attestation을 지우거나 덮어쓸 수 없다.

## 2. batch import와 확장성

기존 단건 importer는 bundle마다 기존 record 전수 scan과 index rebuild를 수행했다. 606,737건에서는
제곱 비용이 되므로 batch staging은 다음 방식으로 분리했다.

```text
shared record identity index 1회 유지
bundle별 conflict/count/loss 검증
bundle별 index rebuild 0회
전체 import 완료 뒤 streaming fresh index rebuild 1회
```

streaming index는 SQLite 임시 정렬과 2,000행 batch를 사용해 record envelope 전체를 Python list로
materialize하지 않는다. legacy index와 records/full-envelope/generation root 및 by-record projection이
byte 의미상 동일한지 회귀로 검증한다.

실제 import는 `production stage-import --execute`에서만 시작한다. 작업은 짧은 `.work` 경로에서 수행하고
성공 후에만 canonical staging 경로로 원자 rename한다. 실패한 작업은 failure receipt만 남기며 canonical
stage나 live store, brain, memory pointer를 만들지 않는다. 동일 성공 stage 재실행은 deep inspection 후
기존 receipt를 반환한다. Bundle result는 저장된 episode envelope, original bundle, validation report,
record manifest에서 독립 재생성하며 import ID, item count, 모든 canonical artifact 경로도 다시 계산한다.
Record JSONL, record manifest, identity/index 파일은 별도 path/size/SHA ledger에 결속한다.

## 3. release와 rollback

staged project root 안에서 다음 조건이 모두 통과해야 release manifest를 만들 수 있다.

```text
batch import source 재투영
llm-full brain deep audit
production memory snapshot deep audit
sealed historical A~F shadow evaluation production_ready
doctor --production equivalent readiness
provenance audit
real LLM/embedding/web/price provider identity
```

release는 `production/releases/<release_id>/project`에 고정된다. 여러 live 디렉터리를 순차 교체하지 않고,
서명된 `production/current.json` 한 파일만 원자 교체한다. `load_settings()`는 이 pointer의 HMAC,
release manifest hash, activation history, canonical path와 production_ready를 확인한 뒤 release project root를
사용한다. `configs/`, `prompts/`, `schemas/`의 전체 file-hash ledger도 release ID와 active-root fast inspection에
결속한다. 별도 release artifact ledger는 episode 검증 metadata, brain, production memory/vector index,
warehouse와 선택된 shadow evidence의 exact path/size/SHA를 결속한다. Record artifact root와 release
artifact root는 모두 release ID 입력이며, memory current pointer는 canonical
`snapshots/<snapshot_id>/manifest.json`만 허용한다. Stage 이동 중 중단은 immutable release transaction으로 재개하며 partial receipt/manifest를
canonical artifact로 공개하지 않는다. 운영 secret은 release에 복사하지 않고 outer root `.env`를 명시적
dotenv source로 사용한다. 활성화 뒤 provider/model 또는 record artifact가 바뀌면 signed pointer resolution이
fail-close한다.
이전 release로의 rollback도 같은 단일 pointer 교체이며 모든 activation receipt는 content-addressed history로
보존한다.

활성 release의 일일 산출물은 봉인 대상과 분리한다. record-derived company memory는 release artifact로
봉인하고 runtime에서는 exact no-op 검증만 하며 다시 쓰지 않는다. 새 candidate company memory는 불변
run별 prediction artifact, release ID, known_at, payload hash를 운영 HMAC으로 한 파일 안에 결속한 뒤 원자
교체한다. warehouse의 predictions/company_memory/daily_outcomes와 post-close evaluation episode는 정상
가변 산출물로 취급하되, prompt에 들어가는 candidate memory는 attestation 검증을 통과한 파일만 사용한다.
봉인 시점 doctor 보고서의 SHA는 release ID에 포함하고, deep inspection은 당시 보고서의 ready 상태와 현재
readiness를 독립 검증하므로 정상 일일 count 변화가 과거 보고서 전체와 같아야 한다고 요구하지 않는다.
`analyze`와 `evaluate`는 signed pointer를 해석한 outer `Settings`를 그대로 사용해 release에 secret을 복사하지
않고도 provider/API 설정을 유지한다.

## 4. 실제 1,127 bundle read-only 검증

2026-08-14 현재 실제 repaired corpus를 양쪽 source/repaired bytes 약 32GB I/O로 다시 검사했다.

```text
inventory_id                         P9INV-974A99B55B152FF02040
source manifest entries             1,397
READY_FOR_IMPORT bundles            1,127
declared/import-ready records       606,737
training eligible records           384,846
semantic excluded records            11,486
finding count                             0
deep hash elapsed                    354.2s
```

결속 root:

```text
source manifest SHA-256  3ee9ec0f393fe5f76864142b1e25bfcf320be0564d07f66eb036b39798e34f42
source root SHA-256      21f15f7fe573ba89ae7cd3b9a35fd3997ecf6b75df32ea605cae8f7b7246d185
repaired root SHA-256    760cf649b91316ea6e8068652c6d14c63f200a64faae86e6ab6d0c8eed8aa78f
quality gate root        7ae298ccf621d180f4073ec18f0e873616d3c7916c0d91f907e03de129fe11e2
ready entries SHA-256    95aee84402dbc3ff5c06161043d388b42b46ae796b595702a420dcdd9dbac139
```

## 5. 현재 production readiness

Codex OAuth/CSV-only/local embedding bootstrap 이후 현재 root의 Phase 9 readiness는
`ready=false`이고 blocker는 5개다.

```text
current live record store                  968 / expected 606,737
production import inventory attestation    missing
current store vs import-ready inventory    mismatch
production batch import                    not staged
Phase 8 historical A~F production gate     not ready
active production release                  missing
```

Codex OAuth health, pinned local real embedding, stock-web price/research_daily, promotion/shadow HMAC,
CSV-only/disabled-web policy는 준비됐다. 세부 Phase 8 blocker에는 paired historical day 1/40,
catalog brain, production memory snapshot 부재가 포함된다. 따라서 inventory는 content 기준
`ready_for_import=true`지만 아직 attestation되지 않았고, stage import·llm-full build·activation은 실행하지 않았다.

```text
production import performed      false
production pointer changed       false
current live record count        968
```

이는 실패를 통과로 표시한 것이 아니다. 실제 provider/API key와 historical A~F 증거가 준비되면 같은
inventory를 운영 key로 봉인한 뒤 stage → build/audit → finalize → activate 순서로 진행해야 한다.

## 6. 테스트와 gate

추가 회귀:

```text
inventory content addressing과 HMAC 봉인
동일 inventory 재생성 시 attestation 보존
source/repaired/gate hash와 path traversal 거부
coherent entries/count rewrite 거부
streaming/legacy record index exact parity
격리 batch import와 live store zero-write
실패 workdir의 canonical stage 미노출과 retry
record DB/source tamper 재계산 거부
release finalize와 activation 분리
서명된 single-pointer root resolution
pointer tamper와 invalid release 거부
batch result coherent rewrite와 artifact relocation 거부
stage publication 및 release relocation 실패 주입 후 retry
release transaction과 configuration ledger coherent tamper 거부
activation history 누락과 동일 release 재활성화 거부
active release project를 기준으로 readiness 재계산
outer dotenv provider/shadow key 전달과 release 내 secret zero-copy
active provider drift 및 record/release-transitive artifact tamper 거부
memory current pointer manifest relocation 거부
```

현재 확인한 gate는 Phase 9 직접 회귀 22개와 company-memory/release lifecycle 회귀를 포함하며,
`ruff check .`, `mypy` 114 modules, schema parity, 전체 pytest 1,483개를 통과했다. 최종 전체 pytest
실행 시간은 311.0초다. 외부 독립감사는 최신 Phase 9 고정 tree를 `APPROVE`했다.

### 외부 독립감사

읽기 전용 재감사에서 batch import 재투영, release identity, canonical pointer, outer dotenv 전달,
정상 analyze/evaluate lifecycle, runtime company-memory attestation과 immutable/mutable artifact 경계를 반복
검증했다. 마지막 고정 tree 판정은 `APPROVE`이며 잔여 P0/P1 finding은 없다.
