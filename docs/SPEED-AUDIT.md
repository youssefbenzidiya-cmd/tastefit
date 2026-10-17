# Speed audit, 2026-10-17

Live Cloud Run `/tenant` for a Wicker Park point (41.9088, -87.6796), rent 4000. Event kinds and timings only. No taste payloads were stored.

| ms | what arrived |
|---:|---|
| 6979 | `unit` |
| 19481 | three `step` events |
| 52561 | `profile` and the next steps |
| 77951 | `candidates` and the next steps |
| 90824 | `step` |
| 95497 | `step` |
| 111990 | `step` |
| 118029 | `step` |
| 122713 | `shortlist` |
| 137656 | `baseline` |
| 137666 | `memo` and `budget` |

The run finished in 138s, inside the 300s Cloud Run cut. Longest quiet stretches: 33.1s before `profile`, 25.4s before `candidates`, 16.5s, then 14.9s before `baseline`. The first event was `unit` at 7.0s. An earlier capture the same day saw about 31s before the first event and quiet stretches of 26–48s. This run was faster at the start, and its longest stretch was 33.1s.

A warm local process, with no Qloo call, spent 39ms importing, 0.4–0.5s naming the point, 17ms on the City Scan unit, and under 1ms on the market-share estimate. The City Scan is not the gap.

Tag and demographic reads already run together. Concept tag lookups already run together. The area rank and the city rank already run together (`PARALLEL` is 3). One rank call cannot be split without making the scores incomparable. The call budget stays 25. The run deadline stays 230s.

No safe change beats these gaps.
