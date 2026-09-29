<!--
Merge brief. Write for the person who approves the merge, not for someone reading the code.
Every line must help them decide to merge, or know what to watch after the merge.
If a reviewer can learn it from the diff, cut it. There is no How section.
Delete every section that has nothing real to say. A trivial PR is the headline plus the slot line.
Budget: about 150 words for low risk, 250 for medium, and 400 for high. At most 3 bullets per section.
-->

<!-- Headline: 1 to 3 sentences. What the system does after the merge, where its output goes, and why now. Link the issue. In a stack, say which part this is. -->

**Live effect:** <!-- none, or what changes for users, money, services, or alerts --> · **Risk:** <!-- low, medium, or high, with the triggers in parentheses: money path, keys or IAM, production config or infrastructure, stored data, hard to undo --> · **Ships:** <!-- on merge, next release, or after a manual step --> · **Blocks:** <!-- PRs that must merge after this one, or delete this slot -->

## Needs your call

<!-- Only if you need a human decision. @mention the person who must answer. -->

-

## Decisions

<!-- Only choices a teammate could dispute. "X, not Y, because Z." Link each to its lines in the diff. -->

-

## Risks

<!-- What can go wrong, how bad it is, and what limits it or which issue follows up. -->

-

## Proof

<!-- The strongest evidence that it works: the test that reproduces the bug, a staging check, the plan summary, a screenshot. Not a list of every test. -->

-
- **Not verified:** <!-- what nobody checked, human or agent -->

## Rollout

<!-- Only if live behavior changes or a manual step is needed. Give each step the signal to check. The last step is the rollback. -->

1.
