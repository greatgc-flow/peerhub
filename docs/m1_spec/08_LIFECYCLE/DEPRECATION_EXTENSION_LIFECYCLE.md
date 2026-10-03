# Deprecation / Extension Lifecycle

## Extension 승격

기능 요청이 들어오면 즉시 Core에 넣지 않습니다.

```text
signal
→ repeated/validated need
→ Extension candidate
→ boundary + schema + tests
→ isolated implementation
→ operation evidence
→ M2+ milestone review
```

Core 승격은 다음 모두가 있어야 별도 의사결정합니다.

- 여러 use case에서 반복되는 필수성
- Extension으로 둘 때 구조적 중복/불변식 훼손이 명확
- failure semantics가 충분히 검증됨
- migration/cutover 영향 분석
- 기존 Core simplicity보다 이득이 큼

## Deprecation

```text
announce
→ observe usage
→ provide replacement/migration
→ warn
→ remove from advertised surface
→ remove implementation
→ retain historical evidence
```

갑작스러운 삭제보다 사용 증거를 확인합니다.

## 외부 provider/model/standard 변화

Changing fact는 Catalog를 갱신하고 procedure는 Skill을 갱신합니다.
Core schema 변경은 실제 boundary가 바뀔 때만 검토합니다.
