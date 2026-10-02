# Extension 작성 가이드

- Core가 없어도 안 되는 기능만 Core.
- 그 외는 Extension.
- Core MUST NOT import Extension.
- Extension table은 Extension 소유.
- Core startup은 Extension schema에 의존하지 않음.
- write extension은 public Core port로만 변경.
- readonly extension은 write port 자체를 받지 않음.
- vendor 세션/모델/프로토콜은 adapter 내부.
- extension failure가 기본 communication을 멈추지 않음.
