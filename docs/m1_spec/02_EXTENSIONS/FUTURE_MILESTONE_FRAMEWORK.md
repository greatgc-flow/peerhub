# Milestone / Optional Track 승격 기준

## Required Roadmap

```text
M1 Durable Communication
-> M2 Durable Work Continuity
-> M3 Federated Intelligent Collaboration
-> required roadmap complete
```

M2/M3 설계는 병렬 가능하지만 production promotion은 이전 milestone Exit Gate PASS 이후입니다.

## After M3

더 이상 순차적 `N차 milestone`을 만들지 않습니다. M4-A~L은 **Optional Capability Tracks**입니다.

각 후보는 다음을 다시 결정합니다.

```text
KEEP
MERGE
SPLIT
ABSORB
SUPERSEDE
RETIRE
```

Activation 평가:
- 실제 사용자 가치/빈도와 운영 evidence;
- 5 Whys 근본원인;
- M1~M3 기존 capability 조합으로 해결 가능한지;
- Core 변경 없이 Extension으로 격리 가능한지;
- 표준/OSS로 custom code를 줄일 수 있는지;
- 실패 격리/rollback/disable 가능성;
- provider specificity와 quota/cost;
- authority/security;
- 유지보수 부담.

**현재 구현이 많다는 이유, 과거 command가 존재했다는 이유, 기술적으로 가능하다는 이유만으로 우선순위를 올리지 않습니다.**
